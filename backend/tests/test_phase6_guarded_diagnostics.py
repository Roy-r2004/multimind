import inspect

from scripts.phase6_guarded_extraction import (
    KNOWN_ZERO_CANDIDATE_EXECUTIONS,
    discover_smoke_targets,
    rank_smoke_target,
    terminal_explanation,
    validate_discovery_request,
)


def summary(**overrides):
    value = {
        "source_documents": 1,
        "prepared_documents": 1,
        "extracted_candidates": 0,
        "country_decisions": 0,
        "failed_work": 0,
        "pending_work_by_kind": {},
        "work_jobs_by_kind_and_status": {},
        "publication_invoked": False,
    }
    value.update(overrides)
    return value


def test_successful_zero_facility_extraction_is_terminal_and_unpublished():
    value = summary()
    assert terminal_explanation(value) == "no_facilities_extracted"
    assert value["publication_invoked"] is False


def test_candidate_with_unseeded_or_pending_verification_is_distinguished():
    value = summary(
        extracted_candidates=1,
        pending_work_by_kind={"verify_candidate": 1},
    )
    assert terminal_explanation(value) == "verification_pending"
    assert value["publication_invoked"] is False


def test_candidate_fully_verified_and_deduplicated_completes_package_a():
    value = summary(
        extracted_candidates=1,
        country_decisions=1,
        work_jobs_by_kind_and_status={"deduplicate_candidate:succeeded": 1},
    )
    assert terminal_explanation(value) == "package_a_complete_with_candidates"
    assert value["publication_invoked"] is False


def test_failed_work_blocks_completion_without_publication():
    value = summary(extracted_candidates=1, failed_work=1)
    assert terminal_explanation(value) == "blocked_by_failed_work"
    assert value["publication_invoked"] is False


def smoke_item(**overrides):
    value = {
        "execution_id": "safe-execution",
        "execution_status": "paused",
        "retrieval_failure": None,
        "retrieval_result_id": "retrieval",
        "text_character_count": 5000,
        "representation_type": "text/html",
        "document_count": 1,
        "facility_signals": 2,
        "address_signals": 2,
        "contact_signals": 2,
        "source_classification": "facility_profile",
        "directory_observations": 0,
        "existing_candidate_count": 0,
        "existing_attempt_count": 0,
    }
    value.update(overrides)
    return value


def test_smoke_ranking_prefers_small_paused_explicit_profile():
    score, reason, warnings = rank_smoke_target(smoke_item())
    assert score > 100
    assert reason == "explicit_address_and_contact_signals"
    assert warnings == []


def test_smoke_ranking_excludes_failed_empty_large_and_known_zero_sources():
    assert rank_smoke_target(smoke_item(retrieval_failure="timeout"))[0] < 0
    assert rank_smoke_target(smoke_item(text_character_count=0))[0] < 0
    assert rank_smoke_target(smoke_item(document_count=1000))[0] < 0
    known = next(iter(KNOWN_ZERO_CANDIDATE_EXECUTIONS))
    assert rank_smoke_target(smoke_item(execution_id=known))[0] < 0
    assert rank_smoke_target(smoke_item(existing_candidate_count=1))[0] < 0


def test_target_discovery_requires_tenant_and_normalizes_country_and_limit():
    assert validate_discovery_request("org", "lb", 10) == ("org", "LB", 10)
    for arguments in ((None, "LB", 10), ("org", "LBN", 10), ("org", "LB", 0)):
        try:
            validate_discovery_request(*arguments)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid discovery scope was accepted")


def test_target_discovery_source_is_read_only_and_has_no_sensitive_output_fields():
    source = inspect.getsource(discover_smoke_targets)
    for forbidden in (
        "provider.extract", "seed_document_preparation", "claim_batch",
        "db.add", "db.commit", "publication", "excel",
    ):
        assert forbidden not in source
    for forbidden_output in (
        '"content_text":', '"prompt":', '"provider_response":',
        '"api_key":', '"safe_error_message":',
    ):
        assert forbidden_output not in source
