"""Compare all classifier prompt versions against the labelled test set."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from licenselens.config import get_settings
from licenselens.db.session import make_engine, make_sessionmaker
from licenselens.llm.classify import LABELS, accuracy, classify, find_leaks
from licenselens.llm.gateway import create_gateway
from licenselens.llm.prompts import read_prompt, versions
from licenselens.llm.recorder import LLMCallRecorder

TESTSET = Path("eval/prompt_testset.jsonl")
CACHE = Path("eval/cache/classify.json")
NOTES = Path("NOTES.md")


def load_testset():
    examples = []

    for number, line in enumerate(TESTSET.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue

        row = json.loads(line)

        if row["label"] not in LABELS:
            raise ValueError(f"Invalid label on line {number}: {row['label']}")

        if "TODO" in row["question"]:
            raise ValueError(f"TODO found on line {number}")

        examples.append({"n": number, **row})

    if len(examples) != 20:
        raise ValueError(f"Expected 20 questions, found {len(examples)}")
    if len({example["question"] for example in examples}) != len(examples):
        raise ValueError("Duplicate questions found in the evaluation test set")

    return examples


def cache_key(prompt: str, question: str, model: str) -> str:
    """Cache an answer only for the exact prompt, question, and model combination."""
    identity = f"{model}\n\n{prompt}\n\n{question}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def valid_cache_entry(entry, model: str | None = None) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("got"), str)
        and entry["got"] in {*LABELS, "invalid"}
        and type(entry.get("tokens_in")) is int
        and entry["tokens_in"] >= 0
        and isinstance(entry.get("reply"), str)
        and (model is None or entry.get("model") == model)
    )


def write_table(rows, total):
    table = [
        "## D09 - Prompt comparison",
        "",
        "| Prompt version | Correct | Accuracy | Invalid replies | Average input tokens |",
        "|---|---:|---:|---:|---:|",
    ]

    for row in rows:
        table.append(
            f"| {row['version']} | {row['correct']}/{total} "
            f"| {row['accuracy']:.0%} | {row['invalid']} "
            f"| {row['tokens_in']} |"
        )

    text = NOTES.read_text(encoding="utf-8") if NOTES.exists() else "# LicenseLens Notes\n"

    marker = "## D09 - Prompt comparison"

    if marker in text:
        text = text[: text.index(marker)].rstrip() + "\n\n"

    NOTES.write_text(
        text.rstrip() + "\n\n" + "\n".join(table) + "\n",
        encoding="utf-8",
    )


async def run(args):
    settings = get_settings()
    examples = load_testset()

    prompt_dir = Path("prompts")
    prompt_versions = versions(prompt_dir, "classify")

    if len(prompt_versions) < 3:
        raise ValueError("At least three classify prompt versions are required.")

    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)

    recorder = LLMCallRecorder(sessionmaker, settings)
    gateway = create_gateway(settings)

    cache = {}
    if not args.no_cache and CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            raise ValueError("Prompt evaluation cache must contain a JSON object.")

    if not args.no_cache:
        CACHE.parent.mkdir(parents=True, exist_ok=True)

    model = gateway.primary.model
    print(f"Evaluation model: {model}")

    rows = []
    asked = 0

    try:
        for version in prompt_versions:
            prompt = read_prompt(prompt_dir, version)

            for leak in find_leaks(prompt, [example["question"] for example in examples]):
                print(f"WARNING: Possible test-set leak in {version}: {leak}")

            results = []

            for example in examples:
                key = cache_key(prompt, example["question"], model)

                if key not in cache or not valid_cache_entry(cache[key], model):
                    got, reply = await classify(
                        gateway,
                        prompt,
                        version,
                        example["question"],
                        on_attempt=recorder.callback(
                            purpose="classify",
                            prompt_version=version,
                        ),
                    )

                    cache[key] = {
                        "got": got,
                        "reply": reply.content[:120],
                        "tokens_in": reply.tokens_in,
                        "model": reply.model,
                    }

                    if not args.no_cache:
                        CACHE.write_text(
                            json.dumps(cache, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                    asked += 1
                    await asyncio.sleep(60 / args.rpm)

                results.append({**example, **cache[key]})

            for result in results:
                if result["got"] != result["label"]:
                    print(
                        f"{version} missed line {result['n']}: "
                        f"wanted {result['label']}, got {result['got']}"
                    )

            rows.append(
                {
                    "version": version,
                    "correct": sum(r["got"] == r["label"] for r in results),
                    "accuracy": accuracy(results),
                    "invalid": sum(r["got"] == "invalid" for r in results),
                    "tokens_in": round(sum(r["tokens_in"] for r in results) / len(results)),
                }
            )

    finally:
        await engine.dispose()

    write_table(rows, len(examples))

    print("\nPROMPT COMPARISON")
    print("-----------------")

    for row in rows:
        print(f"{row['version']:<15} {row['correct']:>2}/20  {row['accuracy']:.0%}")

    print(f"\nModel calls made now: {asked}")
    print("Results saved in NOTES.md")
    print("Cached responses saved in eval/cache/")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpm", type=float, default=12)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    if args.rpm <= 0:
        parser.error("--rpm must be greater than zero")

    asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
