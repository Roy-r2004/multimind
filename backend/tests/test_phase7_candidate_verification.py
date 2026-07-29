from app.services.scraping.facility_candidate_verification import (
    CandidateFinalStatus,
    CountryDecision,
    CountryEvidence,
    exact_identity_keys,
    final_status,
    normalize_email,
    normalize_address,
    normalize_city_region,
    normalize_facility_type,
    normalize_name,
    normalize_phone,
    normalize_website,
    probable_duplicate_score,
    verify_country,
)


def test_country_physical_evidence_is_authoritative() -> None:
    inside = verify_country("AT", [CountryEvidence("full_address", "Vienna, Austria", "address")])
    outside = verify_country("AT", [CountryEvidence("explicit_physical_location", "Germany", "page")])
    assert inside.decision == CountryDecision.INSIDE
    assert outside.decision == CountryDecision.OUTSIDE


def test_weak_signals_cannot_reject() -> None:
    result = verify_country("AT", [
        CountryEvidence("domain_suffix", ".de", "url"),
        CountryEvidence("page_language", "German", "html"),
        CountryEvidence("phone_prefix", "+49", "phone"),
        CountryEvidence("hosting_country", "Germany", "network"),
    ])
    assert result.decision == CountryDecision.UNCERTAIN
    assert final_status(result, has_name=True, has_physical_locator=False)[0] == CandidateFinalStatus.NEEDS_REVIEW


def test_contact_and_identity_normalization_is_deterministic() -> None:
    assert normalize_email(" Info@Example.COM ") == "info@example.com"
    assert normalize_phone("+43 (1) 234 567") == "+431234567"
    assert normalize_website("WWW.Example.COM/path/") == (
        "https://www.example.com/path", "example.com"
    )
    assert exact_identity_keys(
        name="Example Clinic", address="1 Main St", website="example.com",
        phone=None, email=None,
    ) == exact_identity_keys(
        name=" example clinic ", address="1 main st.", website="https://www.example.com/",
        phone=None, email=None,
    )
    root = exact_identity_keys(
        name="Example Clinic", address="1 Main St", website="EXAMPLE.COM/",
        phone=None, email=None,
    )
    www_root = exact_identity_keys(
        name="Example Clinic", address="1 Main St", website="https://www.example.com",
        phone=None, email=None,
    )
    assert root == www_root
    assert "website:https://example.com/" in root
    profile_a = exact_identity_keys(
        name="Example Clinic", address="1 Main St",
        website="https://www.example.com/locations/vienna/",
        phone=None, email=None,
    )
    profile_b = exact_identity_keys(
        name="Example Clinic", address="1 Main St",
        website="https://example.com/locations/graz",
        phone=None, email=None,
    )
    assert "website:https://example.com/locations/vienna" in profile_a
    assert "website:https://example.com/locations/graz" in profile_b
    assert profile_a != profile_b
    branch_a = exact_identity_keys(
        name="Example Clinic Vienna", address="1 Main St",
        website="https://example.com", phone=None, email=None,
    )
    branch_b = exact_identity_keys(
        name="Example Clinic Graz", address="9 River Rd",
        website="https://www.example.com/", phone=None, email=None,
    )
    assert branch_a != branch_b
    assert normalize_name("  Clínique—Alpha ") == "clinique alpha"
    assert normalize_address("  1 Main St., Vienna; ") == "1 main st., vienna"
    assert normalize_city_region(" Île-de-France ") == "ile de france"
    assert normalize_facility_type("Rehabilitation Centre") == "rehabilitation_center"


def test_probable_duplicate_is_reviewable() -> None:
    score, reasons = probable_duplicate_score(
        {"name": "Example Clinic", "city": "Vienna", "website": "example.com"},
        {"name": "EXAMPLE CLINIC", "city": "Vienna", "website": "www.example.com"},
    )
    assert score >= .55
    assert "same_normalized_name" in reasons


def test_similar_name_alone_does_not_merge_distinct_branches() -> None:
    score, reasons = probable_duplicate_score(
        {"name": "Alpha Recovery", "city": "Vienna", "address": "1 Main Street"},
        {"name": "Alpha Recovery", "city": "Graz", "address": "9 River Road"},
    )
    assert reasons == ["same_normalized_name"]
    assert score < .55
