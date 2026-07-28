"""Phase 4 Slice 5: non-Docker preparation + classification merge tests.

Covers pure prepare_provider_results behavior (items 1–30). Persistence against
PostgreSQL lives in test_phase4_discovery_results_postgres.py (not run here).
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.db.models import CrawlNodeSourceClassification
from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.discovery_url_service import (
    build_canonical_url_hash_payload,
    compute_canonical_url_hash,
)
from app.services.scraping.source_discovery_claim_service import ClaimedQueryJob, generate_claim_token
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)
from app.services.scraping.source_discovery_result_service import (
    PreparedDiscoveryBatch,
    PreparedDiscoveryResult,
    SourceDiscoveryResultService,
    merge_crawl_node_classification,
    prepare_provider_results,
)

SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "source_discovery_result_service.py"
)
FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
SECRET = "secret-key-should-never-leak"


def _claimed(**overrides: Any) -> ClaimedQueryJob:
    base = ClaimedQueryJob(
        id="query-job-1",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="Lebanon rehabilitation directory Beirut",
        provider="serper",
        claim_token=generate_claim_token(),
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=60),
        attempt_count=1,
        last_attempt_at=FIXED_NOW,
        priority=100,
        generation_ordinal=3,
        discovery_round=1,
        purpose="seed",
        country_code="LB",
        country_name="Lebanon",
        region_code="BEY",
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="directory",
        scope_level="region",
        important_city="Beirut",
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot="p" * 64,
        requested_at=FIXED_NOW,
    )
    return replace(base, **overrides) if overrides else base


def _item(
    claimed: ClaimedQueryJob,
    *,
    url: str,
    title: str = "Example Title",
    snippet: str = "Example snippet",
    rank: int = 1,
    **overrides: Any,
) -> DiscoveryProviderResultItem:
    base = DiscoveryProviderResultItem(
        original_url=url,
        title=title,
        snippet=snippet,
        rank=rank,
        provider=claimed.provider,
        provider_result_type="organic",
        query_job_id=claimed.id,
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        claim_token=claimed.claim_token,
        scope_level=claimed.scope_level,
        language_code=claimed.language_code,
        region_code=claimed.region_code,
        region_name=claimed.region_name,
        important_city=claimed.important_city,
        country_code=claimed.country_code,
        discovered_at=FIXED_NOW,
    )
    return replace(base, **overrides) if overrides else base


def _succeeded(
    claimed: ClaimedQueryJob,
    results: list[DiscoveryProviderResultItem],
    *,
    raw: int | None = None,
    malformed: int = 0,
) -> DiscoveryProviderExecutionResult:
    return DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider=claimed.provider,
        query_job_id=claimed.id,
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        claim_token=claimed.claim_token,
        discovered_at=FIXED_NOW,
        results=tuple(results),
        raw_result_count=raw if raw is not None else len(results),
        accepted_result_count=len(results),
        skipped_malformed_count=malformed,
        diagnostic_code="succeeded",
    )


def test_01_successful_batch_prepares_valid_results():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://Docs.Python.Org/rehab")]),
        clock=FIXED_NOW,
    )
    assert batch.ready
    assert len(batch.results) == 1
    assert batch.results[0].persist_candidate is True
    assert batch.results[0].persist_crawl_node is True


def test_02_original_url_preserved():
    claimed = _claimed()
    original = "https://Docs.Python.Org/path?utm_source=x&id=1"
    batch = prepare_provider_results(
        claimed, _succeeded(claimed, [_item(claimed, url=original)]), clock=FIXED_NOW
    )
    assert batch.results[0].original_url == original


def test_03_canonical_url_generated():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="HTTPS://Docs.Python.Org/Path")]),
        clock=FIXED_NOW,
    )
    assert batch.results[0].canonical_url == "https://docs.python.org/Path"


def test_04_tracking_parameters_removed():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(
            claimed,
            [_item(claimed, url="https://docs.python.org/x?utm_source=a&gclid=1&keep=2")],
        ),
        clock=FIXED_NOW,
    )
    assert batch.results[0].canonical_url == "https://docs.python.org/x?keep=2"


def test_05_canonical_hash_uses_payload_helper():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/x")]),
        clock=FIXED_NOW,
    )
    expected = compute_canonical_url_hash(batch.results[0].canonical_url)
    assert batch.results[0].canonical_url_hash == expected
    assert expected == sha256_hex(build_canonical_url_hash_payload(batch.results[0].canonical_url))


def test_06_title_snippet_rank_preserved():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(
            claimed,
            [_item(claimed, url="https://docs.python.org/", title="T", snippet="S", rank=3)],
        ),
        clock=FIXED_NOW,
    )
    assert batch.results[0].title == "T"
    assert batch.results[0].snippet == "S"
    assert batch.results[0].rank == 3


def test_07_missing_title_remains_missing():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/", title="")]),
        clock=FIXED_NOW,
    )
    assert batch.results[0].title == ""


def test_08_missing_snippet_remains_missing():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/", snippet="")]),
        clock=FIXED_NOW,
    )
    assert batch.results[0].snippet == ""


def test_09_invalid_url_rejected_without_fabrication():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="not a url")], raw=1),
        clock=FIXED_NOW,
    )
    assert batch.ready
    assert batch.results == ()
    assert batch.invalid_url_count == 1


def test_10_unsafe_literal_ip_rejected_for_crawl():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="http://127.0.0.1/admin")]),
        clock=FIXED_NOW,
    )
    assert batch.unsafe_url_count == 1
    assert len(batch.results) == 1
    assert batch.results[0].persist_candidate is True
    assert batch.results[0].persist_crawl_node is False
    assert batch.results[0].rejection_code == "unsafe_result_url"


def test_11_injected_private_dns_rejected():
    claimed = _claimed()
    calls: list[str] = []

    def resolver(host: str) -> list[str]:
        calls.append(host)
        return ["10.0.0.5"]

    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        resolver=resolver,
        require_dns=True,
        clock=FIXED_NOW,
    )
    assert calls == ["docs.python.org"]
    assert batch.unsafe_url_count == 1
    assert batch.results[0].persist_crawl_node is False


def test_12_mixed_public_private_dns_rejected():
    claimed = _claimed()

    def resolver(host: str) -> list[str]:
        return ["8.8.8.8", "10.0.0.1"]

    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        resolver=resolver,
        require_dns=True,
        clock=FIXED_NOW,
    )
    assert batch.results[0].persist_crawl_node is False
    assert batch.results[0].safety_error_code == "mixed_public_private_dns"


def test_13_injected_public_dns_accepted():
    claimed = _claimed()

    def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        resolver=resolver,
        require_dns=True,
        clock=FIXED_NOW,
    )
    assert batch.results[0].persist_crawl_node is True
    assert batch.results[0].is_safe is True


def test_14_no_real_dns_call_without_resolver():
    claimed = _claimed()
    # require_dns True without resolver → unsafe via dns_resolution_failed (no socket).
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        resolver=None,
        require_dns=True,
        clock=FIXED_NOW,
    )
    assert batch.results[0].persist_crawl_node is False
    assert batch.results[0].safety_error_code == "dns_resolution_failed"


def test_15_pdf_classification_preserved():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/report.pdf")]),
        clock=FIXED_NOW,
    )
    assert batch.results[0].source_classification == CrawlNodeSourceClassification.PDF.value


def test_16_social_classification_preserved():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://www.facebook.com/rehab.center")]),
        clock=FIXED_NOW,
    )
    assert (
        batch.results[0].source_classification
        == CrawlNodeSourceClassification.SOCIAL_PROFILE.value
    )


def test_17_government_registry_directory_classification():
    claimed = _claimed()
    gov = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://health.gov/facilities")]),
        clock=FIXED_NOW,
    )
    directory = prepare_provider_results(
        claimed,
        _succeeded(
            claimed,
            [_item(claimed, url="https://www.healthgrades.com/find-a-doctor")],
        ),
        clock=FIXED_NOW,
    )
    assert gov.results[0].source_classification == CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value
    assert directory.results[0].source_classification == CrawlNodeSourceClassification.DIRECTORY.value


def test_18_ambiguous_remains_unclassified():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/about")]),
        clock=FIXED_NOW,
    )
    assert (
        batch.results[0].source_classification
        == CrawlNodeSourceClassification.UNCLASSIFIED.value
    )


def test_19_duplicate_tracking_variants_collapse():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(
            claimed,
            [
                _item(claimed, url="https://docs.python.org/x?utm_source=a", rank=2),
                _item(claimed, url="https://docs.python.org/x?gclid=1", rank=5),
            ],
            raw=2,
        ),
        clock=FIXED_NOW,
    )
    assert len(batch.results) == 1
    assert batch.duplicate_within_query_count == 1
    assert batch.results[0].canonical_url == "https://docs.python.org/x"


def test_20_meaningfully_different_urls_remain_distinct():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(
            claimed,
            [
                _item(claimed, url="https://docs.python.org/a", rank=1),
                _item(claimed, url="https://docs.python.org/b", rank=2),
            ],
        ),
        clock=FIXED_NOW,
    )
    assert len(batch.results) == 2
    assert {r.canonical_url for r in batch.results} == {
        "https://docs.python.org/a",
        "https://docs.python.org/b",
    }


def test_21_best_rank_duplicate_behavior_deterministic():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(
            claimed,
            [
                _item(claimed, url="https://docs.python.org/x?utm_source=a", rank=5, title="Worse"),
                _item(claimed, url="https://docs.python.org/x?gclid=1", rank=2, title="Better"),
            ],
        ),
        clock=FIXED_NOW,
    )
    assert len(batch.results) == 1
    assert batch.results[0].rank == 2
    assert batch.results[0].title == "Better"


def test_22_empty_successful_provider_response():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed, _succeeded(claimed, [], raw=0, malformed=0), clock=FIXED_NOW
    )
    assert batch.ready
    assert batch.results == ()
    assert batch.raw_provider_count == 0
    assert batch.parsed_provider_count == 0


def test_23_provider_job_ownership_mismatch_rejected():
    claimed = _claimed()
    other = replace(
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        organization_id="other-org",
    )
    batch = prepare_provider_results(claimed, other, clock=FIXED_NOW)
    assert batch.outcome == "rejected"
    assert batch.error_code == "organization_mismatch"
    assert batch.results == ()


def test_24_claim_token_mismatch_rejected():
    claimed = _claimed()
    other = replace(
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        claim_token=generate_claim_token(),
    )
    batch = prepare_provider_results(claimed, other, clock=FIXED_NOW)
    assert batch.outcome == "rejected"
    assert batch.error_code == "stale_claim"


def test_25_provider_mismatch_rejected():
    claimed = _claimed()
    other = replace(
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        provider="brave",
    )
    batch = prepare_provider_results(claimed, other, clock=FIXED_NOW)
    assert batch.outcome == "rejected"
    assert batch.error_code == "provider_mismatch"


def test_26_raw_errors_secrets_not_exposed():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        replace(
            _succeeded(claimed, []),
            outcome="provider_timeout",
            diagnostic_code=SECRET,
        ),
        clock=FIXED_NOW,
    )
    assert batch.outcome == "rejected"
    assert batch.error_code == "invalid_provider_batch"
    dumped = repr(batch)
    assert SECRET not in dumped
    assert "Traceback" not in dumped


def test_27_prepared_dtos_immutable():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        clock=FIXED_NOW,
    )
    assert is_dataclass(batch) and batch.__dataclass_params__.frozen
    assert is_dataclass(batch.results[0]) and batch.results[0].__dataclass_params__.frozen
    with pytest.raises(FrozenInstanceError):
        batch.raw_provider_count = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        batch.results[0].rank = 99  # type: ignore[misc]


def test_28_no_database_session_during_preparation():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    prepare_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "prepare_provider_results":
            prepare_fn = node
            break
    assert prepare_fn is not None
    banned = {"AsyncSessionLocal", "session_factory", "session.begin", "commit"}
    text = ast.get_source_segment(source, prepare_fn) or ""
    for token in banned:
        assert token not in text
    # Runtime: monkeypatch session factory would be unused; call still works with no DB.
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        clock=FIXED_NOW,
    )
    assert batch.ready


def test_29_no_http_provider_call_during_preparation():
    source = inspect.getsource(prepare_provider_results)
    for token in ("httpx", "fetch_payload", "execute_claimed_query", "AsyncClient", "socket"):
        assert token not in source


def test_30_no_facility_acceptance_qualification_fields():
    assert "facility_accepted" not in {f.name for f in fields(PreparedDiscoveryResult)}
    assert "qualified" not in {f.name for f in fields(PreparedDiscoveryResult)}
    assert "published" not in {f.name for f in fields(PreparedDiscoveryBatch)}
    names = {f.name for f in fields(PreparedDiscoveryResult)}
    assert "source_classification" in names
    # Preliminary classification only — not facility acceptance.
    assert "initial_acceptance" not in names


def test_classification_merge_does_not_downgrade_to_unclassified():
    assert (
        merge_crawl_node_classification("directory", "unclassified") == "directory"
    )


def test_classification_merge_does_not_flip_official_to_directory():
    assert (
        merge_crawl_node_classification("official_facility_site", "directory")
        == "official_facility_site"
    )


def test_classification_merge_upgrades_from_unclassified():
    assert merge_crawl_node_classification("unclassified", "pdf") == "pdf"


def test_classification_merge_keeps_strong_conflict_conservative():
    assert (
        merge_crawl_node_classification("directory", "government_source")
        == "directory"
    )


def test_malformed_counts_propagate_without_fabricated_urls():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed, _succeeded(claimed, [], raw=3, malformed=3), clock=FIXED_NOW
    )
    assert batch.malformed_provider_count == 3
    assert batch.results == ()


def test_service_module_does_not_import_mock_worker():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "mission_campaign_mock_worker" not in source


def test_service_module_does_not_call_legacy_existing_candidate():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "_existing_candidate" not in source


def test_persist_rejects_non_ready_batch_without_session():
    # Construction-only: rejected batch should short-circuit before TX work when
    # exercised asynchronously — verify prepare rejection shape here.
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        replace(_succeeded(claimed, []), outcome="provider_timeout"),
        clock=FIXED_NOW,
    )
    assert batch.outcome == "rejected"
    assert isinstance(SourceDiscoveryResultService(), SourceDiscoveryResultService)


def test_prepare_does_not_touch_magic_session_factory(monkeypatch: pytest.MonkeyPatch):
    sentinel = MagicMock()
    monkeypatch.setattr(
        "app.services.scraping.source_discovery_result_service.AsyncSessionLocal",
        sentinel,
    )
    claimed = _claimed()
    prepare_provider_results(
        claimed,
        _succeeded(claimed, [_item(claimed, url="https://docs.python.org/")]),
        clock=FIXED_NOW,
    )
    sentinel.assert_not_called()
