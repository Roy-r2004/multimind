"""Phase 5B deterministic directory parsing tests; synthetic and network-free."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.models import CrawlEdgeRelationshipType
from app.services.scraping.directory_expansion_service import (
    PARSER_REGISTRY,
    DirectoryIdentificationOutcome,
    DirectoryParserKind,
    PreparedDirectoryContent,
    compute_prepared_content_fingerprint,
    identify_and_prepare_directory,
    persist_prepared_expansion,
)
from app.services.scraping.phase5_contracts import directory_observation_fingerprint
from app.services.scraping.phase5_contracts import Phase5WorkKind, prepare_phase5_job
from app.services.scraping.discovery_url_service import compute_canonical_url_hash

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def source(*, content=None, structured=None, records=(), content_type="text/html",
           hint=DirectoryParserKind.AUTO):
    fingerprint = compute_prepared_content_fingerprint(
        content_type=content_type, decoded_content=content,
        structured_json=structured, embedded_json_records=records)
    return PreparedDirectoryContent(
        job_id="job", organization_id="org", execution_id="exec",
        claim_token="claim", parent_crawl_node_id="directory-node",
        input_retrieval_result_id="retrieval-result",
        input_source_document_id="source-document",
        input_retrieval_method="http_retrieval",
        listing_page_original_url="https://docs.python.org/list",
        listing_page_canonical_url="https://docs.python.org/list",
        content_type=content_type, decoded_content=content,
        structured_json=structured, embedded_json_records=records,
        parser_hint=hint, content_fingerprint=fingerprint, observed_at=NOW)


def test_html_cards_extract_explicit_fields_and_relationships():
    result = identify_and_prepare_directory(source(content="""
      <div class="facility-card" data-address="Main St" data-phone="+1 555">
        <a href="/profiles/one">Facility One</a>
        <a class="official website" href="https://www.python.org/?utm_source=x">Website</a>
      </div>
      <div class="facility-card"><a href="/profiles/two">Facility Two Branch</a></div>
    """))
    assert result.outcome is DirectoryIdentificationOutcome.SUPPORTED
    assert [x.displayed_facility_name for x in result.listings] == [
        "Facility One", "Facility Two Branch"]
    assert result.listings[0].canonical_profile_url == "https://docs.python.org/profiles/one"
    assert result.listings[0].canonical_official_website_url == "https://www.python.org/"
    assert "utm_source=x" in result.listings[0].observed_official_website_url
    assert result.listings[0].displayed_address == "Main St"
    assert result.listings[1].canonical_official_website_url is None


def test_table_and_list_registry_selection():
    table = identify_and_prepare_directory(source(content="""
      <table><tr data-city="Beirut"><td><a href="/p/1">One</a></td></tr></table>
    """))
    listing = identify_and_prepare_directory(source(content="""
      <ol class="directory-list"><li><a href="/p/1">One</a></li><li><a href="/p/2">Two</a></li></ol>
    """))
    assert table.metadata.parser_kind is DirectoryParserKind.HTML_TABLE
    assert table.listings[0].displayed_city == "Beirut"
    assert listing.metadata.parser_kind is DirectoryParserKind.HTML_LIST
    assert len(listing.listings) == 2


def test_json_collection_and_nested_public_results():
    result = identify_and_prepare_directory(source(
        structured={"data": {"results": [
            {"name": "One", "profileUrl": "/p/1", "address": "A"},
            {"name": "Two", "officialWebsite": "https://pypi.org"},
        ]}}, content_type="application/json"))
    assert result.outcome is DirectoryIdentificationOutcome.SUPPORTED
    assert result.metadata.parser_kind is DirectoryParserKind.JSON_COLLECTION
    assert result.listings[0].structured_payload_reference == "$.data.results[0]"
    assert result.listings[1].canonical_profile_url is None


def test_json_ld_item_list():
    result = identify_and_prepare_directory(source(
        structured={"@context": "https://schema.org", "@type": "ItemList",
                    "itemListElement": [
                        {"position": 1, "item": {"@type": "MedicalOrganization",
                                                "name": "One", "url": "/p/1",
                                                "telephone": "123"}}
                    ]},
        content_type="application/ld+json"))
    assert result.metadata.parser_kind is DirectoryParserKind.JSON_LD_ITEM_LIST
    assert result.listings[0].displayed_phone == "123"


def test_embedded_json_and_map_payload():
    embedded = identify_and_prepare_directory(source(content="""
      <div class="directory-card"></div>
      <script type="application/json">{"markers":[{"name":"Map One","url":"/p/1"}]}</script>
    """))
    mapped = identify_and_prepare_directory(source(
        structured={"markers": [{"name": "Map One", "url": "/p/1"}]},
        content_type="application/json",
        hint=DirectoryParserKind.MAP_LISTING_PAYLOAD))
    assert any(x.displayed_facility_name == "Map One" for x in embedded.listings)
    assert mapped.listings[0].extraction_method is DirectoryParserKind.MAP_LISTING_PAYLOAD


def test_empty_unsupported_malformed_and_unknown_hint_fail_closed():
    empty = identify_and_prepare_directory(source(content="<table></table>"))
    unsupported = identify_and_prepare_directory(source(content="<main>About us</main>"))
    malformed = identify_and_prepare_directory(source(
        content="{bad", content_type="application/json"))
    assert empty.outcome is DirectoryIdentificationOutcome.EMPTY
    assert unsupported.outcome is DirectoryIdentificationOutcome.NOT_A_DIRECTORY
    assert malformed.outcome is DirectoryIdentificationOutcome.MALFORMED
    with pytest.raises(ValidationError):
        source(content="x", hint="future_parser")


def test_relative_urls_canonicalization_unsafe_rejection_and_no_fabrication():
    result = identify_and_prepare_directory(source(structured={"items": [
        {"name": "Relative", "profileUrl": "../profile?id=1&utm_source=x"},
        {"name": "Unsafe", "profileUrl": "http://127.0.0.1/private"},
        {"name": "No Website"},
    ]}, content_type="application/json"))
    assert result.listings[0].canonical_profile_url == "https://docs.python.org/profile?id=1"
    assert "utm_source=x" in result.listings[0].observed_profile_url
    assert result.listings[1].canonical_profile_url is None
    assert result.listings[2].canonical_official_website_url is None


def test_observed_urls_remove_secrets_but_keep_pagination_parameters():
    result = identify_and_prepare_directory(source(structured={"items": [{
        "name": "Safe", "profileUrl": "/p/1?ToKeN=hidden&page=2&API_KEY=nope"
    }]}, content_type="application/json"))
    observed = result.listings[0].observed_profile_url
    assert observed == "https://docs.python.org/p/1?page=2"
    assert result.listings[0].canonical_profile_url.endswith("/p/1?page=2")
    persisted_values = result.listings[0].model_dump_json()
    assert "hidden" not in persisted_values
    assert "nope" not in persisted_values


def test_branch_identity_stability_and_separation():
    common = dict(
        organization_id="o", execution_id="e", parent_directory_node_id="n",
        listing_page_url="https://docs.python.org/list",
        profile_url="https://docs.python.org/profile",
        displayed_facility_name="Facility")
    a = directory_observation_fingerprint(**common, displayed_address="Branch A", listing_rank=1)
    replay = directory_observation_fingerprint(
        **common, displayed_address="Branch A", listing_rank=1)
    branch = directory_observation_fingerprint(
        **common, displayed_address="Branch B", listing_rank=2)
    assert a == replay
    assert a != branch


def test_duplicate_listing_replay_is_removed_without_merging_branches():
    result = identify_and_prepare_directory(source(structured={"items": [
        {"name": "One", "url": "/p/1", "position": 1},
        {"name": "One", "url": "/p/1", "position": 1},
        {"name": "One", "url": "/p/1", "address": "Other", "position": 2},
    ]}, content_type="application/json"))
    assert len(result.listings) == 2
    assert result.metadata.duplicate_item_count == 1


def test_canonical_node_identity_hash_and_cache_keys_are_url_bound():
    first = "https://docs.python.org/profile/one"
    replay = "https://docs.python.org/profile/one"
    distinct = "https://docs.python.org/profile/two"
    first_hash = compute_canonical_url_hash(first)
    replay_hash = compute_canonical_url_hash(replay)
    distinct_hash = compute_canonical_url_hash(distinct)
    assert first_hash == replay_hash
    assert first_hash != distinct_hash
    assert ("org", "exec", first_hash) == ("org", "exec", replay_hash)
    assert len({
        ("org", "exec", first_hash),
        ("org", "exec", replay_hash),
        ("org", "exec", distinct_hash),
    }) == 2


def test_pagination_load_more_structured_api_and_infinite_markers():
    html = identify_and_prepare_directory(source(content="""
      <div class="facility-card"><a href="/p/1">One</a></div>
      <nav class="pagination"><a rel="next" href="/list?page=2">Next</a></nav>
      <button class="load-more" data-url="/list?cursor=abc">More</button>
      <div class="infinite-scroll"></div>
    """))
    structured = identify_and_prepare_directory(source(
        structured={"items": [{"name": "One"}], "apiUrl": "/api/list?offset=2"},
        content_type="application/json"))
    assert html.metadata.has_pagination
    assert html.metadata.has_load_more
    assert html.metadata.has_infinite_scroll
    assert any(x.requires_browser_interaction and x.canonical_url is None
               for x in html.continuations)
    assert structured.continuations[0].relationship is CrawlEdgeRelationshipType.STRUCTURED_API


def test_self_loop_and_repeated_continuations_are_ignored():
    result = identify_and_prepare_directory(source(content="""
      <div class="facility-card"><a href="/p/1">One</a></div>
      <a rel="next" href="/list">Same</a>
      <a rel="next" href="/next">Next</a><a rel="next" href="/next">Next</a>
    """))
    assert len(result.continuations) == 1


def test_input_size_type_and_payload_exclusivity_guards():
    with pytest.raises(ValidationError):
        source(content="x", structured={"items": []})
    with pytest.raises(ValidationError):
        source(content=None, structured=None)
    with pytest.raises(ValidationError):
        source(content="x" * 2_000_001)
    with pytest.raises(ValidationError):
        PreparedDirectoryContent(
            **source(content="x").model_dump(exclude={"content_fingerprint"}),
            content_fingerprint="z" * 64)


@pytest.mark.asyncio
async def test_stale_claim_returns_before_any_write():
    prepared = identify_and_prepare_directory(source(structured={
        "items": [{"name": "One", "url": "/p/1"}]},
        content_type="application/json"))

    class Session:
        async def scalar(self, statement):
            return None

    result = await persist_prepared_expansion(Session(), prepared)
    assert result.outcome == "stale_claim"
    assert result.observation_count == result.node_count == result.edge_count == 0


def test_parser_registry_is_typed_and_no_external_call_implementation():
    assert set(PARSER_REGISTRY) == set(DirectoryParserKind) - {DirectoryParserKind.AUTO}
    source_text = (Path(__file__).resolve().parents[1] / "app" / "services" /
                   "scraping" / "directory_expansion_service.py").read_text()
    for forbidden in ("import httpx", "import requests", "import socket",
                      "from playwright", "import firecrawl", "import serper"):
        assert forbidden not in source_text.lower()


def test_public_identification_metadata_contains_no_sensitive_listing_values():
    result = identify_and_prepare_directory(source(content="""
      <div class="facility-card" data-address="Secret Address" data-phone="555">
        <a href="/p/1">One</a>
      </div>
    """))
    public = result.metadata.model_dump_json()
    assert "Secret Address" not in public
    assert "555" not in public
    assert "https://" not in public
    assert "claim" not in public


def test_032_is_linear_and_model_supports_phase5b_edges():
    migration = (Path(__file__).resolve().parents[1] / "alembic" / "versions" /
                 "032_phase5b_directory_graph_relationships.py").read_text()
    assert 'revision = "032"' in migration
    assert 'down_revision = "031"' in migration
    assert CrawlEdgeRelationshipType.DIRECTORY_TO_OFFICIAL_SITE.value == (
        "directory_to_official_site")
    assert CrawlEdgeRelationshipType.PAGINATION.value == "pagination"
    assert CrawlEdgeRelationshipType.LOAD_MORE.value == "load_more"
    assert CrawlEdgeRelationshipType.STRUCTURED_API.value == "structured_api"


def test_directory_job_identity_is_bound_to_retrieved_representation():
    common = dict(
        organization_id="o", execution_id="e", crawl_node_id="n",
        original_url="https://docs.python.org/list",
        source_classification="directory",
        work_kind=Phase5WorkKind.DIRECTORY_EXPANSION,
        selected_tool="directory_expansion", requested_at=NOW,
        input_content_fingerprint="a" * 64)
    http = prepare_phase5_job(
        **common, input_retrieval_result_id="http-result",
        input_source_document_id="locator-a",
        input_retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL)
    replay = prepare_phase5_job(
        **common, input_retrieval_result_id="http-result",
        input_source_document_id="locator-b",
        input_retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL)
    rendered = prepare_phase5_job(
        **common, input_retrieval_result_id="firecrawl-result",
        input_source_document_id="locator-c",
        input_retrieval_method=Phase5WorkKind.FIRECRAWL_RETRIEVAL)
    assert http.fingerprint == replay.fingerprint
    assert http.fingerprint != rendered.fingerprint


def test_10001_entries_are_processed_in_continuation_slices_without_gaps():
    payload = {"items": [
        {"name": f"Facility {index}", "url": f"/profile/{index}"}
        for index in range(10001)
    ]}
    durable = source(structured=payload, content_type="application/json")
    ranks, cursor, states = [], 0, set()
    while True:
        prepared = identify_and_prepare_directory(durable.model_copy(update={
            "next_entry_ordinal": cursor, "entries_completed": cursor}))
        ranks.extend(item.listing_rank for item in prepared.listings)
        states.add(prepared.parser_state_fingerprint)
        cursor = prepared.slice_end_ordinal
        if not prepared.has_more_entries:
            break
    assert [len(ranks[index:index + 2000])
            for index in range(0, len(ranks), 2000)] == [2000] * 5 + [1]
    assert ranks == list(range(1, 10002))
    assert cursor == 10001
    assert len(states) == 1


def test_content_fingerprint_mismatch_fails_at_trusted_boundary():
    valid = source(structured={"items": [{"name": "One", "url": "/p/1"}]},
                   content_type="application/json")
    with pytest.raises(ValidationError, match="fingerprint"):
        PreparedDirectoryContent.model_validate({
            **valid.model_dump(), "structured_json": {
                "items": [{"name": "Changed", "url": "/p/1"}]}})


def test_static_shell_can_fallback_to_distinct_rendered_representation():
    shell = identify_and_prepare_directory(source(
        content='<div id="root"></div><script src="/app.js"></script>'))
    rendered = identify_and_prepare_directory(source(
        structured={"items": [{"name": "Rendered", "url": "/p/1"}]},
        content_type="application/json"))
    assert shell.outcome is DirectoryIdentificationOutcome.REQUIRES_MANAGED_RENDERING
    assert rendered.outcome is DirectoryIdentificationOutcome.SUPPORTED


@pytest.mark.parametrize("html", [
    '<nav><ul><li><a href="/about">About</a></li></ul></nav>',
    '<footer><div class="social-links"><a href="/social">Social</a></div></footer>',
    '<div class="news-card"><a href="/news/1">News story</a></div>',
    '<div class="product-card"><a href="/product/1">Product</a></div>',
    '<ol class="breadcrumbs"><li><a href="/">Home</a></li></ol>',
    '<div class="language-selector"><a href="/fr">French</a></div>',
    '<div class="contact-block">Address: unrelated</div>',
])
def test_generic_repeated_links_and_nonfacility_blocks_are_not_listings(html):
    result = identify_and_prepare_directory(source(content=html))
    assert not result.listings
    assert result.outcome is DirectoryIdentificationOutcome.NOT_A_DIRECTORY


def test_conflicting_parser_boundaries_are_ambiguous():
    result = identify_and_prepare_directory(source(content="""
      <table><tr><td><a href="/p/1">Table One</a></td></tr></table>
      <div class="facility-card"><a href="/p/2">Card Two</a></div>
    """))
    assert result.outcome is DirectoryIdentificationOutcome.AMBIGUOUS


def test_browser_markers_have_durable_schema_fields():
    from app.db.models import ScrapingPhase5WorkJob
    columns = ScrapingPhase5WorkJob.__table__.c
    assert "requires_browser_interaction" in columns
    assert "requires_managed_rendering" in columns
    assert "continuation_markers_json" in columns


def test_topology_reuse_does_not_collapse_directory_provenance():
    common = dict(
        organization_id="o", execution_id="e",
        listing_page_url="https://docs.python.org/list",
        profile_url="https://docs.python.org/profile",
        official_website_url="https://www.python.org/",
        displayed_facility_name="One", listing_rank=1)
    first = directory_observation_fingerprint(
        **common, parent_directory_node_id="directory-a")
    second = directory_observation_fingerprint(
        **common, parent_directory_node_id="directory-b")
    assert first != second
    from app.db.models import ScrapingCrawlEdge
    unique = {constraint.name for constraint in ScrapingCrawlEdge.__table__.constraints}
    assert "uq_crawl_edge_org_exec_rel" in unique
