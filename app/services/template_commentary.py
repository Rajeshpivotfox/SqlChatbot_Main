import re
from decimal import Decimal
from app.services.query_executor import QueryResult

_CURRENCY_KEYWORDS = {"value", "amount", "revenue", "cost", "price", "total", "sum",
                       "balance", "budget", "actual", "rate", "salary", "income",
                       "profit", "loss", "expense", "liability", "asset", "equity"}


def _is_numeric(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _fmt_number(v) -> str:
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and v != int(v):
        return f"{v:,.2f}"
    return f"{int(v):,}"


def _fmt_scale(v: float) -> str:
    """Return a human-readable scaled number, e.g. 34.87B, 1.23M, 450K."""
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1_000_000_000:
        return f"{sign}{abs_v / 1_000_000_000:.2f}B"
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:.2f}M"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:.2f}K"
    return _fmt_number(v)


def _is_currency_col(col_name: str) -> bool:
    return any(k in col_name.lower() for k in _CURRENCY_KEYWORDS)


def _single_value_explanation(label: str, val: float) -> str:
    """Generate a 1-2 sentence explanation for a single KPI value."""
    label_lower = label.lower()
    scaled = _fmt_scale(val)
    formatted = _fmt_number(val)
    negative = val < 0
    zero = val == 0

    # Negative-value insight
    if negative:
        if any(k in label_lower for k in ("revenue", "income", "profit", "sales")):
            note = (f"The negative figure ({scaled}) suggests contra-revenue entries, "
                    "returns, or net adjustments are included — consider reviewing "
                    "the individual account breakdown.")
        elif any(k in label_lower for k in ("cost", "expense", "loss")):
            note = (f"A negative cost/expense total ({scaled}) typically indicates "
                    "reversals or credits exceeding the gross amount.")
        else:
            note = (f"The result is negative ({scaled}), which may indicate offsetting "
                    "entries or credits that exceed debits in this category.")
    elif zero:
        note = "The result is zero — no matching transactions were found for this category."
    else:
        if any(k in label_lower for k in ("count", "total_count", "num", "number")):
            note = f"There are **{formatted}** records matching this query."
        else:
            note = f"The total amounts to **{scaled}** across all matching accounts."

    return f"**{label}:** {formatted}\n{note}"


def _has_keyword(sql: str, *keywords: str) -> bool:
    return any(re.search(rf'\b{kw}\b', sql, re.IGNORECASE) for kw in keywords)


class TemplateCommentary:
    """Fast deterministic commentary using pattern-matched templates. Returns None
    when the result shape is too complex, letting the caller fall back to LLM."""

    def generate(self, question: str, sql: str, result: QueryResult) -> str | None:
        if not result.rows:
            return None

        rows = result.rows
        cols = result.columns
        n_rows = result.total_rows
        page_rows = len(rows)
        n_cols = len(cols)

        # Identify numeric vs string columns (based on first row)
        numeric_cols = [c for c in cols if _is_numeric(rows[0].get(c["name"]))]
        string_cols = [c for c in cols if c not in numeric_cols]

        # ── Single value (COUNT / SUM / single KPI) ───────────────────────────
        if page_rows == 1 and n_cols == 1:
            col = cols[0]["name"]
            val = rows[0][col]
            if _is_numeric(val):
                label = col.replace("_", " ").title()
                return _single_value_explanation(label, float(val))
            return f"**{col.replace('_', ' ').title()}:** {val}"

        # ── Single row, multiple columns (KPI summary row) ────────────────────
        if page_rows == 1 and n_cols <= 8:
            parts = []
            explanations = []
            for c in cols:
                v = rows[0][c["name"]]
                label = c["name"].replace("_", " ").title()
                if _is_numeric(v):
                    parts.append(f"**{label}:** {_fmt_number(v)}")
                    # Add a scaled note for large numbers
                    fv = float(v)
                    if abs(fv) >= 1_000:
                        explanations.append(f"{label}: {_fmt_scale(fv)}")
                elif v is not None:
                    parts.append(f"**{label}:** {v}")
            summary = "  •  ".join(parts)
            if explanations:
                summary += "\n" + "  |  ".join(explanations)
            return summary if parts else None

        # ── GROUP BY aggregate (multi-row with numeric column) ────────────────
        if _has_keyword(sql, "GROUP BY") and numeric_cols and page_rows >= 2:
            label_col = string_cols[0]["name"] if string_cols else cols[0]["name"]
            value_col = numeric_cols[0]["name"]
            try:
                values = [
                    float(rows[i][value_col])
                    for i in range(page_rows)
                    if _is_numeric(rows[i].get(value_col))
                ]
                total = sum(values)
                top_label = rows[0].get(label_col, "—")
                top_val = rows[0].get(value_col)
                val_label = value_col.replace("_", " ").title()
                top_str = (f"**{top_label}** leads with {_fmt_scale(float(top_val))}"
                           if _is_numeric(top_val) else "")
                note = f"{top_str}." if top_str else ""
                return (
                    f"**{n_rows:,} groups** found. "
                    f"Combined {val_label}: **{_fmt_scale(total)}**. {note}"
                ).strip()
            except (TypeError, ValueError):
                pass

        # ── TOP N listing ─────────────────────────────────────────────────────
        if _has_keyword(sql, "TOP") and numeric_cols and page_rows <= 50:
            label_col = string_cols[0]["name"] if string_cols else cols[0]["name"]
            value_col = numeric_cols[0]["name"]
            try:
                items = [
                    f"{rows[i][label_col]} ({_fmt_scale(float(rows[i][value_col]))})"
                    for i in range(min(3, page_rows))
                    if _is_numeric(rows[i].get(value_col))
                ]
                suffix = ", ..." if page_rows > 3 else ""
                val_label = value_col.replace("_", " ").title()
                return (
                    f"**Top {page_rows} by {val_label}:** {', '.join(items)}{suffix}. "
                    f"Results are sorted highest to lowest."
                )
            except (KeyError, TypeError):
                pass

        # ── Multi-row generic fallback ────────────────────────────────────────
        if n_rows > 0:
            return f"**{n_rows:,} records** returned."

        return None
