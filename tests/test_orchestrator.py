import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.orchestrator import QueryOrchestrator
from app.services.query_executor import QueryResult
from app.services.cache_service import CacheService
from app.services.sql_validator import SQLValidator
from app.services.result_formatter import ResultFormatter
from app.exceptions import SQLValidationError


@pytest.fixture
def mock_orchestrator():
    nl_engine = AsyncMock()
    nl_engine.generate_sql = AsyncMock(
        return_value="SELECT COUNT(*) AS total FROM [dbo].[Orders]"
    )

    validator = SQLValidator()

    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=QueryResult(
        columns=[{"name": "total", "type": "int"}],
        rows=[{"total": 1500}],
        total_rows=1,
        execution_time_ms=25.0,
    ))

    formatter = ResultFormatter()

    commentary_gen = AsyncMock()
    commentary_gen.generate = AsyncMock(
        return_value="There are 1,500 orders in the database."
    )

    cache = CacheService(ttl_seconds=60)

    return QueryOrchestrator(
        nl_engine=nl_engine,
        validator=validator,
        executor=executor,
        formatter=formatter,
        commentary_gen=commentary_gen,
        cache=cache,
    )


@pytest.mark.asyncio
async def test_full_pipeline(mock_orchestrator):
    response = await mock_orchestrator.process_question("How many orders?")

    assert response.query_id
    assert response.generated_sql == "SELECT COUNT(*) AS total FROM [dbo].[Orders]"
    assert response.total_rows == 1
    assert response.rows[0]["total"] == 1500
    assert response.commentary == "There are 1,500 orders in the database."
    assert response.execution_time_ms > 0


@pytest.mark.asyncio
async def test_pipeline_caches_result(mock_orchestrator):
    await mock_orchestrator.process_question("How many orders?")
    response2 = await mock_orchestrator.process_question("How many orders?")

    # Second call should hit cache, so NL engine called only once
    assert mock_orchestrator._nl_engine.generate_sql.call_count == 1
    assert response2.total_rows == 1


@pytest.mark.asyncio
async def test_pipeline_without_commentary(mock_orchestrator):
    response = await mock_orchestrator.process_question(
        "How many orders?", include_commentary=False
    )
    assert response.commentary is None
    mock_orchestrator._commentary.generate.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_rejects_bad_sql():
    nl_engine = AsyncMock()
    nl_engine.generate_sql = AsyncMock(
        return_value="DROP TABLE Users"
    )

    orchestrator = QueryOrchestrator(
        nl_engine=nl_engine,
        validator=SQLValidator(),
        executor=AsyncMock(),
        formatter=ResultFormatter(),
        commentary_gen=AsyncMock(),
        cache=CacheService(),
    )

    with pytest.raises(SQLValidationError):
        await orchestrator.process_question("delete everything")
