"""
Tests for Tag-column-based filtering using LIKE '%, Category' patterns.

The tblChartOfAccounts.Tag column stores values like:
  "Loans, Liability"
  "Interest Income, Revenue"
  "Fixed Assets - Development Costs, Asset"
  "KPI - Headcount, Other"

The category is always the LAST part after the final comma, so queries
must use  WHERE c.Tag LIKE '%, Liability'  — NOT  WHERE c.Tag = 'Liability'.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.nl_to_sql import NLToSQLEngine, DEFAULT_FEW_SHOT
from app.services.sql_validator import SQLValidator
from app.prompts.nl_to_sql import NL_TO_SQL_SYSTEM_PROMPT


# ── 1. Prompt template checks ─────────────────────────────────────────────────

class TestTagPromptTemplate:
    """The system prompt must teach Claude the LIKE pattern for Tag filtering."""

    def test_prompt_instructs_like_for_liability(self):
        assert "LIKE '%, Liability'" in NL_TO_SQL_SYSTEM_PROMPT

    def test_prompt_instructs_like_for_asset(self):
        assert "LIKE '%, Asset'" in NL_TO_SQL_SYSTEM_PROMPT

    def test_prompt_instructs_like_for_revenue(self):
        assert "LIKE '%, Revenue'" in NL_TO_SQL_SYSTEM_PROMPT

    def test_prompt_explains_tag_comma_format(self):
        """Prompt must explain that category sits after the last comma."""
        prompt_lower = NL_TO_SQL_SYSTEM_PROMPT.lower()
        assert "tag" in prompt_lower
        # Either "comma" or "last" must appear to explain the format
        assert "comma" in prompt_lower or "last" in prompt_lower

    def test_prompt_recommends_like_over_exact_match(self):
        """Prompt must recommend LIKE as the approach, not exact =.
        If = 'Liability' appears at all it must be in a negative context (e.g. 'not = Liability')."""
        # LIKE must be the explicitly recommended form
        assert "LIKE '%, Liability'" in NL_TO_SQL_SYSTEM_PROMPT

        # If = 'Liability' appears in the prompt it must be preceded by a negative qualifier
        # (the prompt may reference it as an anti-pattern example)
        prompt_lower = NL_TO_SQL_SYSTEM_PROMPT.lower()
        if "= 'liability'" in prompt_lower:
            idx = prompt_lower.index("= 'liability'")
            context_before = prompt_lower[max(0, idx - 40):idx]
            assert any(word in context_before for word in ("not", "never", "instead", "avoid")), (
                "= 'Liability' appears in the prompt without a negative qualifier — "
                "Claude might use exact match instead of LIKE"
            )

    def test_prompt_covers_common_categories(self):
        """All main financial categories must be covered in the prompt."""
        for category in ["Liability", "Asset", "Revenue", "Expense", "Equity", "Other"]:
            assert category in NL_TO_SQL_SYSTEM_PROMPT, (
                f"Category '{category}' not found in system prompt"
            )


# ── 2. Few-shot example checks ────────────────────────────────────────────────

class TestTagFewShotExamples:
    """Few-shot examples must demonstrate LIKE-based Tag filtering."""

    def test_liability_example_exists(self):
        example = next(
            (ex for ex in DEFAULT_FEW_SHOT if "liability" in ex["question"].lower()),
            None,
        )
        assert example is not None, "No liability example found in DEFAULT_FEW_SHOT"

    def test_liability_example_uses_like(self):
        example = next(ex for ex in DEFAULT_FEW_SHOT if "liability" in ex["question"].lower())
        assert "LIKE" in example["sql"], "Liability example must use LIKE, not ="
        assert "'%, Liability'" in example["sql"]

    def test_liability_example_does_not_use_exact_match(self):
        example = next(ex for ex in DEFAULT_FEW_SHOT if "liability" in ex["question"].lower())
        assert "= 'Liability'" not in example["sql"]

    def test_revenue_example_exists(self):
        example = next(
            (ex for ex in DEFAULT_FEW_SHOT if "revenue" in ex["question"].lower()),
            None,
        )
        assert example is not None, "No revenue example found in DEFAULT_FEW_SHOT"

    def test_revenue_example_uses_like(self):
        example = next(ex for ex in DEFAULT_FEW_SHOT if "revenue" in ex["question"].lower())
        assert "LIKE" in example["sql"]
        assert "'%, Revenue'" in example["sql"]

    def test_no_example_uses_exact_tag_match(self):
        """No example should use Tag = 'Category' — it would miss all real rows."""
        categories = ["Liability", "Asset", "Revenue", "Expense", "Equity", "Other"]
        for ex in DEFAULT_FEW_SHOT:
            for cat in categories:
                assert f"Tag = '{cat}'" not in ex["sql"], (
                    f"Example '{ex['question']}' incorrectly uses exact match for '{cat}'"
                )


# ── 3. SQL Validator accepts LIKE-based Tag queries ───────────────────────────

class TestTagFilteringSQLValidator:
    """LIKE-based Tag queries must pass the security validator."""

    def setup_method(self):
        self.validator = SQLValidator()

    def test_like_liability_passes(self):
        sql = (
            "SELECT SUM(t.Value) AS total_liability "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Liability'"
        )
        result = self.validator.validate(sql)
        assert "'%, Liability'" in result

    def test_like_asset_passes(self):
        sql = (
            "SELECT SUM(t.Value) AS total_assets "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Asset'"
        )
        result = self.validator.validate(sql)
        assert "'%, Asset'" in result

    def test_like_revenue_passes(self):
        sql = (
            "SELECT SUM(t.Value) AS total_revenue "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Revenue'"
        )
        assert self.validator.validate(sql) is not None

    def test_like_multiple_categories_passes(self):
        sql = (
            "SELECT c.Tag, SUM(t.Value) AS total_value "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Asset' OR c.Tag LIKE '%, Liability' "
            "GROUP BY c.Tag "
            "ORDER BY total_value DESC"
        )
        assert self.validator.validate(sql) is not None

    def test_like_with_top_and_group_by_passes(self):
        """A realistic top-N + LIKE filter query must pass validation."""
        sql = (
            "SELECT TOP 10 c.AccountDescription, SUM(t.Value) AS total_value "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Liability' "
            "GROUP BY c.AccountDescription "
            "ORDER BY total_value DESC"
        )
        assert self.validator.validate(sql) is not None


# ── 4. NLToSQLEngine builds prompt with LIKE instructions ─────────────────────

class TestTagFilteringNLEngine:
    """NLToSQLEngine must embed LIKE instructions in the prompt sent to Claude."""

    @pytest.mark.asyncio
    async def test_prompt_contains_like_instruction(self):
        mock_claude = AsyncMock()
        mock_claude.complete = AsyncMock(
            return_value=(
                "SELECT SUM(t.Value) AS total_liability "
                "FROM [dbo].[tblTransactionalData] t "
                "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
                "WHERE c.Tag LIKE '%, Liability'"
            )
        )
        mock_schema = AsyncMock()
        mock_schema.get_tables = AsyncMock(return_value=[])
        mock_schema.format_for_prompt = lambda tables: "[dbo].[tblChartOfAccounts]: Tag (nvarchar)"

        engine = NLToSQLEngine(mock_claude, mock_schema)
        await engine.generate_sql("What is the total liability?")

        system_prompt = mock_claude.complete.call_args.kwargs["system_prompt"]
        assert "LIKE '%, Liability'" in system_prompt
        assert "Tag" in system_prompt

    @pytest.mark.asyncio
    async def test_like_sql_returned_unchanged(self):
        """SQL with LIKE Tag filter must survive the clean-up step intact."""
        expected = (
            "SELECT SUM(t.Value) AS total_liability "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Liability'"
        )
        mock_claude = AsyncMock()
        mock_claude.complete = AsyncMock(return_value=expected)
        mock_schema = AsyncMock()
        mock_schema.get_tables = AsyncMock(return_value=[])
        mock_schema.format_for_prompt = lambda tables: "schema"

        engine = NLToSQLEngine(mock_claude, mock_schema)
        result = await engine.generate_sql("What is the total liability?")

        assert "'%, Liability'" in result
        assert "= 'Liability'" not in result

    @pytest.mark.asyncio
    async def test_like_revenue_sql_returned_unchanged(self):
        """SQL with LIKE Revenue filter must survive the clean-up step intact."""
        expected = (
            "SELECT SUM(t.Value) AS total_revenue "
            "FROM [dbo].[tblTransactionalData] t "
            "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
            "WHERE c.Tag LIKE '%, Revenue'"
        )
        mock_claude = AsyncMock()
        mock_claude.complete = AsyncMock(return_value=expected)
        mock_schema = AsyncMock()
        mock_schema.get_tables = AsyncMock(return_value=[])
        mock_schema.format_for_prompt = lambda tables: "schema"

        engine = NLToSQLEngine(mock_claude, mock_schema)
        result = await engine.generate_sql("What is the total revenue?")

        assert "'%, Revenue'" in result

    @pytest.mark.asyncio
    async def test_like_with_markdown_fencing_cleaned(self):
        """LIKE Tag SQL inside markdown fencing must be cleaned and returned correctly."""
        mock_claude = AsyncMock()
        mock_claude.complete = AsyncMock(
            return_value=(
                "```sql\n"
                "SELECT SUM(t.Value) AS total_assets "
                "FROM [dbo].[tblTransactionalData] t "
                "JOIN [dbo].[tblChartOfAccounts] c ON t.AccountID = c.AccountID "
                "WHERE c.Tag LIKE '%, Asset';\n"
                "```"
            )
        )
        mock_schema = AsyncMock()
        mock_schema.get_tables = AsyncMock(return_value=[])
        mock_schema.format_for_prompt = lambda tables: "schema"

        engine = NLToSQLEngine(mock_claude, mock_schema)
        result = await engine.generate_sql("What are the total assets?")

        assert "'%, Asset'" in result
        assert "```" not in result   # fencing removed
        assert result.endswith("'%, Asset'")  # no trailing semicolon
