import re

from generate_incidents import TEMPLATES, generate

from incident_triage.models import ImpactCategory

INTERNAL_IDENTIFIER = re.compile(
    r"\b[\w-]+\.(?:internal|local|corp|com|net)\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|@"
)


def test_same_seed_gives_identical_incidents():
    assert generate(50, seed=42) == generate(50, seed=42)


def test_customer_visible_fields_have_no_internal_identifiers():
    for incident in generate(300, seed=42):
        for text in (incident.title, incident.description, incident.resolution_summary):
            assert not INTERNAL_IDENTIFIER.search(text), (incident.id, text)


def test_templates_cover_every_specific_impact_category():
    used = {t["impact"] for templates in TEMPLATES.values() for t in templates}

    assert used >= set(ImpactCategory) - {ImpactCategory.OTHER}
