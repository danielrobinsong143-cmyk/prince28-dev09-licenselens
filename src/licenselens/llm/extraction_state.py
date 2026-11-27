"""Local, content-free failure tracking for resumable extraction runs."""

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from licenselens.llm.extract import LLMOutputTruncatedError
from licenselens.llm.gateway import (
    ProviderBadRequestError,
    ProviderQuotaExhaustedError,
    ProviderRateLimitedError,
)

DEFAULT_FAILURES_PATH = Path("data/extraction_failures.json")
_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def safe_failure_reason(error: Exception) -> str:
    """Classify a failure without retaining exception text or document content."""
    if isinstance(error, ProviderQuotaExhaustedError):
        return "provider_quota_exhausted"
    if isinstance(error, ProviderRateLimitedError):
        return "provider_rate_limited"
    if isinstance(error, ProviderBadRequestError):
        return "http_status_400"
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_status_{error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "provider_timeout"
    if isinstance(error, httpx.TransportError):
        return "provider_transport_error"
    if isinstance(error, LLMOutputTruncatedError):
        return "output_truncated"
    if isinstance(error, ValidationError):
        return "metadata_validation_failed"
    if isinstance(error, LookupError):
        return "metadata_persistence_failed"
    if isinstance(error, ValueError):
        return "invalid_model_output"
    return "unexpected_error"


def load_failures(path: Path = DEFAULT_FAILURES_PATH) -> dict[str, dict]:
    """Load the local failure index; invalid state fails closed before extraction."""
    if not path.exists():
        return {}

    try:
        failures = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Extraction failure state is unreadable or invalid JSON.") from error

    if not isinstance(failures, dict) or any(
        not isinstance(document_id, str)
        or not isinstance(record, dict)
        or not isinstance(record.get("attempts"), int)
        or not isinstance(record.get("reason"), str)
        for document_id, record in failures.items()
    ):
        raise ValueError("Extraction failure state has an invalid structure.")
    return failures


def record_failure(
    path: Path,
    document_id: str,
    reason: str,
    *,
    attempted_at: datetime | None = None,
) -> dict[str, dict]:
    """Record a safe reason and increment the document's failure count atomically."""
    if not _SAFE_REASON.fullmatch(reason):
        raise ValueError("Failure reason must be a safe classification code.")

    failures = load_failures(path)
    previous = failures.get(document_id, {})
    failures[document_id] = {
        "attempts": previous.get("attempts", 0) + 1,
        "reason": reason,
        "last_attempt_at": (attempted_at or datetime.now(UTC)).isoformat(),
    }
    _write_failures(path, failures)
    return failures


def clear_failure(path: Path, document_id: str) -> dict[str, dict]:
    """Remove stale failure state after confirmed metadata persistence."""
    if not path.exists():
        return {}
    failures = load_failures(path)
    failures.pop(document_id, None)
    _write_failures(path, failures)
    return failures


def _write_failures(path: Path, failures: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".extraction-failures-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(failures, temporary_file, ensure_ascii=False, indent=2, sort_keys=True)
            temporary_file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
