"""Unit tests for Phase A country containment assessment."""

from app.services.scraping.country_containment_service import assess_country_containment


def test_execution_scope_alone_is_uncertain():
    result = assess_country_containment(
        target_country_code="FR",
        country_source="execution_scope",
    )
    assert result.status == "uncertain"
    assert result.publication_class == "review_required"


def test_extracted_country_mismatch_is_outside():
    result = assess_country_containment(
        target_country_code="FR",
        extracted_country_code="DE",
        extracted_country_raw="Germany",
        country_source="extracted_evidence",
    )
    assert result.status == "confirmed_outside"
    assert result.publication_class == "excluded"


def test_monaco_france_neighbor_address_is_outside():
    result = assess_country_containment(
        target_country_code="FR",
        address_text="12 Avenue des Spélugues, Monaco",
        country_source="execution_scope",
    )
    assert result.status == "confirmed_outside"
    assert result.publication_class == "excluded"


def test_austria_address_with_local_phone_is_target():
    result = assess_country_containment(
        target_country_code="AT",
        address_text="Something in Austria, Wien",
        phone_values=["+43 1 2345678"],
        country_source="execution_scope",
    )
    assert result.status == "confirmed_target"
    assert result.publication_class == "verified"


def test_german_phone_on_austria_mission_without_address_is_outside():
    result = assess_country_containment(
        target_country_code="AT",
        phone_values=["+49 89 123456"],
        country_source="execution_scope",
    )
    assert result.status == "confirmed_outside"
    assert result.publication_class == "excluded"


def test_matching_extracted_country_with_address_is_target():
    result = assess_country_containment(
        target_country_code="EE",
        extracted_country_code="EE",
        address_text="Tallinn, Estonia",
        country_source="extracted_evidence",
    )
    assert result.status == "confirmed_target"
    assert result.publication_class == "verified"
