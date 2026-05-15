import re
import structlog
from app.infrastructure.claude_client import ClaudeClient
from app.services.schema_service import SchemaService, TableMetadata
from app.prompts.nl_to_sql import NL_TO_SQL_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES_TEMPLATE
from app.exceptions import OutOfScopeError

logger = structlog.get_logger(__name__)

DEFAULT_FEW_SHOT = [
    {
        "question": "How many transactions are in the database?",
        "sql": ("SELECT COUNT(*) AS total_transactions "
                "FROM [dbo].[tblTransactionalData]")
    },
    {
        "question": "What are the top 10 accounts by total value?",
        "sql": ("SELECT TOP 10 t.AccountID, c.AccountDescription, "
                "SUM(t.Value) AS total_value "
                "FROM [dbo].[tblTransactionalData] t "
                "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
                "GROUP BY t.AccountID, c.AccountDescription "
                "ORDER BY total_value DESC")
    },
    {
        "question": "Show me all transactions for period Jan2022",
        "sql": ("SELECT TransactionID, AccountID, LegalEntity, Value, Period "
                "FROM [dbo].[tblTransactionalData] "
                "WHERE Period = 'Jan2022' "
                "ORDER BY Value DESC")
    },
    {
        "question": "What is the total value per legal entity?",
        "sql": ("SELECT LegalEntity, "
                "SUM(Value) AS total_value, "
                "COUNT(*) AS transaction_count "
                "FROM [dbo].[tblTransactionalData] "
                "GROUP BY LegalEntity "
                "ORDER BY total_value DESC")
    },
    {
        "question": "Show me all exchange rates for a specific period",
        "sql": ("SELECT Description, Category, Period, Rate, Fraction "
                "FROM [dbo].[tblExchangeRates] "
                "ORDER BY Period, Description")
    },
    {
        "question": "What is the total liability?",
        "sql": ("SELECT SUM(t.Value) AS total_liability "
                "FROM [dbo].[tblTransactionalData] t "
                "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
                "WHERE c.Tag LIKE '%, Liability'")
    },
    {
        "question": "What is the total revenue?",
        "sql": ("SELECT SUM(t.Value) AS total_revenue "
                "FROM [dbo].[tblTransactionalData] t "
                "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
                "WHERE c.Tag LIKE '%, Revenue'")
    },
    {
        "question": "Show total value by account category",
        "sql": ("SELECT c.Tag AS account_tag, "
                "RIGHT(c.Tag, CHARINDEX(',', REVERSE(c.Tag)) - 2) AS category, "
                "SUM(t.Value) AS total_value "
                "FROM [dbo].[tblTransactionalData] t "
                "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
                "GROUP BY c.Tag "
                "ORDER BY total_value DESC")
    },
]


class NLToSQLEngine:
    """Converts natural language questions to SQL using Claude API."""

    def __init__(self, claude_client: ClaudeClient, schema_service: SchemaService,
                 few_shot_examples: list[dict] | None = None):
        self._claude = claude_client
        self._schema_service = schema_service
        self._few_shot = few_shot_examples or DEFAULT_FEW_SHOT

    async def generate_sql(self, question: str,
                           history: list[dict] | None = None) -> str:
        """Generate a SQL query from a natural language question."""
        tables = await self._schema_service.get_tables()
        relevant = self._filter_relevant_tables(question, tables, history)
        schema_text = self._schema_service.format_for_prompt(relevant)

        examples_text = "\n".join(
            FEW_SHOT_EXAMPLES_TEMPLATE.format(**ex) for ex in self._few_shot
        )

        history_text = self._format_history(history) if history else ""

        system_prompt = NL_TO_SQL_SYSTEM_PROMPT.format(
            schema=schema_text,
            examples=examples_text,
            history=history_text,
        )

        raw_sql = await self._claude.complete(
            system_prompt=system_prompt,
            user_message=f"Question: {question}\nSQL:",
            temperature=0.0,
        )

        # Detect out-of-scope response from Claude — may carry an inline answer
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
                    tables_in_prompt=len(relevant))
        return sql

    @staticmethod
    def _format_history(history: list[dict]) -> str:
        """Format conversation history for the prompt."""
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
        for i, turn in enumerate(history, 1):
            rows_note = f"  [{turn['row_count']:,} rows returned]" if turn.get("row_count", 0) > 0 else ""
            tag = "  ← MOST RECENT BASE QUERY" if i == len(history) else ""
            lines.append(f"  Turn {i}: Q: \"{turn['question']}\"")
            lines.append(f"           SQL: {turn['sql']}{rows_note}{tag}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _extract_tables_from_sql(sql: str) -> set[str]:
        """Return lowercase table names referenced in a SQL string."""
        return {m[1].lower() for m in re.findall(r'\[?\w+\]?\.\[?(\w+)\]?', sql)}

    def _filter_relevant_tables(self, question: str,
                                 tables: list[TableMetadata],
                                 history: list[dict] | None = None) -> list[TableMetadata]:
        """Return tables relevant to the question via keyword overlap."""
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
                if col.foreign_key_ref:
                    fk_parts = col.foreign_key_ref.split(".")
                    fk_targets.add(fk_parts[0] if fk_parts else col.foreign_key_ref)

            if q_tokens & (tbl_tokens | col_tokens):
                matched.add(key)

        # Always include tables referenced in history SQL (supports follow-up questions
        # like "now show only X" where the question has no table-matching keywords)
        history_tables: set[str] = set()
        if history:
            for turn in history:
                history_tables.update(self._extract_tables_from_sql(turn.get("sql", "")))

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
        """Strip markdown fencing, comments, trailing semicolons."""
        sql = raw.strip()
        sql = re.sub(r"^```(?:sql)?\s*", "", sql)
        sql = re.sub(r"\s*```$", "", sql)
        sql = sql.rstrip(";").strip()
        sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
        return sql.strip()
