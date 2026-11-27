from scripts.verify_extraction_metadata import PARSER_FIELDS, summarize_extraction_rows


def test_verifier_checks_complete_docmeta_and_parser_fields_without_values():
    metadata = {
        "doc_kind": "license",
        "year": None,
        "language": "en",
        "summary": "A licence document.",
        "topics": [],
        "spdx_id": None,
        "name": None,
        "osi_approved": None,
        "copyleft_type": None,
        "permissions": [],
        "conditions": [],
        "limitations": [],
        **dict.fromkeys(PARSER_FIELDS, "preserved"),
    }

    result = summarize_extraction_rows([("private-id", metadata)])

    assert result == {
        "checked": 1,
        "complete_fields": 1,
        "valid_docmeta": 1,
        "parser_metadata_present": 1,
    }
    assert "private-id" not in str(result)
    assert "A licence document" not in str(result)


def test_verifier_reports_incomplete_or_invalid_metadata():
    metadata = {"doc_kind": "license", "language": "en", "summary": "Text"}

    result = summarize_extraction_rows([("doc-1", metadata)])

    assert result == {
        "checked": 1,
        "complete_fields": 0,
        "valid_docmeta": 1,
        "parser_metadata_present": 0,
    }
