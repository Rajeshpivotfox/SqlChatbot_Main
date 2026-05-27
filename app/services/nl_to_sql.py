# ──────────────────────────────────────────────────────────────────────────────
# NL-TO-SQL ENGINE — Claude API call #1 (the expensive one).
#
# This is the primary token consumer. The system prompt contains:
#   1. Static rules (~400 tokens, cacheable by Anthropic)
#   2. DB schema (variable, depends on table filtering)
#   3. Few-shot examples (~600 tokens)
#   4. Conversation history (variable, 0-6 turns)
#
# TOKEN EFFICIENCY STRATEGIES USED:
#   - Table filtering: _filter_relevant_tables() sends only tables whose names
#     or column names match keywords in the question. This shrinks the schema
#     section from ~all tables to ~1-3 relevant ones.
#   - Few-shot selection: _select_relevant_examples() picks only examples that
#     reference tables present in the filtered schema. Fewer examples = fewer
#     input tokens without hurting accuracy for the specific question.
#   - max_tokens=512: SQL output is ~100-300 tokens. The old default (4096)
#     wasted the model's attention budget.
#   - History compression: only the last 3 turns carry full SQL; older turns
#     carry just the question to save tokens while preserving context.
#   - Stable system prompt: the static portion (rules + examples) doesn't
#     change between calls, so Anthropic's automatic prompt caching kicks in.
#
# SCOPE CHECK: if Claude determines the question is unrelated to the database,
# it returns "OUT_OF_SCOPE" (or "OUT_OF_SCOPE:<answer>" for general knowledge).
# This is caught here and raised as OutOfScopeError → the orchestrator returns
# a friendly "I can only answer database questions" response.
# ──────────────────────────────────────────────────────────────────────────────

import re
import structlog
from app.infrastructure.claude_client import ClaudeClient
from app.services.schema_service import SchemaService, TableMetadata
from app.prompts.nl_to_sql import NL_TO_SQL_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES_TEMPLATE
from app.exceptions import OutOfScopeError

logger = structlog.get_logger(__name__)

# ── Few-shot examples ────────────────────────────────────────────────────────
# Each example teaches Claude a SQL pattern for the denormalized view.
# ALL queries target [dbo].[transactionaldata] — no JOINs needed.
# The "tables" key lets _select_relevant_examples() pick only examples
# that reference objects present in the current prompt's schema.

DEFAULT_FEW_SHOT = [
    {
        "question": "How many transactions are in the database?",
        "sql": ("SELECT COUNT(*) AS total_transactions "
                "FROM [dbo].[transactionaldata]"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "What are the top 10 accounts by total value?",
        "sql": ("SELECT TOP 10 account_id, account_description, "
                "SUM(value) AS total_value "
                "FROM [dbo].[transactionaldata] "
                "GROUP BY account_id, account_description "
                "ORDER BY total_value DESC"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "Show me all transactions for period Jan2022",
        "sql": ("SELECT account_id, account_description, legal_entity_name, "
                "value, period, transaction_type_desc "
                "FROM [dbo].[transactionaldata] "
                "WHERE period = 'Jan2022' "
                "ORDER BY value DESC"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "What is the total value per legal entity?",
        "sql": ("SELECT legal_entity_name, "
                "SUM(value) AS total_value, "
                "COUNT(*) AS transaction_count "
                "FROM [dbo].[transactionaldata] "
                "GROUP BY legal_entity_name "
                "ORDER BY total_value DESC"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "Show me transactions for year 2022",
        "sql": ("SELECT account_description, legal_entity_name, value, "
                "period, full_month, year "
                "FROM [dbo].[transactionaldata] "
                "WHERE year = '2022' "
                "ORDER BY period, value DESC"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "What is the total liability?",
        "sql": ("SELECT SUM(value) AS total_liability "
                "FROM [dbo].[transactionaldata] "
                "WHERE tag LIKE '%, Liability'"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "What is the total revenue?",
        "sql": ("SELECT SUM(value) AS total_revenue "
                "FROM [dbo].[transactionaldata] "
                "WHERE tag LIKE '%, Revenue'"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "Show total value by account category",
        "sql": ("SELECT tag AS account_tag, "
                "RIGHT(tag, CHARINDEX(',', REVERSE(tag)) - 2) AS category, "
                "SUM(value) AS total_value "
                "FROM [dbo].[transactionaldata] "
                "GROUP BY tag "
                "ORDER BY total_value DESC"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "Show monthly totals for January",
        "sql": ("SELECT year, full_month, "
                "SUM(value) AS total_value, "
                "COUNT(*) AS transaction_count "
                "FROM [dbo].[transactionaldata] "
                "WHERE short_month = 'Jan' "
                "GROUP BY year, full_month "
                "ORDER BY year"),
        "tables": {"transactionaldata"},
    },
    {
        "question": "What data types are there?",
        "sql": ("SELECT DISTINCT data_type, data_type_desc "
                "FROM [dbo].[transactionaldata] "
                "ORDER BY data_type"),
        "tables": {"transactionaldata"},
    },
]


class NLToSQLEngine:
    """Converts natural language questions to SQL using Claude API (call #1)."""

    def __init__(self, claude_client: ClaudeClient, schema_service: SchemaService,
                 few_shot_examples: list[dict] | None = None):
        self._claude = claude_client
        self._schema_service = schema_service
        self._few_shot = few_shot_examples or DEFAULT_FEW_SHOT

    async def generate_sql(self, question: str,
                           history: list[dict] | None = None) -> str:
        """Generate a SQL query from a natural language question.
        This is the most token-intensive call — ~1000-2000 input tokens."""
        tables = await self._schema_service.get_tables()
        relevant = self._filter_relevant_tables(question, tables, history)
        schema_text = self._schema_service.format_for_prompt(relevant)

        # Only include examples that reference tables in the filtered schema
        relevant_table_names = {t.table_name.lower() for t in relevant}
        selected_examples = self._select_relevant_examples(relevant_table_names)

        examples_text = "\n".join(
            FEW_SHOT_EXAMPLES_TEMPLATE.format(
                question=ex["question"], sql=ex["sql"]
            ) for ex in selected_examples
        )

        history_text = self._format_history(history) if history else ""

        system_prompt = NL_TO_SQL_SYSTEM_PROMPT.format(
            schema=schema_text,
            examples=examples_text,
            history=history_text,
        )

        # max_tokens=512: SQL output is typically 100-300 tokens.
        # Lower cap = faster response + prevents runaway generation.
        raw_sql = await self._claude.complete(
            system_prompt=system_prompt,
            user_message=f"Question: {question}\nSQL:",
            temperature=0.0,
            max_tokens=512,
        )

        # Claude returns "OUT_OF_SCOPE" for non-database questions
        stripped = raw_sql.strip()
        if stripped.upper().startswith("OUT_OF_SCOPE"):
            answer = ""
            colon_idx = stripped.find(":")
            if colon_idx != -1:
                answer = stripped[colon_idx + 1:].strip()
            logger.info("question_out_of_scope", question=question,
                        has_answer=bool(answer))
            raise OutOfScopeError(question, answer=answer)

        sql = self._clean_sql(raw_sql)
        logger.info("sql_generated", question=question, sql=sql,
                    tables_in_prompt=len(relevant),
                    examples_in_prompt=len(selected_examples))
        return sql

    def _select_relevant_examples(self, table_names: set[str]) -> list[dict]:
        """Pick few-shot examples that reference tables in the current schema.
        Falls back to all examples if none match (e.g., when all tables are included)."""
        selected = [
            ex for ex in self._few_shot
            if ex.get("tables", set()) & table_names
        ]
        return selected if selected else self._few_shot

    @staticmethod
    def _format_history(history: list[dict]) -> str:
        """Format conversation history for the prompt.

        Token efficiency: only the last 3 turns include full SQL.
        Older turns include just the question — enough for Claude to understand
        the topic arc without paying for stale SQL that won't be reused."""
        if not history:
            return ""
        lines = [
            "\nCONVERSATION HISTORY — FOLLOW-UP RULES (read carefully):",
            "If the new question is a follow-up — i.e. it uses words like 'now', 'only',",
            "'same', 'those', 'also', 'but', 'instead', 'filter', 'what about', or clearly",
            "refers to previous results — you MUST:",
            "  1. Take the MOST RECENT SQL shown below as your starting point.",
            "  2. MODIFY that SQL to satisfy the new requirement (add WHERE clause, change",
            "     TOP N, adjust GROUP BY, etc.). Preserve all JOINs, GROUP BY, aggregations",
            "     and aliases from the base query unless the user explicitly changes them.",
            "  3. Do NOT write a brand-new query from scratch for follow-ups.",
            "",
            "Previous conversation turns (oldest → newest):",
        ]
        # Only include full SQL for the last 3 turns to save tokens
        recent_cutoff = max(0, len(history) - 3)
        for i, turn in enumerate(history, 1):
            tag = "  ← MOST RECENT BASE QUERY" if i == len(history) else ""
            if i - 1 < recent_cutoff:
                # Older turns: question only (saves ~50-150 tokens per turn)
                lines.append(f"  Turn {i}: Q: \"{turn['question']}\"")
            else:
                # Recent turns: full SQL included for modification
                rows_note = f"  [{turn['row_count']:,} rows returned]" if turn.get("row_count", 0) > 0 else ""
                lines.append(f"  Turn {i}: Q: \"{turn['question']}\"")
                lines.append(f"           SQL: {turn['sql']}{rows_note}{tag}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _extract_tables_from_sql(sql: str) -> set[str]:
        """Return lowercase table names referenced in a SQL string.
        Matches patterns like [dbo].[tblName] or dbo.tblName."""
        return {m[1].lower() for m in re.findall(r'\[?\w+\]?\.\[?(\w+)\]?', sql)}

    def _filter_relevant_tables(self, question: str,
                                 tables: list[TableMetadata],
                                 history: list[dict] | None = None) -> list[TableMetadata]:
        """Return tables relevant to the question via keyword overlap.

        THIS IS THE KEY TOKEN-SAVING MECHANISM. Instead of sending the full
        schema for ALL tables (~200+ tokens per table), we only send tables
        whose name or column names overlap with keywords in the question.
        For a 10-table DB, this can cut schema tokens by 70-80%.

        Inclusion rules (a table is included if ANY of these match):
          1. Table name or column name overlaps with question keywords
          2. Table is a FK target of a matched table (for JOINs)
          3. Table was referenced in conversation history SQL (for follow-ups)
        Falls back to ALL tables if nothing matches (safety net)."""

        # Stop words: common English words that don't indicate table/column names
        _STOP = {
            "what", "show", "get", "list", "how", "many", "the", "is", "are",
            "for", "of", "in", "a", "an", "all", "me", "my", "our", "do",
            "does", "did", "can", "i", "you", "we", "to", "and", "or", "by",
            "with", "from", "give", "tell", "find", "which", "where", "when",
            "who", "that", "this", "those", "these", "on", "at", "has", "have",
        }

        q_tokens = set(re.sub(r"[^a-z0-9]", " ", question.lower()).split()) - _STOP
        if not q_tokens:
            return tables

        def _tokenize_name(name: str) -> set[str]:
            """Split CamelCase/PascalCase names into keywords.
            e.g. 'tblTransactionalData' → {'transactional', 'data'}"""
            name = re.sub(r"^(tbl|dim|fact|vw|sp|fn)", "", name, flags=re.I)
            name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
            name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
            return set(name.lower().split())

        matched: set[str] = set()
        fk_targets: set[str] = set()

        for tbl in tables:
            key = f"{tbl.schema_name}.{tbl.table_name}"
            tbl_tokens = _tokenize_name(tbl.table_name)
            col_tokens: set[str] = set()
            for col in tbl.columns:
                col_tokens.update(_tokenize_name(col.name))
                # Track FK targets so we auto-include tables needed for JOINs
                if col.foreign_key_ref:
                    fk_parts = col.foreign_key_ref.split(".")
                    fk_targets.add(fk_parts[0] if fk_parts else col.foreign_key_ref)

            if q_tokens & (tbl_tokens | col_tokens):
                matched.add(key)

        # Follow-up support: include tables from previous SQL even if the new
        # question has no table-matching keywords ("now show only X")
        history_tables: set[str] = set()
        if history:
            for turn in history:
                history_tables.update(self._extract_tables_from_sql(turn.get("sql", "")))

        # Safety net: if nothing matched, send all tables (question may use
        # synonyms or phrasing that doesn't overlap with schema names)
        if not matched and not history_tables:
            return tables

        result = [
            tbl for tbl in tables
            if f"{tbl.schema_name}.{tbl.table_name}" in matched
            or tbl.table_name in fk_targets
            or tbl.table_name.lower() in history_tables
        ]
        return result if result else tables

    @staticmethod
    def _clean_sql(raw: str) -> str:
        """Strip markdown fencing, comments, trailing semicolons.
        Claude sometimes wraps SQL in ```sql ... ``` despite instructions."""
        sql = raw.strip()
        sql = re.sub(r"^```(?:sql)?\s*", "", sql)
        sql = re.sub(r"\s*```$", "", sql)
        sql = sql.rstrip(";").strip()
        sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
        return sql.strip()
