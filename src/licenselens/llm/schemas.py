"""Request and response schemas for the LLM API."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    purpose: str = Field(default="general", max_length=50)
    stream: bool = True


class ChatResponse(BaseModel):
    request_id: str
    answer: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_inr: float
    fallback_used: bool
