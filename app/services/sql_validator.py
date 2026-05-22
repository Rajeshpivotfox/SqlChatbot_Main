# ──────────────────────────────────────────────────────────────────────────────
# SQL VALIDATOR — defense-in-depth safety gate (Step 3 in the pipeline).
#
# Even though Claude is prompted to only generate SELECT statements, we
# validate the output before executing it. This protects against:
#   1. Prompt injection: user tricks Claude into generating harmful SQL
#   2. Model hallucination: Claude generates non-SELECT accidentally
#   3. Malformed output: incomplete or concatenated statements
#
# Validation checks (in order):
#   1. Non-empty query
#   2. Length cap (5000 chars — prevents absurdly large generated queries)
#   3. Must start with SELECT (or WITH ... SELECT for CTEs)
#   4. Forbidden keyword scan (INSERT, UPDATE, DELETE, DROP, xp_*, sp_*, etc.)
#   5. No multi-statement execution (reject semicolons followed by content)
#   6. Common SQL injection patterns ('; -- and ' OR '1'='1')
#
# This costs 0 tokens — it's pure regex. The patterns are compiled once at
# module load for performance.
# ──────────────────────────────────────────────────────────────────────────────

import re
import structlog
from app.exceptions import SQLValidationError

logger = structlog.get_logger(__name__)

# Dangerous SQL keywords/patterns that should never appear in generated queries
FORBIDDEN_KEYWORDS = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
    r"\bALTER\b", r"\bCREATE\b", r"\bTRUNCATE\b", r"\bEXEC\b",
    r"\bEXECUTE\b", r"\bMERGE\b", r"\bGRANT\b", r"\bREVOKE\b",
    r"\bDENY\b", r"\bBACKUP\b", r"\bRESTORE\b", r"\bSHUTDOWN\b",
    r"\bRECONFIGURE\b", r"\bBULK\s+INSERT\b",
    r"\bOPENROWSET\b", r"\bOPENDATASOURCE\b", r"\bOPENQUERY\b",
    r"\bxp_\w+", r"\bsp_\w+",  # Extended/stored procs
    r"\bINTO\b",                # SELECT INTO creates tables
    r"\bWAITFOR\b",            # Time-based attacks
]

# Compiled once at module load for O(1) re-use
FORBIDDEN_PATTERN = re.compile(
    "|".join(FORBIDDEN_KEYWORDS),
    re.IGNORECASE | re.MULTILINE
)

# Allows: SELECT ... or WITH cte AS (...) SELECT ...
VALID_START_PATTERN = re.compile(
    r"^\s*(WITH\s+\w+\s+AS\s*\(.*?\)\s*)?SELECT\b",
    re.IGNORECASE | re.DOTALL
)

MAX_QUERY_LENGTH = 5000


class SQLValidator:
    """Validates generated SQL to ensure safety. Costs 0 tokens — pure regex."""

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

        # Prevent multi-statement execution (e.g., SELECT 1; DROP TABLE x)
        if re.search(r";\s*\S", sql):
            raise SQLValidationError("Multiple SQL statements are not allowed")

        # Common SQL injection signatures
        if re.search(r"'\s*;\s*--", sql) or \
           re.search(r"'\s*OR\s+'1'\s*=\s*'1", sql, re.IGNORECASE):
            raise SQLValidationError("Potential SQL injection pattern detected")

        logger.debug("sql_validation_passed", sql_length=len(sql))
        return sql.strip().rstrip(";")
