import re
from decimal import Decimal
from app.services.query_executor import QueryResult

_CURRENCY_KEYWORDS = {"value", "amount", "revenue", "cost", "price", "total", "sum",
                       "balance", "budget", "actual", "rate", "salary", "income",
                       "profit", "loss", "expense"}


def _is_numeric(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _fmt_number(v) -> str:
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and v != int(v):
        return f"{v:,.2f}"
    return f"{int(v):,}"


def _is_currency_col(col_name: str) -> bool:
    return any(k in col_name.lower() for k in _CURRENCY_KEYWORDS)


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
                return f"**{label}:** {_fmt_number(val)}"
            return f"**{col.replace('_', ' ').title()}:** {val}"

        # ── Single row, multiple columns (KPI summary row) ────────────────────
        if page_rows == 1 and n_cols <= 8:
            parts = []
            for c in cols:
                v = rows[0][c["name"]]
                label = c["name"].replace("_", " ").title()
                if _is_numeric(v):
                    parts.append(f"**{label}:** {_fmt_number(v)}")
                elif v is not None:
                    parts.append(f"**{label}:** {v}")
            return "  •  ".join(parts) if parts else None

        # ── GROUP BY aggregate (multi-row with numeric column) ────────────────
        if _has_keyword(sql, "GROUP BY") and numeric_cols and page_rows >= 2:
            label_col = string_cols[0]["name"] if string_cols else cols[0]["name"]
            value_col = numeric_cols[0]["name"]
            try:
                total = sum(
                    float(rows[i][value_col])
                    for i in range(page_rows)
                    if _is_numeric(rows[i].get(value_col))
                )
                top_label = rows[0].get(label_col, "—")
                top_val = rows[0].get(value_col)
                val_label = value_col.replace("_", " ").title()
                top_str = f" — Top: **{top_label}** ({_fmt_number(top_val)})" if _is_numeric(top_val) else ""
                return (
                    f"**{n_rows:,} groups** found.{top_str}  "
                    f"Total {val_label}: **{_fmt_number(total)}**."
                )
            except (TypeError, ValueError):
                pass

        # ── TOP N listing ─────────────────────────────────────────────────────
        if _has_keyword(sql, "TOP") and numeric_cols and page_rows <= 50:
            label_col = string_cols[0]["name"] if string_cols else cols[0]["name"]
            value_col = numeric_cols[0]["name"]
            try:
                items = [
                    f"{rows[i][label_col]} ({_fmt_number(rows[i][value_col])})"
                    for i in range(min(3, page_rows))
                    if _is_numeric(rows[i].get(value_col))
                ]
                suffix = ", ..." if page_rows > 3 else ""
                return f"**Top {page_rows}:** {', '.join(items)}{suffix}"
            except (KeyError, TypeError):
                pass

        # ── Multi-row generic fallback ────────────────────────────────────────
        if n_rows > 0:
            return f"**{n_rows:,} records** returned."

        return None
