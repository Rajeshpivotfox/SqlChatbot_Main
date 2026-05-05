import re
import structlog
from app.exceptions import SQLValidationError

logger = structlog.get_logger(__name__)

FORBIDDEN_KEYWORDS = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
    r"\bALTER\b", r"\bCREATE\b", r"\bTRUNCATE\b", r"\bEXEC\b",
    r"\bEXECUTE\b", r"\bMERGE\b", r"\bGRANT\b", r"\bREVOKE\b",
    r"\bDENY\b", r"\bBACKUP\b", r"\bRESTORE\b", r"\bSHUTDOWN\b",
    r"\bRECONFIGURE\b", r"\bBULK\s+INSERT\b",
    r"\bOPENROWSET\b", r"\bOPENDATASOURCE\b", r"\bOPENQUERY\b",
    r"\bxp_\w+", r"\bsp_\w+",
    r"\bINTO\b",
    r"\bWAITFOR\b",
]

FORBIDDEN_PATTERN = re.compile(
    "|".join(FORBIDDEN_KEYWORDS),
    re.IGNORECASE | re.MULTILINE
)

VALID_START_PATTERN = re.compile(
    r"^\s*(WITH\s+\w+\s+AS\s*\(.*?\)\s*)?SELECT\b",
    re.IGNORECASE | re.DOTALL
)

MAX_QUERY_LENGTH = 5000


class SQLValidator:
    """Validates generated SQL to ensure safety."""

    def validate(self, sql: str) -> str:
        """Validate SQL and return it if safe. Raises SQLValidationError if unsafe."""
        if not sql or not sql.strip():
            raise SQLValidationError("Empty SQL query")

        if len(sql) > MAX_QUERY_LENGTH:
            raise SQLValidationError(
                f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters"
            )

        if not VALID_START_PATTERN.match(sql):
            raise SQLValidationError(
                "Query must begin with SELECT (or WITH ... SELECT)"
            )

        match = FORBIDDEN_PATTERN.search(sql)
        if match:
            keyword = match.group()
            logger.warning("forbidden_sql_keyword_detected",
                           keyword=keyword, sql=sql[:200])
            raise SQLValidationError(
                f"Forbidden SQL operation detected: {keyword}"
            )

        if re.search(r";\s*\S", sql):
            raise SQLValidationError("Multiple SQL statements are not allowed")

        if re.search(r"'\s*;\s*--", sql) or \
           re.search(r"'\s*OR\s+'1'\s*=\s*'1", sql, re.IGNORECASE):
            raise SQLValidationError("Potential SQL injection pattern detected")

        logger.debug("sql_validation_passed", sql_length=len(sql))
        return sql.strip().rstrip(";")
