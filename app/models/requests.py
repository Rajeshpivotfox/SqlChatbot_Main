from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000,
                          description="Natural language question about the data")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=5000)
    include_commentary: bool = Field(default=True)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        return v.strip()


class FeedbackRequest(BaseModel):
    query_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None
