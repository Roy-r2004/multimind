from unittest.mock import AsyncMock

import pytest

from app.services.scraping.execution_orchestrator import (
    run_standalone_facility_website_enrichment,
)
from app.services.scraping.facility_website_enrichment_service import (
    build_official_website_query,
    facility_website_enrichment_service,
    select_official_website,
    website_needs_enrichment,
)
from app.services.scraping.search_providers.base import SearchProviderResult


def result(rank: int, url: str, title: str, snippet: str = "") -> SearchProviderResult:
    return SearchProviderResult(rank=rank, url=url, title=title, snippet=snippet)


def test_query_uses_exact_facility_name_and_geography():
    query = build_official_website_query(
        name="Republican Scientific and Practical Center for Mental Health",
        city="Minsk",
        country_name="Belarus",
    )

    assert '"Republican Scientific and Practical Center for Mental Health"' in query
    assert "Minsk" in query
    assert "Belarus" in query
    assert "official website" in query


def test_selection_rejects_directories_social_media_documents_and_list_pages():
    candidates = [
        result(
            1,
            "https://example.gov.by/registry/list-of-health-organizations",
            "List of health organizations",
        ),
        result(2, "https://facebook.com/centre-alpha", "Centre Alpha"),
        result(3, "https://example.by/centre-alpha.pdf", "Centre Alpha PDF"),
        result(4, "https://directory.example/clinics/centre-alpha", "Centre Alpha"),
        result(5, "https://www.docfinder.at/arzt/centre-alpha", "Centre Alpha DocFinder"),
        result(6, "https://www.herold.at/firmeneintrag/centre-alpha", "Centre Alpha Herold"),
    ]

    assert (
        select_official_website(
            facility_name="Centre Alpha",
            city="Minsk",
            country_name="Belarus",
            results=candidates,
        )
        is None
    )


def test_selection_prefers_strong_name_matching_official_homepage():
    candidates = [
        result(
            1,
            "https://health-directory.example/belarus/centre-alpha",
            "Centre Alpha | Health Directory",
            "Directory listing for clinics in Belarus",
        ),
        result(
            2,
            "https://centre-alpha.by/services",
            "Centre Alpha — rehabilitation and mental health",
            "Official site of Centre Alpha in Minsk, Belarus",
        ),
        result(
            3,
            "https://news.example/article-centre-alpha",
            "Centre Alpha opens a new building",
        ),
    ]

    selected = select_official_website(
        facility_name="Centre Alpha",
        city="Minsk",
        country_name="Belarus",
        results=candidates,
    )

    assert selected is not None
    assert selected.url == "https://centre-alpha.by/"


def test_selection_returns_none_when_results_are_ambiguous():
    candidates = [
        result(1, "https://alpha.example/", "Alpha health services"),
        result(2, "https://centre.example/", "Treatment centre in Belarus"),
    ]

    assert (
        select_official_website(
            facility_name="Centre Alpha",
            city=None,
            country_name="Belarus",
            results=candidates,
        )
        is None
    )


def test_selection_rejects_district_gov_cms_and_department_pages():
    candidates = [
        result(
            1,
            "http://bykhov.gov.by/index.php/ytz/item/1693-internat",
            "Быхаўскі псіханеўралагічны дом-інтэрнат",
            "District portal page about the facility",
        ),
        result(
            2,
            "http://chausy.gov.by/2013-01-16-08-44-45/itemlist/category/132-guso",
            "Расцянскі дом-інтэрнат",
            "Item list category on municipal site",
        ),
        result(
            3,
            "https://ncgb.by/ob-uchrezhdenii/podrazdeleniya/psihonevrologicheskij-dispanser/",
            "Психоневрологический диспансер",
            "Hospital department page",
        ),
        result(
            4,
            "https://consilium.by/uslugi/psihoterapiya/",
            "Консилиум Медикум",
            "Service page for psychotherapy",
        ),
        result(
            5,
            "https://sanatorii.by/by/?Berezka",
            "санаторый Бярозка",
            "Sanatorium catalog aggregator",
        ),
    ]

    assert (
        select_official_website(
            facility_name="Быхаўскі псіханеўралагічны дом-інтэрнат",
            city="Bykhov",
            country_name="Belarus",
            results=candidates,
        )
        is None
    )


def test_selection_keeps_dedicated_facility_domain_over_parent_hospital_page():
    candidates = [
        result(
            1,
            "https://hospital.by/ob-uchrezhdenii/podrazdeleniya/narcology/",
            "Бобруйский наркологический диспансер",
            "Parent hospital subdivision page",
        ),
        result(
            2,
            "https://narcology.by/contacts",
            "Бобруйский наркологический диспансер — official site",
            "Official website of the dispensary in Belarus",
        ),
    ]

    selected = select_official_website(
        facility_name="Бобруйский наркологический диспансер",
        city="Bobruisk",
        country_name="Belarus",
        results=candidates,
    )

    assert selected is not None
    assert selected.url == "https://narcology.by/"


def test_website_needs_enrichment_for_district_gov_and_department_urls():
    assert website_needs_enrichment(
        "http://bykhov.gov.by/index.php/ytz/item/1693-internat"
    )
    assert website_needs_enrichment(
        "https://ncgb.by/ob-uchrezhdenii/podrazdeleniya/psihonevrologicheskij-dispanser/"
    )
    assert website_needs_enrichment("https://sanatorii.by/by/?Berezka")
    assert not website_needs_enrichment("https://narcology.by/")
    assert not website_needs_enrichment("https://mentalhealth.by/")


@pytest.mark.asyncio
async def test_standalone_cleanup_enriches_websites_before_ai_review(monkeypatch):
    enrich = AsyncMock(return_value={"enriched": 7})
    monkeypatch.setattr(
        facility_website_enrichment_service,
        "enrich_execution",
        enrich,
    )

    summary = await run_standalone_facility_website_enrichment(
        execution_id="exec-1",
        organization_id="org-1",
        max_facilities=25,
    )

    assert summary == {"enriched": 7}
    enrich.assert_awaited_once_with(
        None,
        organization_id="org-1",
        execution_id="exec-1",
        max_facilities=25,
    )
