import ast
import inspect
import textwrap

import pytest
from sqlalchemy import func, select

import scripts.phase5_guarded_smoke as guarded_smoke
from app.db.models import (
    ScrapingBlueprint, ScrapingCrawlNode, ScrapingExecution,
    ScrapingFacilityPhaseWorkJob, ScrapingMission, ScrapingPhase5RetrievalResult,
    ScrapingPhase5WorkJob, ScrapingSourceCandidate, ScrapingSourceDiscoveryQuery,
    ScrapingSourceDocument,
)
from scripts.phase5_guarded_smoke import (
    KNOWN_UNSUITABLE_EXECUTION_ID,
    _profile_target_decision,
    controlled_execution_reuse_exclusion,
    controlled_profile_identities,
    create_controlled_profile,
    list_profile_targets,
    preview_controlled_profile,
    run_profile_target_listing,
    validate_controlled_profile_request,
    validate_profile_listing_request,
)


def target(**overrides):
    value = {
        "execution_id": "execution-b",
        "execution_status": "paused",
        "execution_node_count": 4,
        "execution_document_count": 0,
        "successful_retrieval_count": 0,
        "source_document_count": 0,
        "terminal_failed_job_count": 0,
        "retryable_failed_job_count": 0,
        "url": "https://care.gov.lb/facilities/cedar-treatment-centre",
        "classification": "facility_profile",
        "source_candidate_id": "candidate",
        "provider": "serper",
        "discovery_query_id": "query",
    }
    value.update(overrides)
    return value


def test_listing_scope_is_mandatory_tenant_safe_and_validated():
    assert validate_profile_listing_request(" org ", "lb", 10) == ("org", "LB", 10)
    for values in (
        (None, "LB", 10),
        ("", "LB", 10),
        ("org", None, 10),
        ("org", "LBN", 10),
        ("org", "1B", 10),
        ("org", "LB", 0),
        ("org", "LB", 101),
    ):
        try:
            validate_profile_listing_request(*values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid listing request accepted: {values!r}")
    source = inspect.getsource(list_profile_targets)
    assert "ScrapingCrawlNode.organization_id == organization_id" in source
    assert "ScrapingSourceCandidate.country_code == country" in source


def test_known_active_large_unsafe_and_terminal_failed_targets_are_excluded():
    exclusions = (
        ({"execution_id": KNOWN_UNSUITABLE_EXECUTION_ID},
         "known_zero_candidate_execution"),
        ({"execution_status": "running"}, "active_execution"),
        ({"execution_node_count": 101}, "large_execution"),
        ({"url": "ftp://care.gov.lb/facility/one"}, "unsupported_scheme"),
        ({"terminal_failed_job_count": 1, "retryable_failed_job_count": 0},
         "terminal_retrieval_failure"),
        ({"source_document_count": 1}, "already_retrieved"),
        ({"successful_retrieval_count": 1}, "already_retrieved"),
        ({"source_candidate_id": None}, "missing_source_candidate_provenance"),
    )
    for overrides, expected_reason in exclusions:
        score, exclusion_reason, warnings = _profile_target_decision(
            target(**overrides))
        assert score is None
        assert exclusion_reason == expected_reason
        assert warnings == []


def test_search_home_social_binary_and_non_profile_nodes_are_excluded():
    exclusions = (
        ({"url": "https://care.gov.lb/search?q=clinic"},
         "search_or_directory_page"),
        ({"url": "https://care.gov.lb/", "classification": "unclassified"},
         "generic_homepage"),
        ({"url": "https://facebook.com/clinic"}, "social_or_map_only"),
        ({"url": "https://care.gov.lb/facility/profile.pdf"},
         "binary_or_pdf_resource"),
        ({"url": "https://care.gov.lb/about",
          "classification": "supporting_source"}, "no_facility_profile_signal"),
        ({"classification": "directory"}, "excluded_classification_directory"),
    )
    for overrides, expected_reason in exclusions:
        score, exclusion_reason, warnings = _profile_target_decision(
            target(**overrides))
        assert score is None
        assert exclusion_reason == expected_reason
        assert warnings == []


def test_small_paused_official_facility_profile_ranks_first_deterministically():
    preferred = target()
    lower = target(
        execution_id="execution-a",
        execution_status="completed",
        execution_node_count=20,
        classification="official_facility_site",
        url="https://care.gov.lb/clinic/cedar",
    )
    first_score, first_reason, first_warnings = _profile_target_decision(preferred)
    lower_score, lower_reason, lower_warnings = _profile_target_decision(lower)
    assert first_score is not None
    assert lower_score is not None
    assert first_score > lower_score
    assert first_reason == "official_single_facility_profile"
    assert lower_reason == "explicit_facility_url_signals"
    assert first_warnings == []
    assert lower_warnings == []
    ranked = [
        {"score": lower_score, "execution_id": "execution-b", "crawl_node_id": "2"},
        {"score": first_score, "execution_id": "execution-c", "crawl_node_id": "1"},
        {"score": first_score, "execution_id": "execution-a", "crawl_node_id": "3"},
    ]
    ranked.sort(key=lambda item: (
        -item["score"], item["execution_id"], item["crawl_node_id"]))
    assert [item["crawl_node_id"] for item in ranked] == ["3", "1", "2"]


def _assert_no_database_mutations(*functions):
    mutation_methods = {
        "add", "add_all", "delete", "flush", "commit", "merge",
    }
    mutation_receivers = {"session", "db"}
    forbidden_sql = {
        "insert", "update", "delete", "alter", "drop", "create", "truncate",
        "for update",
    }
    for function in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if (
                isinstance(called, ast.Attribute)
                and isinstance(called.value, ast.Name)
                and called.value.id in mutation_receivers
                and called.attr in mutation_methods
            ):
                raise AssertionError(
                    f"database mutation found: {called.value.id}.{called.attr}")
            if isinstance(called, ast.Attribute) and called.attr == "with_for_update":
                raise AssertionError("database locking found: with_for_update")
            if isinstance(called, ast.Name) and called.id in {
                "insert", "update", "delete",
            }:
                raise AssertionError(
                    f"database mutation statement found: {called.id}")
            if (
                isinstance(called, ast.Name)
                and called.id in {"text", "exec_driver_sql"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                sql = " ".join(node.args[0].value.lower().split())
                assert not any(keyword in sql for keyword in forbidden_sql)


def test_listing_code_is_read_only_has_no_network_and_emits_no_sensitive_fields():
    listing_source = inspect.getsource(list_profile_targets)
    runner_source = inspect.getsource(run_profile_target_listing)
    combined = listing_source + runner_source
    _assert_no_database_mutations(
        list_profile_targets, run_profile_target_listing)
    for forbidden in (
        ".execute(http",
        "NormalHttpRetriever", "provider.extract", "provider.complete",
        "claim_job", "create_job_idempotently",
        "persist_retrieval_resources", "publication", "excel", "export",
    ):
        assert forbidden not in combined
    for sensitive in (
        "content_text", "prompt_body", "provider_response", "api_key",
        "last_error_message", "raw_error", "secret",
    ):
        assert f'"{sensitive}"' not in combined


def test_database_mutation_ast_check_allows_set_add_but_rejects_session_add():
    def harmless():
        values = set()
        values.add("crawl-node")

    def database_write(session):
        session.add("crawl-node")

    _assert_no_database_mutations(harmless)
    try:
        _assert_no_database_mutations(database_write)
    except AssertionError as exc:
        assert "session.add" in str(exc)
    else:
        raise AssertionError("session.add was not rejected")


def controlled_request(organization_id: str):
    return validate_controlled_profile_request(
        organization_id,
        "LB",
        "https://cedar-rehab.org/",
        "Cedar Rehab Package A smoke",
    )


@pytest.mark.asyncio
async def test_controlled_profile_preview_is_read_only_and_side_effect_free(db, auth):
    request = controlled_request(auth.org_id)
    tracked_models = (
        ScrapingMission, ScrapingBlueprint, ScrapingExecution,
        ScrapingSourceDiscoveryQuery, ScrapingSourceCandidate, ScrapingCrawlNode,
        ScrapingPhase5WorkJob, ScrapingPhase5RetrievalResult,
        ScrapingSourceDocument, ScrapingFacilityPhaseWorkJob,
    )
    before = {
        model: int(await db.scalar(select(func.count()).select_from(model)) or 0)
        for model in tracked_models
    }
    preview = await preview_controlled_profile(db, request)
    after = {
        model: int(await db.scalar(select(func.count()).select_from(model)) or 0)
        for model in tracked_models
    }
    assert after == before
    assert preview["rows_that_would_be_created"] == 6
    for key in (
        "http_request", "provider_call", "worker_start", "retrieval",
        "extraction", "publication", "excel",
    ):
        assert preview[key] is False
    rendered = str(preview).lower()
    for forbidden in (
        "api_key", "secret", "content_text", "prompt_body", "provider_response",
        "raw_error",
    ):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_confirmed_controlled_profile_creates_valid_idempotent_paused_graph(
    db, auth,
):
    request = controlled_request(auth.org_id)
    first = await create_controlled_profile(
        db, request, creator_id=auth.user.id)
    await db.flush()
    second = await create_controlled_profile(
        db, request, creator_id=auth.user.id)
    await db.flush()

    assert first["created"] is True
    assert second["created"] is False
    assert first["execution_id"] == second["execution_id"]
    assert first["crawl_node_id"] == second["crawl_node_id"]
    assert first["source_candidate_id"] == second["source_candidate_id"]
    ids = controlled_profile_identities(request)
    execution = await db.get(ScrapingExecution, ids["execution"])
    node = await db.get(ScrapingCrawlNode, ids["crawl_node"])
    candidate = await db.get(ScrapingSourceCandidate, ids["source_candidate"])
    assert execution is not None
    assert execution.organization_id == auth.org_id
    assert execution.status.value == "paused"
    assert execution.team_plan_id is None
    assert execution.execution_plan_hash
    assert node is not None
    assert node.organization_id == auth.org_id
    assert node.execution_id == execution.id
    assert node.source_classification.value == "facility_profile"
    assert candidate is not None
    assert candidate.organization_id == auth.org_id
    assert candidate.execution_id == execution.id
    assert candidate.crawl_node_id == node.id

    assert await db.scalar(select(func.count()).select_from(
        ScrapingSourceCandidate).where(
            ScrapingSourceCandidate.execution_id == execution.id)) == 1
    assert await db.scalar(select(func.count()).select_from(
        ScrapingCrawlNode).where(
            ScrapingCrawlNode.execution_id == execution.id)) == 1
    for model in (
        ScrapingPhase5WorkJob, ScrapingPhase5RetrievalResult,
        ScrapingSourceDocument, ScrapingFacilityPhaseWorkJob,
    ):
        assert await db.scalar(select(func.count()).select_from(model).where(
            model.execution_id == execution.id)) == 0
    assert first["retrieval_result_count"] == 0
    assert first["source_document_count"] == 0
    assert first["run_id"] is None
    assert "--execution-id" in first["phase5_preview_command"]
    assert "--crawl-node-id" in first["phase5_preview_command"]


@pytest.mark.asyncio
async def test_controlled_profile_reuse_enforces_tenant_and_execution_safety(
    db, auth, monkeypatch,
):
    request = controlled_request(auth.org_id)
    ids = controlled_profile_identities(request)
    await create_controlled_profile(db, request, creator_id=auth.user.id)
    other_request = controlled_request("different-organization")
    monkeypatch.setattr(
        guarded_smoke, "controlled_profile_identities", lambda _request: ids)
    with pytest.raises(ValueError, match="tenant_mismatch"):
        await create_controlled_profile(
            db, other_request, creator_id=auth.user.id)


def test_controlled_profile_validation_rejects_country_http_and_unsafe_hosts():
    invalid = (
        ("LBN", "https://cedar-rehab.org/", "country"),
        ("LB", "http://cedar-rehab.org/", "HTTPS"),
        ("LB", "https://127.0.0.1/facility", "unsafe_ip"),
        ("LB", "https://care.example.org/facility", "unsafe_hostname"),
    )
    for country, url, expected in invalid:
        with pytest.raises(ValueError, match=expected):
            validate_controlled_profile_request(
                "organization", country, url, "Controlled profile")


def test_controlled_execution_reuse_rejects_active_large_and_known_execution():
    assert controlled_execution_reuse_exclusion(
        "safe", "paused", 1, 0) is None
    assert controlled_execution_reuse_exclusion(
        "safe", "running", 1, 0) == "controlled_profile_execution_is_active"
    assert controlled_execution_reuse_exclusion(
        "safe", "paused", 101, 0) == "controlled_profile_execution_is_oversized"
    assert controlled_execution_reuse_exclusion(
        KNOWN_UNSUITABLE_EXECUTION_ID, "paused", 1, 0
    ) == "known_zero_candidate_execution_must_not_be_reused"


def test_controlled_creation_path_has_no_retrieval_provider_worker_or_publication_calls():
    _assert_no_side_effect_integrations(
        preview_controlled_profile, create_controlled_profile)


def _dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _assert_no_side_effect_integrations(*functions):
    forbidden_modules = (
        "facility_candidate_publication_service",
        "execution_export_service",
        "publication_service",
        "export_service",
    )
    forbidden_calls = {
        "NormalHttpRetriever",
        "claim_job",
        "create_job_idempotently",
        "persist_retrieval_resources",
        "enqueue_execution",
        "enqueue_job",
    }
    for function in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not any(
                    marker in (node.module or "") for marker in forbidden_modules)
            elif isinstance(node, ast.Import):
                assert not any(
                    marker in alias.name
                    for alias in node.names
                    for marker in forbidden_modules
                )
            elif isinstance(node, ast.Call):
                called = _dotted_name(node.func)
                leaf = called.rsplit(".", 1)[-1]
                lowered = called.lower()
                assert leaf not in forbidden_calls
                assert called not in {
                    "provider.extract", "provider.complete",
                    "retriever.retrieve", "NormalHttpRetriever.retrieve",
                }
                assert not (
                    leaf.startswith("publish_")
                    or leaf == "publish"
                    or leaf.startswith("export_")
                    or leaf == "export"
                    or ("excel" in leaf and leaf.startswith("generate"))
                    or (
                        any(marker in lowered for marker in forbidden_modules)
                        and leaf not in {"get", "count"}
                    )
                ), f"side-effect integration call found: {called}"


def test_side_effect_ast_allows_safe_diagnostics_and_rejects_real_calls():
    def safe_diagnostics():
        return {"publication": False, "excel": False, "export": False}

    def publishes(publication_service):
        publication_service.publish("candidate")

    def exports(execution_export_service):
        execution_export_service.export_execution("execution")

    _assert_no_side_effect_integrations(safe_diagnostics)
    for function, expected in (
        (publishes, "publication_service.publish"),
        (exports, "execution_export_service.export_execution"),
    ):
        with pytest.raises(AssertionError, match=expected):
            _assert_no_side_effect_integrations(function)
