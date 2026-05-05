import structlog
from app.infrastructure.claude_client import ClaudeClient
from app.services.result_formatter import ResultFormatter
from app.services.query_executor import QueryResult
from app.services.template_commentary import TemplateCommentary
from app.prompts.commentary import COMMENTARY_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)


class CommentaryGenerator:
    """Generates insights from query results — templates first, LLM as fallback."""

    def __init__(self, claude_client: ClaudeClient,
                 result_formatter: ResultFormatter):
        self._claude = claude_client
        self._formatter = result_formatter
        self._template = TemplateCommentary()

    async def generate(self, question: str, sql: str,
                       result: QueryResult) -> str:
        """Generate commentary for query results."""
        if not result.rows:
            return ("The query returned no results. This might mean there is "
                    "no data matching your criteria, or the time range/filters "
                    "may need adjusting.")

        # Fast path: deterministic template (0ms, no LLM)
        template_result = self._template.generate(question, sql, result)
        if template_result is not None:
            logger.info("commentary_from_template", length=len(template_result))
            return template_result

        # Slow path: LLM commentary for complex/unrecognised result shapes
        results_text = self._formatter.format_for_commentary(result)

        user_message = (
            f"User question: {question}\n\n"
            f"SQL executed:\n{sql}\n\n"
            f"Results ({result.total_rows} total rows):\n{results_text}"
        )

        try:
            commentary = await self._claude.complete(
                system_prompt=COMMENTARY_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.3,
                max_tokens=1024,
            )
            logger.info("commentary_generated", length=len(commentary))
            return commentary
        except Exception as e:
            logger.error("commentary_generation_failed", error=str(e))
            return ("Unable to generate commentary at this time. "
                    "Please review the results directly.")
