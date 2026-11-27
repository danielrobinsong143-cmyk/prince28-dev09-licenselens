import json

import pytest

from licenselens.ingest.parse import ParseError, parse_file
from scripts.compare_json_parsers import (
    LICENCE_FILES,
    evaluate,
    metrics,
    parse_license_text_json,
)


def test_evaluation_uses_ten_named_corpus_licences():
    assert len(LICENCE_FILES) == 10
    assert len(set(LICENCE_FILES)) == 10


def test_license_text_aware_parser_finds_structure_in_json(tmp_path):
    path = tmp_path / "sample.json"
    path.write_text(
        json.dumps(
            {
                "licenseText": (
                    "# Sample License\n\n"
                    "Permission is granted to use this software.\n\n"
                    "## Conditions\n\n"
                    "| Item | Rule |\n|---|---|\n| Notice | Keep it |"
                )
            }
        ),
        encoding="utf-8",
    )

    current = metrics(parse_file, path)
    aware = metrics(parse_license_text_json, path)

    assert current["ok"] and aware["ok"]
    assert current["headings"] == []
    assert aware["headings"] == ["Sample License", "Conditions"]
    assert current["tables"] == 0
    assert aware["tables"] == 1
    assert aware["pages"] == 1


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("{broken", "invalid_json"),
        (json.dumps({"name": "No license text"}), "missing_license_text"),
        (json.dumps({"licenseText": "  "}), "missing_license_text"),
    ],
)
def test_license_text_aware_parser_reports_failures(tmp_path, payload, reason):
    path = tmp_path / "broken.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ParseError) as error:
        parse_license_text_json(path)

    assert error.value.reason == reason


def test_evaluation_reports_failed_parser_without_text_content(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "missing"}), encoding="utf-8")

    [row] = evaluate([path])

    assert row["current"]["ok"] is True
    assert row["license_text"] == {"ok": False, "reason": "missing_license_text"}
    assert row["similarity"] is None
