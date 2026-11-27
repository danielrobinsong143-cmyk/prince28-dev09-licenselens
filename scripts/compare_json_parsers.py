"""Compare current plain-JSON parsing with licenceText-aware parsing.

Run with: uv run python scripts/compare_json_parsers.py
The script reads the ten named SPDX licence files and prints aggregate metrics only.
It does not access the database or write source files.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from licenselens.ingest.parse import (
    ParsedDoc,
    ParseError,
    clean_text,
    finish,
    headings_in,
    parse_file,
    split_pages,
)

ROOT = Path(__file__).resolve().parents[1]
SPDX_DETAILS = ROOT / "data/raw/spdx-licences/json/details"
LICENCE_FILES = (
    "3D-Slicer-1.0.json",
    "AGPL-3.0.json",
    "Apache-2.0.json",
    "Artistic-2.0.json",
    "EPL-2.0.json",
    "GPL-3.0.json",
    "LGPL-3.0.json",
    "MPL-1.1.json",
    "MPL-2.0.json",
    "ODbL-1.0.json",
)


def parse_license_text_json(path: Path) -> ParsedDoc:
    """Parse the SPDX `licenseText` value through existing plain-text stages."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ParseError("invalid_json", type(error).__name__) from error

    if not isinstance(payload, dict):
        raise ParseError("invalid_json", "root is not an object")

    licence_text = payload.get("licenseText")
    if not isinstance(licence_text, str) or not licence_text.strip():
        raise ParseError("missing_license_text")

    # Apply the existing text cleanup, heading detection, page splitting and
    # ParsedDoc construction without changing ingestion's current JSON behavior.
    text = clean_text(licence_text)
    return finish("plain-licenseText", "text", path.stem, split_pages(text))


def metrics(parser, path: Path) -> dict:
    """Return safe structural metrics, or the parser's machine-readable failure."""
    try:
        document = parser(path)
    except ParseError as error:
        return {"ok": False, "reason": error.reason}

    text = "\n".join(page.text for page in document.pages)
    headings, _ = headings_in(text)
    return {
        "ok": True,
        "text": text,
        "chars": len(text),
        "pages": len(document.pages),
        "headings": [title for _, title in headings],
        "tables": sum(page.tables for page in document.pages),
    }


def normalized_similarity(first: str, second: str) -> float:
    """Compare text while ignoring whitespace and case."""

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().lower()

    return SequenceMatcher(None, normalize(first), normalize(second)).ratio()


def evaluate(paths: tuple[Path, ...] | list[Path]) -> list[dict]:
    """Compare both parsing approaches for each supplied JSON file."""
    rows = []
    for path in paths:
        current = metrics(parse_file, path)
        aware = metrics(parse_license_text_json, path)
        similarity = (
            normalized_similarity(current["text"], aware["text"])
            if current["ok"] and aware["ok"]
            else None
        )
        rows.append(
            {
                "file": path.name,
                "current": current,
                "license_text": aware,
                "similarity": similarity,
            }
        )
    return rows


def _cell(result: dict) -> str:
    if not result["ok"]:
        return f"FAILED ({result['reason']})"
    return (
        f"{result['chars']:,} chars; {result['pages']} pages; "
        f"{len(result['headings'])} headings; {result['tables']} tables"
    )


def main() -> None:
    missing = [name for name in LICENCE_FILES if not (SPDX_DETAILS / name).is_file()]
    if missing:
        raise SystemExit(f"Missing selected licence files: {', '.join(missing)}")

    rows = evaluate([SPDX_DETAILS / name for name in LICENCE_FILES])
    print("| Licence | Current plain JSON | licenseText-aware | Text similarity |")
    print("|---|---|---|---:|")
    for row in rows:
        similarity = "n/a" if row["similarity"] is None else f"{row['similarity']:.2%}"
        print(
            f"| {row['file'][:-5]} | {_cell(row['current'])} | "
            f"{_cell(row['license_text'])} | {similarity} |"
        )


if __name__ == "__main__":
    main()
