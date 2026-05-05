import pytest
from unittest.mock import AsyncMock
from app.services.nl_to_sql import NLToSQLEngine


class TestNLToSQLCleanSQL:
    def test_strips_markdown_fencing(self):
        raw = "```sql\nSELECT 1\n```"
        assert NLToSQLEngine._clean_sql(raw) == "SELECT 1"

    def test_strips_plain_fencing(self):
        raw = "```\nSELECT 1\n```"
        assert NLToSQLEngine._clean_sql(raw) == "SELECT 1"

    def test_strips_trailing_semicolon(self):
        raw = "SELECT TOP 10 * FROM Products;"
        assert NLToSQLEngine._clean_sql(raw) == "SELECT TOP 10 * FROM Products"

    def test_strips_comments(self):
        raw = "SELECT 1 -- this is a comment\nFROM Products"
        result = NLToSQLEngine._clean_sql(raw)
        assert "--" not in result
        assert "FROM Products" in result

    def test_handles_clean_sql(self):
        raw = "SELECT Name FROM [dbo].[Products]"
        assert NLToSQLEngine._clean_sql(raw) == raw

    def test_strips_whitespace(self):
        raw = "  \n  SELECT 1  \n  "
        assert NLToSQLEngine._clean_sql(raw) == "SELECT 1"


@pytest.mark.asyncio
async def test_generate_sql_calls_claude():
    mock_claude = AsyncMock()
    mock_claude.complete = AsyncMock(return_value="SELECT COUNT(*) FROM [dbo].[Orders]")

    mock_schema = AsyncMock()
    mock_schema.get_tables = AsyncMock(return_value=[])
    mock_schema.format_for_prompt = lambda tables: "no tables"

    engine = NLToSQLEngine(mock_claude, mock_schema)
    result = await engine.generate_sql("How many orders?")

    assert result == "SELECT COUNT(*) FROM [dbo].[Orders]"
    mock_claude.complete.assert_called_once()
