class SQLChatbotError(Exception):
    """Base exception for the application."""
    pass


class SQLValidationError(SQLChatbotError):
    """Raised when generated SQL fails validation."""
    pass


class QueryExecutionError(SQLChatbotError):
    """Raised when SQL execution fails."""
    pass


class QueryTimeoutError(SQLChatbotError):
    """Raised when a query exceeds the timeout."""
    pass


class SchemaIntrospectionError(SQLChatbotError):
    """Raised when schema introspection fails."""
    pass


class LLMError(SQLChatbotError):
    """Raised when the Claude API call fails after retries."""
    pass


class OutOfScopeError(SQLChatbotError):
    """Raised when the question is not related to the database."""
    def __init__(self, question: str = "", answer: str = ""):
        self.answer = answer
        super().__init__(question)


class TableNotFoundError(SQLChatbotError):
    """Raised when the generated SQL references a non-existent table."""
    def __init__(self, table_name: str):
        self.table_name = table_name
        super().__init__(f"Table '{table_name}' does not exist in the database")
