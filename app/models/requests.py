# ──────────────────────────────────────────────────────────────────────────────
# REQUEST MODELS — Pydantic validation for incoming API requests.
#
# TOKEN EFFICIENCY KNOBS FOR CALLERS:
#   include_commentary=false → skips Claude API call #2 (saves ~500-1000 tokens)
#     Use this when paginating (page > 1) or when the frontend doesn't need insight.
#   session_id=null → skips conversation history in the NL→SQL prompt
#     Use this for one-off questions that don't need follow-up context.
# ──────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000,
                          description="Natural language question about the data")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=5000)
    # Set to false to skip Claude API call #2 (commentary generation)
    include_commentary: bool = Field(default=True)
    # Client-generated UUID for conversation memory; null = no follow-up support
    session_id: str | None = Field(default=None,
                                   description="Client session ID for conversation memory")
    # Optional override for the commentary system prompt; null = use default
    commentary_prompt: str | None = Field(default=None, max_length=5000,
                                          description="Custom system prompt for commentary generation")
    # Optional override for the NL-to-SQL system prompt; null = use default
    # Must contain {schema}, {examples}, {history} placeholders
    nl_to_sql_prompt: str | None = Field(default=None, max_length=10000,
                                         description="Custom system prompt for NL-to-SQL generation")

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        return v.strip()


class FeedbackRequest(BaseModel):
    query_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None
