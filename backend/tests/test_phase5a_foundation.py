"""Phase 5A contracts/schema tests. No network, Docker, or PostgreSQL."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import configure_mappers

from app.db.models import Base, ScrapingPhase5WorkJob
from app.services.scraping import phase5_job_service
from app.services.scraping.phase5_contracts import (
    Phase5WorkKind,
    PreparedRetrievalResult,
    ProviderToolBlocker,
    RetryableFailure,
    SanitizedPublicEventMetadata,
    SanitizedOperationalMetadata,
    directory_observation_fingerprint,
    prepare_phase5_job,
    retrieval_result_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "031_phase5_directory_retrieval_foundation.py"


def _prepared():
    return prepare_phase5_job(
        organization_id="org", execution_id="exec", crawl_node_id="node",
        original_url="HTTPS://Docs.Python.Org/path?utm_source=x",
        source_classification="directory",
        work_kind=Phase5WorkKind.DIRECTORY_EXPANSION,
        selected_tool="directory_expansion", requested_at=datetime(2026, 1, 1, tzinfo=UTC),
        input_retrieval_result_id="retrieval-result",
        input_source_document_id="source-document",
        input_content_fingerprint="a" * 64,
        input_retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
    )


def test_work_kind_parsing_is_typed_and_unknown_fails_closed():
    assert Phase5WorkKind("http_retrieval") is Phase5WorkKind.HTTP_RETRIEVAL
    with pytest.raises(ValueError):
        Phase5WorkKind("future_magic")


@pytest.mark.parametrize(("kind", "tool"), [
    (Phase5WorkKind.DIRECTORY_EXPANSION, "directory_expansion"),
    (Phase5WorkKind.HTTP_RETRIEVAL, "http"),
    (Phase5WorkKind.FIRECRAWL_RETRIEVAL, "firecrawl"),
    (Phase5WorkKind.PLAYWRIGHT_RETRIEVAL, "playwright"),
])
def test_work_kind_tool_combinations_are_explicit(kind, tool):
    binding = ({
        "input_retrieval_result_id": "retrieval-result",
        "input_source_document_id": "source-document",
        "input_content_fingerprint": "a" * 64,
        "input_retrieval_method": Phase5WorkKind.HTTP_RETRIEVAL,
    } if kind is Phase5WorkKind.DIRECTORY_EXPANSION else {})
    prepared = prepare_phase5_job(
        organization_id="o", execution_id="e", crawl_node_id="n",
        original_url="https://docs.python.org/a",
        source_classification="directory", work_kind=kind, selected_tool=tool,
        requested_at=datetime.now(UTC), **binding)
    assert prepared.selected_tool == tool
    with pytest.raises(ValueError):
        prepare_phase5_job(
            organization_id="o", execution_id="e", crawl_node_id="n",
            original_url="https://docs.python.org/a",
            source_classification="directory", work_kind=kind,
            selected_tool="contradictory", requested_at=datetime.now(UTC),
            **binding)


def test_job_fingerprint_is_deterministic_and_reuses_phase4_canonicalization():
    first = _prepared()
    second = _prepared()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert first.canonical_url == "https://docs.python.org/path"


def test_hash_helper_receives_payload_not_canonical_bytes(monkeypatch):
    observed = {}

    def fake_hash(value):
        observed["value"] = value
        return "a" * 64

    monkeypatch.setattr("app.services.scraping.phase5_contracts.sha256_hex", fake_hash)
    assert _prepared().fingerprint == "a" * 64
    assert isinstance(observed["value"], dict)


def test_identity_is_execution_and_organization_scoped():
    first = _prepared()
    other_org = prepare_phase5_job(**{
        **first.model_dump(exclude={"fingerprint", "canonical_url", "rejection_category"}),
        "organization_id": "other",
    })
    assert first.fingerprint != other_org.fingerprint


def test_unsafe_url_is_a_rejected_non_fetchable_contract():
    prepared = prepare_phase5_job(
        organization_id="o", execution_id="e", crawl_node_id="n",
        original_url="http://127.0.0.1/private", source_classification="directory",
        work_kind=Phase5WorkKind.HTTP_RETRIEVAL, selected_tool="http",
        requested_at=datetime.now(UTC),
    )
    assert prepared.canonical_url is None
    assert prepared.rejection_category


def test_observation_fingerprint_is_deterministic_and_listing_specific():
    value = dict(
        organization_id="o", execution_id="e", parent_directory_node_id="n",
        listing_page_url="https://docs.python.org/a",
        profile_url="https://docs.python.org/p", listing_rank=1)
    assert directory_observation_fingerprint(**value) == directory_observation_fingerprint(**value)
    assert directory_observation_fingerprint(**value) != directory_observation_fingerprint(
        **{**value, "listing_rank": 2})


def test_retrieval_results_are_one_to_many_with_stable_resource_identity():
    common = dict(
        organization_id="o", execution_id="e", work_job_id="j",
        retrieval_method=Phase5WorkKind.FIRECRAWL_RETRIEVAL,
        resource_role="mapped_page", result_ordinal=0)
    first = retrieval_result_fingerprint(
        **common, resource_url="https://docs.python.org/a?utm_source=x")
    replay = retrieval_result_fingerprint(
        **common, resource_url="https://docs.python.org/a")
    second = retrieval_result_fingerprint(
        **{**common, "result_ordinal": 1}, resource_url="https://docs.python.org/b")
    assert first == replay
    assert first != second


def test_provider_blocker_and_retry_contracts_are_action_scoped():
    blocker = ProviderToolBlocker(tool="firecrawl", category="quota_blocked", retryable=True)
    retry = RetryableFailure(category="provider_timeout", public_message="Provider timed out.",
                             next_retry_at=datetime.now(UTC) + timedelta(minutes=1))
    assert blocker.tool == "firecrawl"
    assert retry.category == "provider_timeout"


def test_public_metadata_rejects_tokens_secrets_and_raw_auth():
    with pytest.raises(ValidationError):
        SanitizedPublicEventMetadata(category="provider_error", message="token=secret")
    with pytest.raises(ValidationError):
        SanitizedPublicEventMetadata(category="provider_error", message="safe",
                                     metadata={"claim_token": "secret"})
    event = SanitizedPublicEventMetadata(category="provider_error", message="Unavailable.")
    assert "token" not in event.model_dump_json()
    with pytest.raises(ValidationError):
        SanitizedPublicEventMetadata(
            category="unsafe_url", message="Rejected http://127.0.0.1/private")
    with pytest.raises(ValidationError):
        SanitizedOperationalMetadata.model_validate({"raw_provider_response": "{}"})


@pytest.mark.asyncio
async def test_stale_claim_rejects_result_without_writes(monkeypatch):
    async def stale(*args, **kwargs):
        return None
    monkeypatch.setattr(phase5_job_service, "_locked_claim", stale)

    class Session:
        pass

    result = PreparedRetrievalResult(
        job_id="j", organization_id="o", execution_id="e",
        requested_url="https://example.com", retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
        fetched_at=datetime.now(UTC), result_fingerprint="a" * 64,
        resource_role="page", result_ordinal=0,
    )
    persisted = await phase5_job_service.persist_retrieval_result(
        Session(), claim_token="stale", result=result)
    assert persisted.outcome == "stale_claim"


@pytest.mark.asyncio
async def test_retry_transition_clears_claim_and_preserves_action(monkeypatch):
    job = type("Job", (), {
        "id": "j", "status": None, "next_retry_at": None,
        "last_error_category": None, "last_error_message": None,
        "claim_token": "token", "claimed_at": datetime.now(UTC),
        "lease_expires_at": datetime.now(UTC) + timedelta(minutes=1),
    })()

    async def current(*args, **kwargs):
        return job

    class Session:
        async def flush(self):
            return None

    monkeypatch.setattr(phase5_job_service, "_locked_claim", current)
    retry_at = datetime.now(UTC) + timedelta(minutes=2)
    result = await phase5_job_service.record_retryable_failure(
        Session(), job_id="j", organization_id="o", execution_id="e",
        claim_token="token", failure=RetryableFailure(
            category="provider_timeout", public_message="Provider timed out.",
            next_retry_at=retry_at))
    assert result.outcome == "persisted"
    assert job.status.value == "retry_scheduled"
    assert job.next_retry_at == retry_at
    assert job.claim_token is job.claimed_at is job.lease_expires_at is None


def test_metadata_has_phase5_tables_constraints_and_claim_indexes():
    tables = Base.metadata.tables
    assert {"scraping_phase5_work_jobs", "scraping_phase5_retrieval_results",
            "scraping_directory_observations"} <= set(tables)
    job = tables["scraping_phase5_work_jobs"]
    assert {"ix_phase5_jobs_pending_claim", "ix_phase5_jobs_retry_schedule",
            "ix_phase5_jobs_running_lease"} <= {index.name for index in job.indexes}
    assert "uq_phase5_job_org_exec_fingerprint" in {
        c.name for c in job.constraints if isinstance(c, UniqueConstraint)}
    assert {"fk_phase5_job_node_org_exec", "fk_phase5_job_candidate_org_exec",
            "fk_phase5_job_edge_org_exec", "fk_phase5_job_query_org_exec"} <= {
        c.name for c in job.constraints if isinstance(c, ForeignKeyConstraint)}
    retrieval = tables["scraping_phase5_retrieval_results"]
    assert "uq_phase5_retrieval_resource" in {
        c.name for c in retrieval.constraints if isinstance(c, UniqueConstraint)}
    checks = {c.name for c in job.constraints}
    assert {"ck_phase5_job_work_kind", "ck_phase5_job_status",
            "ck_phase5_job_kind_tool", "ck_phase5_job_claim_state",
            "ck_phase5_job_unsafe_terminal"} <= checks


def test_composite_fk_targets_have_exact_ordered_unique_keys():
    tables = Base.metadata.tables
    unique_keys = {
        table.name: {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        for table in tables.values()
    }
    for table_name in (
        "scraping_phase5_work_jobs", "scraping_phase5_retrieval_results",
        "scraping_directory_observations",
    ):
        for constraint in tables[table_name].constraints:
            if not isinstance(constraint, ForeignKeyConstraint) or len(constraint.columns) < 2:
                continue
            remote_table = next(iter(constraint.elements)).column.table.name
            remote_columns = tuple(element.column.name for element in constraint.elements)
            assert remote_columns in unique_keys[remote_table]
            assert constraint.deferrable in (None, False)


def test_all_phase5_related_mappers_configure_without_ambiguity():
    configure_mappers()


def test_migration_is_single_linear_031_with_expected_contracts():
    text = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "031"' in text
    assert 'down_revision = "030"' in text
    for expected in (
        "scraping_phase5_work_jobs", "scraping_phase5_retrieval_results",
        "scraping_directory_observations", "ix_phase5_jobs_pending_claim",
        "uq_directory_observation_org_exec_fingerprint",
        "uq_phase5_retrieval_resource", "ck_phase5_job_claim_state",
    ):
        assert expected in text


def test_model_job_enum_rejects_unknown_at_python_boundary():
    assert ScrapingPhase5WorkJob.__table__.c.work_kind.type.enum_class is not None


def test_claim_service_declares_bounded_skip_locked_safe_predicates():
    source = (ROOT / "app" / "services" / "scraping" /
              "phase5_job_service.py").read_text(encoding="utf-8")
    assert ".where(and_(*predicates))" in source
    assert ".limit(batch_size).with_for_update(skip_locked=True)" in source
    assert "ScrapingPhase5WorkJob.canonical_url.is_not(None)" in source
    assert "ScrapingPhase5WorkJob.next_retry_at <= now" in source
    assert "ScrapingPhase5WorkJob.lease_expires_at > func.now()" in source
