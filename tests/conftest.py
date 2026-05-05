import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.sql_validator import SQLValidator
from app.services.result_formatter import ResultFormatter
from app.services.query_executor import QueryResult
from app.infrastructure.claude_client import ClaudeClient


@pytest.fixture
def sql_validator():
    return SQLValidator()


@pytest.fixture
def result_formatter():
    return ResultFormatter()


@pytest.fixture
def mock_claude_client():
    client = AsyncMock(spec=ClaudeClient)
    client.complete = AsyncMock(return_value="SELECT 1")
    return client


@pytest.fixture
def sample_query_result():
    return QueryResult(
        columns=[
            {"name": "ProductName", "type": "str"},
            {"name": "Revenue", "type": "Decimal"},
        ],
        rows=[
            {"ProductName": "Widget A", "Revenue": 15000.50},
            {"ProductName": "Widget B", "Revenue": 12300.00},
            {"ProductName": "Widget C", "Revenue": 9800.75},
        ],
        total_rows=3,
        execution_time_ms=42.5,
    )
