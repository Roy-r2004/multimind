"""Regression tests for the "addiction treatment need not be primary" rule
being consistently present across every prompt that produces or reads
`addiction_focus_confirmed` / `addiction_treatment_mission` evidence.

A prior audit found that only the keep/drop prompts had been updated with
this clarification when the "primary program" eligibility requirement was
removed — the four other prompts that set the field compute_client_eligibility
actually gates on (maps_place_enricher, maps_structured_classifier,
maps_primary_extractor, maps_sonar_classifier) still described the field
with zero explanatory text, which could silently reinstate the old
requirement upstream of correct backend logic. These tests render every
affected template with representative context and assert the clarifying
rule is present, so a future edit cannot silently drop it from one template
while keeping it in the others.
"""

from __future__ import annotations

import json

import pytest

from app.llm.prompt_engine import get_prompt_engine

# Phrases that must appear (case-insensitive) in every prompt that sets or
# reads the addiction-focus evidence field — each prompt may phrase it
# differently, but all three ideas must be present somewhere in the text.
_REQUIRED_IDEAS = (
    # 1. Not required to be primary/only program.
    ("primary", "only"),
    # 2. A general psychiatric/multidisciplinary facility can still qualify.
    ("psychiatric", "multidisciplinary"),
    # 3. Mentioning/referring is not sufficient evidence.
    ("referral", "mention"),
)


def _assert_clarified(rendered: str, template_name: str) -> None:
    lowered = rendered.lower()
    for options in _REQUIRED_IDEAS:
        assert any(option in lowered for option in options), (
            f"{template_name} is missing clarifying language for one of "
            f"{options} — the 'not required to be primary' rule may have "
            f"been dropped from this prompt."
        )


@pytest.mark.parametrize(
    "template_name,context",
    [
        (
            "scraping/maps_keep_drop.j2",
            dict(
                country_code="FI",
                country_name="Finland",
                state_code="",
                state_name="",
                facility_json=json.dumps({"name": "Example Clinic"}),
                crawl_excerpt="",
            ),
        ),
        (
            "scraping/maps_keep_drop_sonar.j2",
            dict(
                country_code="FI",
                country_name="Finland",
                state_code="",
                state_name="",
                facility_json=json.dumps({"name": "Example Clinic"}),
                crawl_excerpt="",
            ),
        ),
        (
            "scraping/maps_place_enricher.j2",
            dict(
                country_code="FI",
                country_name="Finland",
                addiction_taxonomy_json=json.dumps(["Alcohol", "Opioids"]),
                facilities_json=json.dumps([{"place_id": "p1", "name": "Example Clinic"}]),
            ),
        ),
        (
            "scraping/maps_structured_classifier.j2",
            dict(
                country_code="FI",
                country_name="Finland",
                facility_json=json.dumps({"name": "Example Clinic"}),
                crawl_excerpt="",
            ),
        ),
        (
            "scraping/maps_primary_extractor.j2",
            dict(
                country_code="FI",
                country_name="Finland",
                facility_json=json.dumps({"name": "Example Clinic"}),
                crawl_excerpt="",
            ),
        ),
        (
            "scraping/maps_sonar_classifier.j2",
            dict(
                country_code="FI",
                country_name="Finland",
                facility_json=json.dumps({"name": "Example Clinic"}),
            ),
        ),
    ],
)
def test_addiction_focus_prompt_renders_and_states_not_required_to_be_primary(
    template_name: str, context: dict
) -> None:
    rendered = get_prompt_engine().render(template_name, **context)
    assert rendered.strip()
    _assert_clarified(rendered, template_name)


def test_keep_drop_and_classification_prompts_agree_on_the_rule() -> None:
    """All six prompts must phrase the rule consistently enough that a human
    auditor (or the model) cannot read one prompt as contradicting another."""
    engine = get_prompt_engine()
    rendered_keep_drop = engine.render(
        "scraping/maps_keep_drop.j2",
        country_code="FI",
        country_name="Finland",
        state_code="",
        state_name="",
        facility_json=json.dumps({"name": "Example Clinic"}),
        crawl_excerpt="",
    )
    rendered_enricher = engine.render(
        "scraping/maps_place_enricher.j2",
        country_code="FI",
        country_name="Finland",
        addiction_taxonomy_json=json.dumps(["Alcohol"]),
        facilities_json=json.dumps([{"place_id": "p1"}]),
    )
    assert "does not have to be" in rendered_keep_drop.lower()
    enricher_lower = " ".join(rendered_enricher.lower().split())
    assert "not that addiction is its primary" in enricher_lower
