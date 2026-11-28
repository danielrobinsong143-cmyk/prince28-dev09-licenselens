import json

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


def test_comparison_configures_ten_distinct_licenses():
    assert len(LICENSES) == 10
    assert len(set(LICENSES)) == 10


def test_all_ten_source_files_exist_in_corpus():
    for lid in LICENSES:
        spdx_file = SPDX_DIR / f"{lid}.json"
        choose_file = CHOOSE_DIR / f"{lid.lower()}.txt"
        assert spdx_file.is_file(), f"Missing SPDX file for {lid}: {spdx_file}"
        assert choose_file.is_file(), f"Missing ChooseALicense file for {lid}: {choose_file}"


def test_extract_spdx_text_valid_and_missing(tmp_path):
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(json.dumps({"licenseText": "Valid legal text."}), encoding="utf-8")
    assert extract_spdx_text(valid_file) == "Valid legal text."

    empty_file = tmp_path / "empty.json"
    empty_file.write_text(json.dumps({"name": "No text"}), encoding="utf-8")
    with pytest.raises(ParseError) as exc:
        extract_spdx_text(empty_file)
    assert exc.value.reason == "missing_license_text"


def test_extract_choosealicense_text_strips_frontmatter(tmp_path):
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


def test_parsers_extract_structure_from_both_sources(tmp_path):
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


def test_lgpl_content_difference_investigation():
    spdx_file = SPDX_DIR / "LGPL-3.0.json"
    choose_file = CHOOSE_DIR / "lgpl-3.0.txt"

    spdx_text = extract_spdx_text(spdx_file)
    choose_text = extract_choosealicense_text(choose_file)

    # SPDX embeds full GPL-3.0 text
    assert "GNU GENERAL PUBLIC LICENSE" in spdx_text
    # ChooseALicense contains only the standalone additional permissions
    assert "GNU GENERAL PUBLIC LICENSE" not in choose_text
    assert len(spdx_text) > len(choose_text) * 4

    row = evaluate_pair("LGPL-3.0", spdx_file, choose_file)
    assert len(row["headings_a"]) == 25  # 7 LGPL + 18 GPL
    assert len(row["headings_b"]) == 7  # 7 LGPL only
    assert "SPDX embeds full GPL-3.0 base text" in row["verdict"]


def test_evaluate_all_generates_valid_table():
    rows = evaluate_all()
    assert len(rows) == 10

    table = generate_markdown_table(rows)
    assert "<!-- parsers:start -->" in table
    assert "<!-- parsers:end -->" in table
    for lid in LICENSES:
        assert f"**{lid}**" in table

    # Verify all 10 have non-zero headings in both sources
    for r in rows:
        assert len(r["headings_a"]) >= 9
        assert len(r["headings_b"]) >= 7
        assert r["pages_a"] > 0
        assert r["pages_b"] > 0
        assert r["chars_a"] > 0
        assert r["chars_b"] > 0
        assert r["time_a"] > 0
        assert r["time_b"] > 0

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
