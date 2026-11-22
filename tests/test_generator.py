import re

from generate_incidents import generate

INTERNAL_IDENTIFIER = re.compile(
    r"\b[\w-]+\.(?:internal|local|corp|com|net)\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|@"
)


def test_same_seed_gives_identical_incidents():
    assert generate(50, seed=42) == generate(50, seed=42)


def test_customer_visible_fields_have_no_internal_identifiers():
    for incident in generate(300, seed=42):
        for text in (incident.title, incident.description, incident.resolution_summary):
            assert not INTERNAL_IDENTIFIER.search(text), (incident.id, text)
