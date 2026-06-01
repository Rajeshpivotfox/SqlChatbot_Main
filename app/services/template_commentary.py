# ──────────────────────────────────────────────────────────────────────────────
# TEMPLATE COMMENTARY — deterministic insights that cost 0 tokens.
#
# This module handles ~60-70% of queries without any Claude API call by
# pattern-matching the result shape:
#   1. Single value (COUNT/SUM/KPI)  → "Total Transactions: 12,345"
#   2. Single row, multiple columns  → "Revenue: 1.2M  |  Count: 45K"
#   3. GROUP BY aggregate            → "10 groups. Combined Value: 34.87B"
#   4. TOP N listing                 → "Top 5 by Value: Entity1 (1.2M), ..."
#   5. Generic multi-row             → "1,234 records returned."
#
# Returns None when the shape is too complex → CommentaryGenerator falls back
# to Claude API call #2. Adding more templates here directly reduces API costs.
#
# TO ADD A NEW TEMPLATE: add a new elif block in generate() that checks the
# result shape (n_rows, n_cols, SQL keywords) and returns a formatted string.
# Return None to fall through to the next template or ultimately to the LLM.
# ──────────────────────────────────────────────────────────────────────────────

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


# ── Follow-up question generation ────────────────────────────────────────────
# These are context-aware suggestions appended to every commentary.
# They help users explore the data further without needing to think of queries.

# Keywords detected in the question to determine the topic
_TOPIC_KEYWORDS = {
    "revenue":   {"category": "Revenue",   "opposites": ["expenses", "liabilities"],
                  "drilldowns": ["by legal entity", "by account", "by month"]},
    "expense":   {"category": "Expense",   "opposites": ["revenue", "assets"],
                  "drilldowns": ["by legal entity", "by account", "by month"]},
    "liability": {"category": "Liability", "opposites": ["assets", "equity"],
                  "drilldowns": ["by legal entity", "by account"]},
    "asset":     {"category": "Asset",     "opposites": ["liabilities", "equity"],
                  "drilldowns": ["by legal entity", "by account"]},
    "equity":    {"category": "Equity",    "opposites": ["assets", "liabilities"],
                  "drilldowns": ["by legal entity", "by account"]},
}


def _suggest_followups(question: str, sql: str, rows: list[dict],
                       cols: list[dict], result_type: str) -> list[str]:
    """Generate 2-4 context-aware follow-up question suggestions.
    result_type: 'single_value', 'single_row', 'comparison', 'top_n', 'generic'"""
    q_lower = question.lower()
    suggestions = []

    # ── Detect what the user was asking about ────────────────────────────────
    # Topic: revenue, expense, liability, asset, equity
    detected_topic = None
    for keyword, info in _TOPIC_KEYWORDS.items():
        if keyword in q_lower:
            detected_topic = info
            break

    # Entity: which legal entity was mentioned
    detected_entity = None
    for entity_kw in ("demo1", "demo2", "demo3", "demo konzern",
                      "konsolidierungsmandant", "teilkonzern"):
        if entity_kw in q_lower:
            detected_entity = entity_kw.upper()
            break

    # Time: which year/period was mentioned
    detected_years = re.findall(r'\b(20\d{2})\b', question)
    has_year_filter = bool(detected_years)

    # Columns in result: helps suggest drill-downs on available dimensions
    col_names = {c["name"].lower() for c in cols}
    has_entity = "legal_entity_name" in col_names or _has_keyword(sql, "legal_entity")
    has_year = "year" in col_names or _has_keyword(sql, "year")
    has_month = "full_month" in col_names or "short_month" in col_names
    has_tag = _has_keyword(sql, "tag")

    # ── Generate suggestions based on result type + context ──────────────────

    if result_type == "single_value":
        # User got one number — suggest breakdowns and comparisons
        if not has_entity:
            suggestions.append("Show this broken down by legal entity")
        if not has_year and not has_year_filter:
            suggestions.append("Compare this across all years")
        elif has_year_filter and len(detected_years) == 1:
            other_year = "2024" if detected_years[0] != "2024" else "2025"
            suggestions.append(f"Compare this with {other_year}")
        if not has_month:
            suggestions.append("Show this by month")
        if detected_topic and detected_topic["opposites"]:
            opposite = detected_topic["opposites"][0]
            suggestions.append(f"What is the total {opposite}?")

    elif result_type == "single_row":
        # User got one row with multiple metrics — suggest expanding
        if not has_entity:
            suggestions.append("Break this down by legal entity")
        if not has_year:
            suggestions.append("Show this for each year")
        if detected_entity:
            suggestions.append(f"Which accounts contribute most for {detected_entity}?")
        if detected_topic and detected_topic["opposites"]:
            opposite = detected_topic["opposites"][0]
            suggestions.append(f"What about {opposite} for the same period?")

    elif result_type == "comparison":
        # User compared groups — suggest drilling into a specific group or changing dimension
        if rows and len(rows) >= 2:
            first_label = str(rows[0].get(cols[0]["name"], ""))
            if first_label:
                suggestions.append(f"Show the account breakdown for {first_label}")
        if detected_entity:
            suggestions.append("Compare this across all legal entities instead")
        elif not has_entity:
            suggestions.append("Break this down by legal entity")
        if has_year and not has_month:
            suggestions.append("Show this by month instead of year")
        if detected_topic and detected_topic["opposites"]:
            opposite = detected_topic["opposites"][0]
            suggestions.append(f"Compare {opposite} for the same periods")
        if not has_tag and not detected_topic:
            suggestions.append("Show this split by account category (asset, liability, revenue, etc.)")

    elif result_type == "top_n":
        # User asked for top N — suggest seeing more, or changing sort
        if rows:
            top_item = str(rows[0].get(cols[0]["name"], ""))
            if top_item:
                suggestions.append(f"Show all transactions for {top_item}")
        suggestions.append("Show the bottom 10 instead")
        if not has_year and not has_year_filter:
            suggestions.append("Filter this for a specific year")
        if detected_entity:
            suggestions.append(f"Show top accounts for {detected_entity}")

    elif result_type == "generic":
        # Generic multi-row result — suggest aggregations
        if not has_entity:
            suggestions.append("Group this by legal entity")
        if not has_year:
            suggestions.append("Group this by year")
        suggestions.append("What is the total value from these results?")

    # ── Universal suggestions (add if not enough yet) ────────────────────────
    if len(suggestions) < 2:
        if not has_entity and "legal entity" not in q_lower:
            suggestions.append("Show total value per legal entity")
        if not detected_topic:
            suggestions.append("What are the total liabilities?")

    # Cap at 4 suggestions
    return suggestions[:4]


def _format_followups(suggestions: list[str]) -> str:
    """Format follow-up suggestions as a markdown section."""
    if not suggestions:
        return ""
    lines = ["\n**Follow-up questions you can ask:**"]
    for s in suggestions:
        lines.append(f"  - {s}")
    return "\n".join(lines)


class TemplateCommentary:
    """Fast deterministic commentary using pattern-matched templates. Returns None
    when the result shape is too complex, letting the caller fall back to LLM."""

    def generate(self, question: str, sql: str, result: QueryResult) -> str | None:
        if not result.rows:
            return None

        rows = result.rows
        cols = result.columns
        # BUG FIX: total_rows can be -1 when the COUNT wrapper query fails
        # (e.g., queries without ORDER BY). Use page_rows as the reliable count.
        n_rows = result.total_rows if result.total_rows >= 0 else len(rows)
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
                base = _single_value_explanation(label, float(val))
            else:
                base = f"**{col.replace('_', ' ').title()}:** {val}"
            followups = _suggest_followups(question, sql, rows, cols, "single_value")
            return base + _format_followups(followups)

        # ── Single row, multiple columns (KPI summary row) ────────────────────
        if page_rows == 1 and n_cols <= 8:
            # Detect if the user asked for a comparison but only got 1 row
            # (e.g., "compare 2024 and 2025" but only 2025 has data)
            q_lower = question.lower()
            is_comparison = any(kw in q_lower for kw in (
                "compare", "comparison", "vs", "versus", "difference",
                "between", "against",
            ))
            is_group_query = _has_keyword(sql, "GROUP BY")

            parts = []
            explanations = []
            for c in cols:
                v = rows[0][c["name"]]
                label = c["name"].replace("_", " ").title()
                if _is_numeric(v):
                    parts.append(f"**{label}:** {_fmt_number(v)}")
                    fv = float(v)
                    if abs(fv) >= 1_000:
                        explanations.append(f"{label}: {_fmt_scale(fv)}")
                elif v is not None:
                    parts.append(f"**{label}:** {v}")
            summary = "  •  ".join(parts)
            if explanations:
                summary += "\n" + "  |  ".join(explanations)

            # If user asked for a comparison but only 1 group was returned,
            # explain that the other group has no data
            if is_comparison and is_group_query and parts:
                summary += (
                    "\n\n*Note: Only **1 group** was returned. The other period "
                    "or category you asked about has no matching data in the "
                    "database. The comparison cannot be made because data exists "
                    "for only one side. Try checking which periods or entities "
                    "have data available.*"
                )

            if parts:
                followups = _suggest_followups(question, sql, rows, cols, "single_row")
                return summary + _format_followups(followups)
            return None

        # ── Comparison / GROUP BY aggregate (multi-row with numeric column) ───
        if _has_keyword(sql, "GROUP BY") and numeric_cols and page_rows >= 2:
            label_col = string_cols[0]["name"] if string_cols else cols[0]["name"]
            value_col = numeric_cols[0]["name"]
            try:
                # Extract label→value pairs for all rows
                pairs = []
                for row in rows:
                    lbl = row.get(label_col, "—")
                    val = row.get(value_col)
                    if _is_numeric(val):
                        pairs.append((str(lbl), float(val)))

                if not pairs:
                    return None

                val_label = value_col.replace("_", " ").title()
                total = sum(v for _, v in pairs)
                all_negative = all(v < 0 for _, v in pairs)
                all_positive = all(v >= 0 for _, v in pairs)

                # Sort by value descending (highest first)
                ranked = sorted(pairs, key=lambda x: x[1], reverse=True)
                highest_label, highest_val = ranked[0]
                lowest_label, lowest_val = ranked[-1]

                lines = []

                # ── Header: overview line ────────────────────────────────
                lines.append(
                    f"**{len(pairs)} groups** compared by {val_label}. "
                    f"Combined total: **{_fmt_scale(total)}**."
                )

                # ── Per-group breakdown ──────────────────────────────────
                lines.append("")
                lines.append("**Breakdown:**")
                for lbl, val in ranked:
                    pct_of_total = (val / total * 100) if total != 0 else 0
                    lines.append(
                        f"  • **{lbl}:** {_fmt_scale(val)} "
                        f"({_fmt_number(val)}) — {pct_of_total:,.1f}% of total"
                    )

                # ── Comparison insight (who's higher, who's lower) ───────
                if len(pairs) >= 2:
                    diff = highest_val - lowest_val
                    lines.append("")
                    if all_negative:
                        # All negative: "less negative = better"
                        # Re-rank: closest to 0 is "better"
                        best_label, best_val = min(pairs, key=lambda x: abs(x[1]))
                        worst_label, worst_val = max(pairs, key=lambda x: abs(x[1]))
                        lines.append(
                            f"**{best_label}** has the smaller negative amount "
                            f"({_fmt_scale(best_val)}), while **{worst_label}** "
                            f"has the larger negative amount ({_fmt_scale(worst_val)})."
                        )
                        abs_diff = abs(worst_val) - abs(best_val)
                        lines.append(
                            f"The gap between them is **{_fmt_scale(abs_diff)}**."
                        )
                        if abs(worst_val) > 0:
                            pct_change = abs_diff / abs(worst_val) * 100
                            lines.append(
                                f"**{best_label}** is {pct_change:,.1f}% less "
                                f"(in absolute terms) than {worst_label}."
                            )
                    else:
                        lines.append(
                            f"**{highest_label}** is the highest at "
                            f"{_fmt_scale(highest_val)}, while **{lowest_label}** "
                            f"is the lowest at {_fmt_scale(lowest_val)}."
                        )
                        lines.append(
                            f"The difference between them is **{_fmt_scale(diff)}**."
                        )
                        if abs(lowest_val) > 0:
                            pct_change = diff / abs(lowest_val) * 100
                            lines.append(
                                f"**{highest_label}** is {pct_change:,.1f}% "
                                f"{'higher' if diff > 0 else 'lower'} than "
                                f"{lowest_label}."
                            )

                # ── Year-over-year trend (if the label column looks like years) ──
                if _is_year_column(label_col, pairs):
                    year_sorted = sorted(pairs, key=lambda x: x[0])
                    if len(year_sorted) >= 2:
                        lines.append("")
                        lines.append("**Year-over-Year Trend:**")
                        for i in range(1, len(year_sorted)):
                            prev_lbl, prev_val = year_sorted[i - 1]
                            curr_lbl, curr_val = year_sorted[i]
                            yoy_diff = curr_val - prev_val
                            direction = "increased" if yoy_diff > 0 else "decreased"
                            if abs(prev_val) > 0:
                                yoy_pct = yoy_diff / abs(prev_val) * 100
                                lines.append(
                                    f"  • {prev_lbl} to {curr_lbl}: "
                                    f"{direction} by **{_fmt_scale(abs(yoy_diff))}** "
                                    f"({yoy_pct:+,.1f}%)"
                                )
                            else:
                                lines.append(
                                    f"  • {prev_lbl} to {curr_lbl}: "
                                    f"{direction} by **{_fmt_scale(abs(yoy_diff))}**"
                                )

                # ── Negative values note ─────────────────────────────────
                if all_negative:
                    lines.append("")
                    lines.append(
                        "*Note: All values are negative, which typically indicates "
                        "contra-entries, reversals, or net credit positions. "
                        "Consider reviewing the individual account breakdown for details.*"
                    )

                # ── Follow-up suggestions ────────────────────────────────
                followups = _suggest_followups(question, sql, rows, cols, "comparison")
                lines.append(_format_followups(followups))

                return "\n".join(lines)
            except (TypeError, ValueError):
                pass

        # ── TOP N listing ─────────────────────────────────────────────────────
        if _has_keyword(sql, "TOP") and numeric_cols and page_rows <= 50:
            label_col = string_cols[0]["name"] if string_cols else cols[0]["name"]
            value_col = numeric_cols[0]["name"]
            try:
                items = []
                for i in range(min(5, page_rows)):
                    val = rows[i].get(value_col)
                    if _is_numeric(val):
                        items.append(
                            f"**{rows[i][label_col]}** ({_fmt_scale(float(val))})"
                        )
                val_label = value_col.replace("_", " ").title()
                suffix = f", and {page_rows - 5} more" if page_rows > 5 else ""
                lines = [
                    f"**Top {page_rows} by {val_label}:**",
                    "",
                ]
                for rank, item in enumerate(items, 1):
                    lines.append(f"  {rank}. {item}")
                if suffix:
                    lines.append(f"  {suffix}")

                # Range summary
                if len(items) >= 2:
                    first_val = float(rows[0][value_col])
                    last_val = float(rows[page_rows - 1][value_col])
                    lines.append("")
                    lines.append(
                        f"Range: **{_fmt_scale(first_val)}** (highest) to "
                        f"**{_fmt_scale(last_val)}** (lowest)."
                    )

                # Follow-up suggestions
                followups = _suggest_followups(question, sql, rows, cols, "top_n")
                lines.append(_format_followups(followups))

                return "\n".join(lines)
            except (KeyError, TypeError):
                pass

        # ── Multi-row generic fallback ────────────────────────────────────────
        if n_rows > 0:
            base = f"**{n_rows:,} records** returned across {n_cols} columns."
            followups = _suggest_followups(question, sql, rows, cols, "generic")
            return base + _format_followups(followups)

        return None


def _is_year_column(col_name: str, pairs: list[tuple[str, float]]) -> bool:
    """Detect if the label column contains year-like values (e.g., '2022', '2023')."""
    if any(kw in col_name.lower() for kw in ("year", "yr", "period")):
        return True
    # Check if all labels look like 4-digit years
    return all(re.match(r'^\d{4}$', lbl) for lbl, _ in pairs)
