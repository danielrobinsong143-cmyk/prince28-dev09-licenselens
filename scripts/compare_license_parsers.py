import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from licenselens.ingest.parse import headings_in, parse_file

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
    for licence in LICENSES:
        json_path = SPDX / f"{licence}.json"
        txt_path = CHOOSE / f"{licence}.txt"

        if not json_path.exists() or not txt_path.exists():
            print(f"\n{licence}: Missing source")
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

            spdx_found, _ = headings_in("\n".join(p.text for p in spdx_doc.pages))
            choose_found, _ = headings_in("\n".join(p.text for p in choose_doc.pages))

            spdx_headings = [title for _, title in spdx_found]
            choose_headings = [title for _, title in choose_found]

            similarity = SequenceMatcher(None, a, b).ratio() * 100

            print(f"\n{'=' * 65}")
            print(f"LICENCE: {licence}")
            print(f"Similarity: {similarity:.2f}%")
            print(f"SPDX pages: {len(spdx_doc.pages)}")
            print(f"ChooseALicense pages: {len(choose_doc.pages)}")

            print(f"\nSPDX headings ({len(spdx_headings)}):")
            for level, title in spdx_found:
                print(f"  Level {level}: {title}")

            print(f"\nChooseALicense headings ({len(choose_headings)}):")
            for level, title in choose_found:
                print(f"  Level {level}: {title}")

            print("\nSPDX heading paths:")
            for page in spdx_doc.pages:
                if page.headings:
                    print(f"  Page {page.page_no}: {page.headings}")

            print("\nChooseALicense heading paths:")
            for page in choose_doc.pages:
                if page.headings:
                    print(f"  Page {page.page_no}: {page.headings}")

        finally:
            spdx_temp.unlink(missing_ok=True)
            choose_temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
