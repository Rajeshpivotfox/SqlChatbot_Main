import structlog
from datetime import datetime, date
from decimal import Decimal
from app.services.query_executor import QueryResult

logger = structlog.get_logger(__name__)


class ResultFormatter:
    """Formats query results for API response and chart rendering."""

    def format_for_response(self, result: QueryResult) -> dict:
        """Convert QueryResult to a JSON-serializable response dict."""
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
        """Format results as a compact text table for LLM commentary input."""
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
        """Heuristic to suggest a chart type based on result shape."""
        if not result.rows or not result.columns:
            return None

        num_cols = len(result.columns)
        num_rows = len(result.rows)

        if num_rows == 1 and num_cols == 1:
            return "metric"

        if num_cols == 2:
            types = [c["type"] for c in result.columns]
            if "str" in types and any(t in ("int", "float", "Decimal") for t in types):
                return "bar"

        date_cols = [c for c in result.columns if "date" in c["name"].lower()
                     or c["type"] in ("datetime", "date")]
        numeric_cols = [c for c in result.columns
                        if c["type"] in ("int", "float", "Decimal")]
        if date_cols and numeric_cols:
            return "line"

        return "table"

    @staticmethod
    def _serialize_value(value):
        """Convert non-JSON-serializable types."""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, bytes):
            return value.hex()
        return value
