import json
from pathlib import Path

import pytest

from licenselens.ingest.parse import ParseError
from scripts.compare_license_parsers import (
    CHOOSE_DIR,
    LICENSES,
    SPDX_DIR,
    evaluate_all,
    evaluate_pair,
    extract_choosealicense_text,
    extract_spdx_text,
    generate_markdown_table,
    normalize,
    parse_choosealicense_doc,
    parse_spdx_doc,
)

SAMPLE_TOPICS = [
    "Definitions and interpretation of terms",
    "Grant of copyright and patent permissions",
    "Redistribution and notice conditions",
    "Source code disclosure obligations",
    "Disclaimer of warranty and fitness",
    "Limitation of liability and damages",
    "Termination and breach provisions",
    "Trademarks and branding restrictions",
    "Governing law and jurisdiction",
    "Miscellaneous and severability terms",
]


@pytest.fixture
def synthetic_corpus_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary directory structure mimicking the 10 paired licence files."""
    spdx_dir = tmp_path / "spdx"
    choose_dir = tmp_path / "choosealicense"
    spdx_dir.mkdir()
    choose_dir.mkdir()

    for lid in LICENSES:
        sections = [
            f"{i + 1}. {SAMPLE_TOPICS[i]}\n\nClause text for {SAMPLE_TOPICS[i].lower()}."
            for i in range(len(SAMPLE_TOPICS))
        ]
        text = f"{lid} Official Legal Terms\n\n" + "\n\n".join(sections)

        spdx_file = spdx_dir / f"{lid}.json"
        spdx_file.write_text(json.dumps({"licenseText": text}), encoding="utf-8")

        choose_file = choose_dir / f"{lid.lower()}.txt"
        choose_file.write_text(
            f"---\ntitle: {lid}\nspdx-id: {lid}\n---\n\n{text}",
            encoding="utf-8",
        )

    return spdx_dir, choose_dir


def test_comparison_configures_ten_distinct_licenses():
    assert len(LICENSES) == 10
    assert len(set(LICENSES)) == 10


@pytest.mark.skipif(
    not (SPDX_DIR.is_dir() and CHOOSE_DIR.is_dir()),
    reason="Local raw corpus is gitignored and not present in CI environment",
)
def test_all_ten_source_files_exist_in_corpus():
    for lid in LICENSES:
        spdx_file = SPDX_DIR / f"{lid}.json"
        choose_file = CHOOSE_DIR / f"{lid.lower()}.txt"
        assert spdx_file.is_file(), f"Missing SPDX file for {lid}: {spdx_file}"
        assert choose_file.is_file(), f"Missing ChooseALicense file for {lid}: {choose_file}"


def test_extract_spdx_text_valid_and_missing(tmp_path: Path):
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(json.dumps({"licenseText": "Valid legal text."}), encoding="utf-8")
    assert extract_spdx_text(valid_file) == "Valid legal text."

    empty_file = tmp_path / "empty.json"
    empty_file.write_text(json.dumps({"name": "No text"}), encoding="utf-8")
    with pytest.raises(ParseError) as exc:
        extract_spdx_text(empty_file)
    assert exc.value.reason == "missing_license_text"


def test_extract_choosealicense_text_strips_frontmatter(tmp_path: Path):
    txt_file = tmp_path / "sample.txt"
    txt_file.write_text(
        "---\ntitle: Sample License\nspdx-id: Sample\n---\n\nSample legal text.\n",
        encoding="utf-8",
    )
    assert extract_choosealicense_text(txt_file) == "Sample legal text."

    empty_body = tmp_path / "empty.txt"
    empty_body.write_text("---\ntitle: Only Header\n---\n\n", encoding="utf-8")
    with pytest.raises(ParseError) as exc:
        extract_choosealicense_text(empty_body)
    assert exc.value.reason == "empty_license_body"


def test_parsers_extract_structure_from_both_sources(tmp_path: Path):
    spdx_file = tmp_path / "Test-1.0.json"
    spdx_file.write_text(
        json.dumps(
            {
                "licenseText": (
                    "Test License Version 1.0\n\n"
                    "1. Definitions\n\n"
                    "Terms defined here.\n\n"
                    "2. Grant\n\n"
                    "Rights granted here."
                )
            }
        ),
        encoding="utf-8",
    )

    choose_file = tmp_path / "test-1.0.txt"
    choose_file.write_text(
        (
            "---\ntitle: Test\n---\n\n"
            "Test License Version 1.0\n\n"
            "1. Definitions\n\n"
            "Terms defined here.\n\n"
            "2. Grant\n\n"
            "Rights granted here."
        ),
        encoding="utf-8",
    )

    doc_a = parse_spdx_doc(spdx_file, "Test-1.0")
    doc_b = parse_choosealicense_doc(choose_file, "Test-1.0")

    assert doc_a.parser == "spdx-json-text"
    assert doc_b.parser == "choosealicense-md"
    assert len(doc_a.pages) == 1
    assert len(doc_b.pages) == 1

    row = evaluate_pair("Test-1.0", spdx_file, choose_file)
    assert row["similarity"] == 100.0
    assert row["headings_a"] == ["1. Definitions", "2. Grant"]
    assert row["headings_b"] == ["1. Definitions", "2. Grant"]


def test_lgpl_content_difference_investigation(tmp_path: Path):
    """Test LGPL-3.0 investigation logic using controlled fixture content."""
    lgpl_headings = [
        "0. Additional Definitions",
        "1. Exception to Section 3 of the GNU GPL",
        "2. Conveying Modified Versions",
        "3. Object Code Incorporating Material from Library Header Files",
        "4. Combined Works",
        "5. Combined Libraries",
        "6. Revised Versions of the GNU Lesser General Public License",
    ]
    gpl_headings = [
        "0. Definitions",
        "1. Source Code",
        "2. Basic Permissions",
        "3. Protecting Users Legal Rights From Anti-Circumvention Law",
        "4. Conveying Verbatim Copies",
        "5. Conveying Modified Source Versions",
        "6. Conveying Non-Source Forms",
        "7. Additional Terms",
        "8. Termination",
        "9. Acceptance Not Required for Having Copies",
        "10. Automatic Licensing of Downstream Recipients",
        "11. Patents",
        "12. No Surrender of Others Freedom",
        "13. Use with the GNU Affero General Public License",
        "14. Revised Versions of this License",
        "15. Disclaimer of Warranty",
        "16. Limitation of Liability",
        "17. Interpretation of Sections 15 and 16",
    ]

    # ChooseALicense contains only the standalone LGPL additional permissions (7 headings)
    choose_body = "GNU LESSER GENERAL PUBLIC LICENSE Version 3\n\n" + "\n\n".join(
        f"{h}\n\nClause content for {h}." for h in lgpl_headings
    )
    choose_file = tmp_path / "lgpl-3.0.txt"
    choose_content = (
        "---\ntitle: GNU Lesser General Public License v3.0\nspdx-id: LGPL-3.0\n---\n\n"
        + choose_body
    )
    choose_file.write_text(choose_content, encoding="utf-8")

    # SPDX embeds the full GNU General Public License base text (18 clauses) alongside the LGPL text
    spdx_body = (
        "GNU LESSER GENERAL PUBLIC LICENSE Version 3\n\n"
        + "\n\n".join(f"{h}\n\nClause content for {h}." for h in lgpl_headings)
        + "\n\n======================================================\n\n"
        + "GNU GENERAL PUBLIC LICENSE Version 3\n\n"
        + "\n\n".join(f"{h}\n\nBase GPL clause content for {h}." for h in gpl_headings)
    )
    spdx_file = tmp_path / "LGPL-3.0.json"
    spdx_file.write_text(json.dumps({"licenseText": spdx_body}), encoding="utf-8")

    spdx_text = extract_spdx_text(spdx_file)
    choose_text = extract_choosealicense_text(choose_file)

    # SPDX embeds full GPL-3.0 text
    assert "GNU GENERAL PUBLIC LICENSE" in spdx_text
    # ChooseALicense contains only the standalone additional permissions
    assert "GNU GENERAL PUBLIC LICENSE" not in choose_text
    assert len(spdx_text) > len(choose_text)

    row = evaluate_pair("LGPL-3.0", spdx_file, choose_file)
    assert len(row["headings_a"]) == 25  # 7 LGPL + 18 GPL
    assert len(row["headings_b"]) == 7  # 7 LGPL only
    assert "SPDX embeds full GPL-3.0 base text" in row["verdict"]


def test_evaluate_all_generates_valid_table(synthetic_corpus_dirs: tuple[Path, Path]):
    """Test evaluate_all() and table formatting using synthetic paired files."""
    spdx_dir, choose_dir = synthetic_corpus_dirs
    rows = evaluate_all(spdx_dir=spdx_dir, choose_dir=choose_dir)
    assert len(rows) == 10

    table = generate_markdown_table(rows)
    assert "<!-- parsers:start -->" in table
    assert "<!-- parsers:end -->" in table
    for lid in LICENSES:
        assert f"**{lid}**" in table

    # Verify all 10 have valid headings, pages, chars, and execution times
    for r in rows:
        assert len(r["headings_a"]) >= 10
        assert len(r["headings_b"]) >= 10
        assert r["pages_a"] > 0
        assert r["pages_b"] > 0
        assert r["chars_a"] > 0
        assert r["chars_b"] > 0
        assert r["time_a"] > 0
        assert r["time_b"] > 0
        assert r["similarity"] > 0
        assert r["verdict"]

    apache_row = next(r for r in rows if r["id"] == "Apache-2.0")
    assert "4 pages each" in apache_row["verdict"]

    epl_row = next(r for r in rows if r["id"] == "EPL-2.0")
    assert "1-page difference (6 vs 5)" in epl_row["verdict"]


def test_evaluate_all_missing_files_raises(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError) as exc_info:
        evaluate_all(spdx_dir=empty_dir, choose_dir=empty_dir)
    assert "Missing SPDX file" in str(exc_info.value)


@pytest.mark.skipif(
    not (SPDX_DIR.is_dir() and CHOOSE_DIR.is_dir()),
    reason="Local raw corpus only present in local development environment",
)
def test_real_corpus_ten_files_metrics_and_verdicts():
    """Verify metrics and verdicts against the real local corpus when available."""
    rows = evaluate_all()
    assert len(rows) == 10

    for r in rows:
        assert len(r["headings_a"]) >= 9
        assert len(r["headings_b"]) >= 7
        assert r["pages_a"] > 0
        assert r["pages_b"] > 0

    apache_row = next(r for r in rows if r["id"] == "Apache-2.0")
    assert apache_row["pages_a"] == 4
    assert apache_row["pages_b"] == 4
    assert "4 pages each" in apache_row["verdict"]

    epl_row = next(r for r in rows if r["id"] == "EPL-2.0")
    assert epl_row["pages_a"] == 6
    assert epl_row["pages_b"] == 5
    assert "1-page difference (6 vs 5)" in epl_row["verdict"]


def test_normalize_helper():
    assert normalize("  Apache   License\n\nVersion  2.0 ") == "apache license version 2.0"
