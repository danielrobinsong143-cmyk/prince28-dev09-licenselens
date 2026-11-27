"""Validated metadata contract for LicenseLens documents."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocMeta(BaseModel):
    """Structured metadata extracted from a source document."""

    model_config = ConfigDict(extra="forbid", strict=True)

    doc_kind: str
    year: int | None = None
    language: str
    summary: str
    topics: list[str] = Field(default_factory=list, max_length=8)

    spdx_id: str | None = None
    name: str | None = None
    osi_approved: bool | None = None
    copyleft_type: str | None = None
    permissions: list[str] = Field(default_factory=list, max_length=8)
    conditions: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, topics: list[str]) -> list[str]:
        """Lowercase topics and remove duplicates while preserving order."""
        return list(dict.fromkeys(topic.strip().lower() for topic in topics))
