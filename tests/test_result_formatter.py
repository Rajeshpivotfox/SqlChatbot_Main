import pytest
from datetime import datetime
from decimal import Decimal
from app.services.result_formatter import ResultFormatter
from app.services.query_executor import QueryResult


@pytest.fixture
def formatter():
    return ResultFormatter()


class TestResultFormatter:
    def test_serialize_datetime(self, formatter):
        dt = datetime(2024, 1, 15, 10, 30, 0)
        assert formatter._serialize_value(dt) == "2024-01-15T10:30:00"

    def test_serialize_decimal(self, formatter):
        d = Decimal("123.45")
        assert formatter._serialize_value(d) == 123.45

    def test_serialize_string(self, formatter):
        assert formatter._serialize_value("hello") == "hello"

    def test_serialize_none(self, formatter):
        assert formatter._serialize_value(None) is None

    def test_format_for_response(self, formatter):
        result = QueryResult(
            columns=[{"name": "id", "type": "int"}, {"name": "name", "type": "str"}],
            rows=[{"id": 1, "name": "Test"}],
            total_rows=1,
            execution_time_ms=10.0,
        )
        formatted = formatter.format_for_response(result)
        assert formatted["total_rows"] == 1
        assert len(formatted["rows"]) == 1
        assert formatted["rows"][0]["name"] == "Test"

    def test_format_for_commentary_empty(self, formatter):
        result = QueryResult(columns=[], rows=[], total_rows=0, execution_time_ms=0)
        text = formatter.format_for_commentary(result)
        assert "No results" in text

    def test_format_for_commentary_with_data(self, formatter):
        result = QueryResult(
            columns=[{"name": "City", "type": "str"}, {"name": "Count", "type": "int"}],
            rows=[{"City": "NYC", "Count": 100}, {"City": "LA", "Count": 80}],
            total_rows=2,
            execution_time_ms=5.0,
        )
        text = formatter.format_for_commentary(result)
        assert "City" in text
        assert "NYC" in text

    def test_detect_chart_type_metric(self, formatter):
        result = QueryResult(
            columns=[{"name": "total", "type": "int"}],
            rows=[{"total": 42}],
            total_rows=1,
            execution_time_ms=1.0,
        )
        assert formatter.detect_chart_type(result) == "metric"

    def test_detect_chart_type_bar(self, formatter):
        result = QueryResult(
            columns=[{"name": "Category", "type": "str"}, {"name": "Sales", "type": "int"}],
            rows=[{"Category": "A", "Sales": 100}],
            total_rows=1,
            execution_time_ms=1.0,
        )
        assert formatter.detect_chart_type(result) == "bar"

    def test_detect_chart_type_empty(self, formatter):
        result = QueryResult(columns=[], rows=[], total_rows=0, execution_time_ms=0)
        assert formatter.detect_chart_type(result) is None
