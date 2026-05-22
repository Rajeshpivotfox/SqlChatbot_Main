# ──────────────────────────────────────────────────────────────────────────────
# RESULT FORMATTER — converts raw DB results for two consumers:
#
#   1. format_for_response() → JSON for the frontend (API response)
#      Handles Decimal→float, datetime→ISO string, bytes→hex
#
#   2. format_for_commentary() → compact text table for Claude API call #2
#      TOKEN EFFICIENCY: uses pipe-separated format (not JSON) to minimize
#      tokens. max_rows caps the input size — Claude only needs 10-20 rows
#      to spot patterns; sending 50+ wastes tokens for no better insight.
#      CommentaryGenerator passes max_rows=20.
#
#   3. detect_chart_type() → heuristic for frontend chart rendering
#      Not used for token-related purposes.
# ──────────────────────────────────────────────────────────────────────────────

import structlog
from datetime import datetime, date
from decimal import Decimal
from app.services.query_executor import QueryResult

logger = structlog.get_logger(__name__)


class ResultFormatter:
    """Formats query results for API response and LLM commentary input."""

    def format_for_response(self, result: QueryResult) -> dict:
        """Convert QueryResult to a JSON-serializable response dict for the frontend."""
        serialized_rows = [
            {k: self._serialize_value(v) for k, v in row.items()}
            for row in result.rows
        ]
        return {
            "columns": result.columns,
            "rows": serialized_rows,
            "total_rows": result.total_rows,
        }

    def format_for_commentary(self, result: QueryResult, max_rows: int = 50) -> str:
        """Format results as a compact pipe-separated text table for LLM input.
        Caller should pass max_rows=20 to keep token count low."""
        if not result.rows:
            return "(No results returned)"

        col_names = [c["name"] for c in result.columns]
        lines = [" | ".join(col_names)]
        lines.append("-" * len(lines[0]))

        for row in result.rows[:max_rows]:
            values = [str(self._serialize_value(row.get(c, ""))) for c in col_names]
            lines.append(" | ".join(values))

        if result.total_rows > max_rows:
            lines.append(f"... ({result.total_rows - max_rows} more rows)")

        return "\n".join(lines)

    def detect_chart_type(self, result: QueryResult) -> str | None:
        """Heuristic to suggest a chart type based on result shape.
        Returns: 'metric', 'bar', 'line', or 'table'."""
        if not result.rows or not result.columns:
            return None

        num_cols = len(result.columns)
        num_rows = len(result.rows)

        # Single number = KPI card
        if num_rows == 1 and num_cols == 1:
            return "metric"

        # String + number = bar chart (e.g., entity names + values)
        if num_cols == 2:
            types = [c["type"] for c in result.columns]
            if "str" in types and any(t in ("int", "float", "Decimal") for t in types):
                return "bar"

        # Date + number = line/time-series chart
        date_cols = [c for c in result.columns if "date" in c["name"].lower()
                     or c["type"] in ("datetime", "date")]
        numeric_cols = [c for c in result.columns
                        if c["type"] in ("int", "float", "Decimal")]
        if date_cols and numeric_cols:
            return "line"

        return "table"

    @staticmethod
    def _serialize_value(value):
        """Convert non-JSON-serializable types to primitives."""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, bytes):
            return value.hex()
        return value
