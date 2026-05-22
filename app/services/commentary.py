# ──────────────────────────────────────────────────────────────────────────────
# COMMENTARY GENERATOR — Claude API call #2 (optional, often skipped).
#
# Two-tier strategy to minimize token usage:
#   1. TemplateCommentary (fast path): pattern-matches the result shape
#      (single value, GROUP BY aggregate, TOP N list) and generates
#      deterministic commentary with 0 API calls. Handles ~60-70% of queries.
#   2. Claude LLM (slow path): only called for complex/unrecognised result
#      shapes that templates can't handle. Uses a compact text-table format
#      (max 20 rows) to keep input tokens low.
#
# TOKEN BUDGET (when LLM path is used):
#   Input:  system prompt (~150 tokens) + user message (~200-800 tokens)
#   Output: max_tokens=512 (3-5 sentence summary)
#   Total:  ~400-1000 tokens per call
# ──────────────────────────────────────────────────────────────────────────────

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
        """Generate commentary. Returns template commentary when possible
        (0 tokens), falls back to Claude API only for complex results."""
        if not result.rows:
            return ("The query returned no results. This might mean there is "
                    "no data matching your criteria, or the time range/filters "
                    "may need adjusting.")

        # Fast path: deterministic template (0ms, 0 tokens, no LLM call)
        template_result = self._template.generate(question, sql, result)
        if template_result is not None:
            logger.info("commentary_from_template", length=len(template_result))
            return template_result

        # Slow path: LLM commentary for complex/unrecognised result shapes
        # max_rows=20 keeps input compact — 20 rows is enough for Claude to
        # spot patterns; sending 50+ rows wastes tokens without better insight
        results_text = self._formatter.format_for_commentary(result, max_rows=20)

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
                # 512 is enough for 3-5 sentence insights (was 1024)
                max_tokens=512,
            )
            logger.info("commentary_generated", length=len(commentary))
            return commentary
        except Exception as e:
            logger.error("commentary_generation_failed", error=str(e))
            return ("Unable to generate commentary at this time. "
                    "Please review the results directly.")
