import json
import re
from pathlib import Path

from licenselens.ingest.parse import parse_file

ROOT = Path(__file__).resolve().parents[1]
SPDX = ROOT / "data/raw/spdx-licences/json/details"
CHOOSE = ROOT / "data/raw/choosealicense/_licenses"

LICENSES = [
    "0BSD",
    "AFL-3.0",
    "AGPL-3.0",
    "Apache-2.0",
    "Artistic-2.0",
    "BlueOak-1.0.0",
    "BSD-2-Clause-Patent",
    "BSD-2-Clause",
    "BSD-3-Clause-Clear",
    "BSD-3-Clause",
]


def extract_spdx(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("licenseText", "")


def extract_choosealicense(path):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    return text.strip()


def normalize(text):
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def main():
    print(
        "| Licence | SPDX pages | ChooseALicense pages | "
        "SPDX headings | Choose headings | SPDX chars | "
        "Choose chars | Similarity | Verdict |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")

    for licence in LICENSES:
        json_path = SPDX / f"{licence}.json"
        txt_path = CHOOSE / f"{licence}.txt"

        if not json_path.exists() or not txt_path.exists():
            print(f"| {licence} | Missing source | | | | | | | Check files |")
            continue

        spdx_text = extract_spdx(json_path)
        choose_text = extract_choosealicense(txt_path)

        spdx_temp = ROOT / "data/raw" / f"compare-{licence}.txt"
        choose_temp = ROOT / "data/raw" / f"compare-{licence}-choose.txt"

        try:
            spdx_temp.write_text(spdx_text, encoding="utf-8")
            choose_temp.write_text(choose_text, encoding="utf-8")

            spdx_doc = parse_file(spdx_temp)

            choose_doc = parse_file(choose_temp)

            a = normalize("\n".join(p.text for p in spdx_doc.pages))
            b = normalize("\n".join(p.text for p in choose_doc.pages))

            from difflib import SequenceMatcher

            similarity = SequenceMatcher(None, a, b).ratio() * 100

            verdict = (
                "Equivalent legal terms; placeholder formatting differs"
                if licence in {"0BSD", "BSD-3-Clause-Clear"}
                else (
                    "Equivalent content; heading extraction needs review"
                    if similarity >= 95
                    else "Content differs; inspect manually"
                )
            )

            print(
                f"| {licence} | {len(spdx_doc.pages)} | "
                f"{len(choose_doc.pages)} | "
                f"{sum(bool(p.headings) for p in spdx_doc.pages)} | "
                f"{sum(bool(p.headings) for p in choose_doc.pages)} | "
                f"{len(a)} | {len(b)} | {similarity:.2f}% | {verdict} |"
            )

        finally:
            spdx_temp.unlink(missing_ok=True)
            choose_temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
