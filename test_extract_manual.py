from licenselens.llm.extract import parse_json, validate_metadata

# Test 1: JSON parsing
raw = """
{
    "doc_kind": "license",
    "year": null,
    "language": "en",
    "summary": "A permissive software license.",
    "topics": ["Open Source", "open source"],
    "spdx_id": "MIT",
    "name": "MIT License",
    "osi_approved": true,
    "copyleft_type": "permissive",
    "permissions": ["commercial use"],
    "conditions": ["include copyright notice"],
    "limitations": ["no warranty"]
}
"""

data = parse_json(raw)
print("JSON parsing: SUCCESS")

# Test 2: Pydantic validation
metadata = validate_metadata(data)
print("Pydantic validation: SUCCESS")

print("Licence:", metadata.name)
print("Topics:", metadata.topics)
