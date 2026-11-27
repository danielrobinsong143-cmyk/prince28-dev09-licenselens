"""Route a question before answering it.

docs       the documents can answer it
personal   it needs live information or the user's own private data
off_topic  it is not about this product's subject
"""

import re
from collections.abc import Awaitable, Callable

from licenselens.llm.gateway import (
    LLMGateway,
    LLMProviderAttempt,
    LLMResponse,
)

LABELS = ("docs", "personal", "off_topic")


def build_messages(prompt: str, question: str) -> list[dict[str, str]]:
    """Put instructions in the system message and user text inside tags."""
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"<question>\n{question.strip()}\n</question>"},
    ]


def parse_label(reply: str, labels: tuple[str, ...] = LABELS) -> str:
    """Extract one valid label. Anything ambiguous is invalid."""
    text = re.sub(r"off[\s-]+topic", "off_topic", reply.lower())

    found = {label for label in labels if re.search(rf"(?<![a-z_]){label}(?![a-z_])", text)}

    return found.pop() if len(found) == 1 else "invalid"


async def classify(
    gateway: LLMGateway,
    prompt: str,
    version: str,
    question: str,
    on_attempt: Callable[[LLMProviderAttempt], Awaitable[None]] | None = None,
) -> tuple[str, LLMResponse]:
    """Classify a question and identify the prompt version used."""
    result = await gateway.generate(
        build_messages(prompt, question),
        purpose="classify",
        prompt_version=version,
        temperature=0,
        on_attempt=on_attempt,
    )

    return parse_label(result.content), result


def accuracy(rows: list[dict]) -> float:
    """Calculate classification accuracy from labelled evaluation rows."""
    return sum(row["got"] == row["label"] for row in rows) / len(rows) if rows else 0.0


def find_leaks(prompt: str, questions: list[str]) -> list[str]:
    """Find test questions that already appear in the prompt."""
    text = " ".join(prompt.lower().split())

    return [question for question in questions if " ".join(question.lower().split()) in text]
