# ──────────────────────────────────────────────────────────────────────────────
# APPLICATION EXCEPTIONS — mapped to HTTP status codes in api/v1/query.py.
#
# Exception hierarchy:
#   SQLChatbotError (base)
#     ├── SQLValidationError    → 400 (unsafe SQL detected by validator)
#     ├── QueryExecutionError   → 500 (DB error during execution)
#     ├── QueryTimeoutError     → 408 (DB query exceeded timeout)
#     ├── TableNotFoundError    → 422 (SQL references non-existent table)
#     ├── OutOfScopeError       → 200 (question not about the database, returns friendly msg)
#     ├── SchemaIntrospectionError → 500 (DB metadata query failed)
#     └── LLMError              → 500 (Claude API failed after retries)
# ──────────────────────────────────────────────────────────────────────────────


class SQLChatbotError(Exception):
    """Base exception for the application."""
    pass


class SQLValidationError(SQLChatbotError):
    """Raised when generated SQL fails validation (forbidden keywords, injection patterns)."""
    pass


class QueryExecutionError(SQLChatbotError):
    """Raised when SQL execution fails at the database level."""
    pass


class QueryTimeoutError(SQLChatbotError):
    """Raised when a query exceeds the configured timeout (default 30s)."""
    pass


class SchemaIntrospectionError(SQLChatbotError):
    """Raised when schema introspection queries against sys.* views fail."""
    pass


class LLMError(SQLChatbotError):
    """Raised when the Claude API call fails after all retries are exhausted."""
    pass


class OutOfScopeError(SQLChatbotError):
    """Raised when Claude determines the question is not about the database.
    If answer is non-empty, Claude provided a brief general-knowledge answer
    (e.g., "What is 2+2?" → answer="4"). The orchestrator wraps this in a
    friendly response rather than returning an error."""
    def __init__(self, question: str = "", answer: str = ""):
        self.answer = answer
        super().__init__(question)


class TableNotFoundError(SQLChatbotError):
    """Raised when SQL references a table that doesn't exist. Common when Claude
    hallucinates a table name not in the schema."""
    def __init__(self, table_name: str):
        self.table_name = table_name
        super().__init__(f"Table '{table_name}' does not exist in the database")
