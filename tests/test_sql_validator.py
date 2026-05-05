import pytest
from app.services.sql_validator import SQLValidator
from app.exceptions import SQLValidationError


@pytest.fixture
def validator():
    return SQLValidator()


class TestSQLValidator:
    def test_valid_select(self, validator):
        sql = "SELECT TOP 10 Name, Price FROM [dbo].[Products] ORDER BY Price DESC"
        result = validator.validate(sql)
        assert result.startswith("SELECT")

    def test_valid_select_with_join(self, validator):
        sql = (
            "SELECT p.Name, c.CategoryName "
            "FROM [dbo].[Products] p "
            "JOIN [dbo].[Categories] c ON p.CategoryID = c.ID"
        )
        result = validator.validate(sql)
        assert "JOIN" in result

    def test_valid_cte(self, validator):
        sql = (
            "WITH TopProducts AS ("
            "SELECT TOP 10 Name, Price FROM [dbo].[Products]"
            ") SELECT * FROM TopProducts"
        )
        result = validator.validate(sql)
        assert result.startswith("WITH")

    def test_valid_aggregate(self, validator):
        sql = (
            "SELECT Category, COUNT(*) AS cnt, AVG(Price) AS avg_price "
            "FROM [dbo].[Products] "
            "GROUP BY Category "
            "HAVING COUNT(*) > 5 "
            "ORDER BY cnt DESC"
        )
        assert validator.validate(sql)

    def test_rejects_empty(self, validator):
        with pytest.raises(SQLValidationError, match="Empty"):
            validator.validate("")

    def test_rejects_whitespace_only(self, validator):
        with pytest.raises(SQLValidationError, match="Empty"):
            validator.validate("   ")

    def test_rejects_drop_table(self, validator):
        with pytest.raises(SQLValidationError, match="Forbidden"):
            validator.validate("SELECT 1; DROP TABLE Users")

    def test_rejects_insert(self, validator):
        with pytest.raises(SQLValidationError, match="must begin with SELECT"):
            validator.validate("INSERT INTO Users (Name) VALUES ('hack')")

    def test_rejects_update(self, validator):
        with pytest.raises(SQLValidationError, match="must begin with SELECT"):
            validator.validate("UPDATE Users SET Name = 'hack' WHERE 1=1")

    def test_rejects_delete(self, validator):
        with pytest.raises(SQLValidationError, match="must begin with SELECT"):
            validator.validate("DELETE FROM Users WHERE 1=1")

    def test_rejects_exec(self, validator):
        with pytest.raises(SQLValidationError, match="Forbidden"):
            validator.validate("SELECT 1; EXEC xp_cmdshell 'dir'")

    def test_rejects_xp_cmdshell(self, validator):
        with pytest.raises(SQLValidationError, match="Forbidden"):
            validator.validate("SELECT xp_cmdshell('whoami')")

    def test_rejects_waitfor(self, validator):
        with pytest.raises(SQLValidationError, match="Forbidden"):
            validator.validate("SELECT 1 WAITFOR DELAY '00:00:10'")

    def test_rejects_select_into(self, validator):
        with pytest.raises(SQLValidationError, match="Forbidden"):
            validator.validate("SELECT * INTO NewTable FROM [dbo].[Products]")

    def test_rejects_openrowset(self, validator):
        with pytest.raises(SQLValidationError, match="Forbidden"):
            validator.validate("SELECT * FROM OPENROWSET('SQLOLEDB', 'server')")

    def test_rejects_multiple_statements(self, validator):
        with pytest.raises(SQLValidationError, match="Multiple"):
            validator.validate("SELECT 1; SELECT 2")

    def test_rejects_injection_pattern(self, validator):
        with pytest.raises(SQLValidationError, match="injection"):
            validator.validate("SELECT * FROM Users WHERE Name = '' OR '1' = '1")

    def test_rejects_too_long(self, validator):
        sql = "SELECT " + "a" * 5001
        with pytest.raises(SQLValidationError, match="maximum length"):
            validator.validate(sql)

    def test_strips_trailing_semicolon(self, validator):
        result = validator.validate("SELECT 1;")
        assert not result.endswith(";")

    def test_rejects_non_select_start(self, validator):
        with pytest.raises(SQLValidationError, match="must begin with SELECT"):
            validator.validate("DECLARE @x INT; SELECT @x")
