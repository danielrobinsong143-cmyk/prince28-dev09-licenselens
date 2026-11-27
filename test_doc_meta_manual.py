from pydantic import ValidationError

from licenselens.llm.doc_meta import DocMeta

base = {
    "doc_kind": "licence",
    "language": "en",
    "summary": "Test",
}

tests = [
    (
        "9 topics",
        {**base, "topics": [f"topic{i}" for i in range(9)]},
    ),
    (
        "Unknown field",
        {**base, "unexpected": "value"},
    ),
]

for name, data in tests:
    try:
        DocMeta(**data)
        print(name, "ACCEPTED (UNEXPECTED)")
    except ValidationError:
        print(name, "REJECTED (EXPECTED)")
