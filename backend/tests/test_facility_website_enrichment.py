from app.services.scraping.facility_website_enrichment_service import (
    build_official_website_query,
    select_official_website,
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
