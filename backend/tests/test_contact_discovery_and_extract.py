"""Phase B1/B2 unit tests for contact page discovery and deterministic extract."""

from __future__ import annotations

from app.services.scraping.contact_location_extract_service import (
    extract_contacts_and_addresses,
)
from app.services.scraping.contact_page_discovery_service import discover_contact_pages
from app.services.scraping.hard_gate_verification_service import (
    HardGateLocation,
    HardGateVerificationInput,
    hard_gate_verification_service,
)


def test_discover_contact_pages_ranks_same_domain_contact_links():
    html = """
    <html><body>
      <a href="/about">About us</a>
      <a href="https://clinic.example/contact">Contact</a>
      <a href="https://other.example/contact">External</a>
      <a href="/standorte">Standorte</a>
      <a href="/blog/news">News</a>
    </body></html>
    """
    links = discover_contact_pages(
        base_url="https://clinic.example/",
        html=html,
        max_links=5,
    )
    urls = [link.url for link in links]
    assert "https://clinic.example/contact" in urls
    assert "https://clinic.example/standorte" in urls
    assert all("other.example" not in url for url in urls)
    assert links[0].score >= links[-1].score


def test_extract_tel_mailto_and_json_ld():
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@type": "LocalBusiness",
      "telephone": "+33 1 23 45 67 89",
      "email": "hello@clinic.example",
      "address": {
        "streetAddress": "10 Rue Example",
        "addressLocality": "Paris",
        "addressCountry": "FR"
      }
    }
    </script>
    </head>
    <body>
      <footer>
        Call <a href="tel:+33199887766">+33 1 99 88 77 66</a>
        or <a href="mailto:admissions@clinic.example">email</a>
      </footer>
    </body></html>
    """
    result = extract_contacts_and_addresses(html, page_url="https://clinic.example/contact")
    phone_values = {item.value for item in result.phones}
    assert "+33 1 23 45 67 89" in phone_values or "+33199887766" in phone_values
    assert any(item.source == "tel_href" for item in result.phones)
    assert any(item.source == "mailto_href" for item in result.emails)
    assert any("Paris" in item.value for item in result.addresses)


def test_branch_phone_gate_requires_every_location_complete():
    result = hard_gate_verification_service.evaluate(
        HardGateVerificationInput(
            target_country_code="FR",
            mission_profile="full_national_census",
            facility_country_containment_status="confirmed_target",
            locations=[
                HardGateLocation(
                    full_address="10 Rue HQ, Paris",
                    country_containment_status="confirmed_target",
                    location_completeness_status="complete",
                    location_gap_reason=None,
                ),
                HardGateLocation(
                    full_address="20 Branch Ave, Lyon",
                    country_containment_status="confirmed_target",
                    location_completeness_status="incomplete",
                    location_gap_reason="phone_missing",
                ),
            ],
            phone_values=["+33122334455"],
            verified_evidence_count=4,
        )
    )
    assert result.publication_class == "review_required"
    assert result.gate_results["location_and_phone_complete"]["status"] == "failed"
    assert result.gate_results["location_and_phone_complete"]["reason"] == "phone_missing"
