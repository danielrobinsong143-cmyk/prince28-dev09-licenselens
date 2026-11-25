"""Check whether Gemini and Groq API keys are working."""

import sys
import time

import httpx

from licenselens.config import get_settings

HINTS = {
    400: "The request or API key may be wrong.",
    401: "The API key is wrong or was deleted.",
    403: "This key is not allowed to use this model.",
    404: "Model not found. Check the model name.",
    429: "Rate limit reached. Wait and try again.",
    503: "Model temporarily overloaded. Retry later.",
}


def ping(name: str, base_url: str, key: str, model: str) -> bool:

    if not key:
        print(f"{name:7} NO KEY   Add it to .env")
        return False

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Reply with one word: pong",
            }
        ],
        "temperature": 0,
    }

    started = time.perf_counter()

    try:
        response = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=30,
        )

    except httpx.TransportError as exc:
        print(f"{name:7} FAILED {type(exc).__name__}: Check your internet.")
        return False

    ms = int((time.perf_counter() - started) * 1000)

    if response.status_code != 200:
        hint = HINTS.get(
            response.status_code,
            "Provider error. Try again later.",
        )

        print(f"{name:7} FAILED HTTP {response.status_code}: {hint}")

        print(response.text[:300])
        return False

    data = response.json()

    answer = (data["choices"][0]["message"].get("content") or "").strip()

    usage = data.get("usage") or {}

    print(
        f"{name:7} OK {model} said {answer!r} "
        f"in {ms} ms, "
        f"tokens in={usage.get('prompt_tokens', '?')} "
        f"out={usage.get('completion_tokens', '?')}"
    )

    return True


def main() -> int:

    s = get_settings()

    results = [
        ping(
            "gemini",
            s.llm_primary_base_url,
            s.gemini_api_key,
            s.llm_primary_model,
        ),
        ping(
            "groq",
            s.llm_fallback_base_url,
            s.groq_api_key,
            s.llm_fallback_model,
        ),
    ]

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
