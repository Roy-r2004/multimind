from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RehabilitationFacility

from app.services.scraping.hard_gate_verification_service import (
    HardGateEvidence,
    HardGateLocation,
    HardGateVerificationInput,
    hard_gate_verification_service,
)
from test_scraping_facility_publication import (
    create_execution,
    create_staged_candidate,
    publish,
)


def test_uncertain_country_gate_stays_review_required_even_with_strong_evidence():
    result = hard_gate_verification_service.evaluate(
        HardGateVerificationInput(
            target_country_code="FR",
            mission_profile="full_national_census",
            facility_country_containment_status="uncertain",
            locations=[
                HardGateLocation(
                    full_address="10 Rue Example, Paris",
                    country_containment_status="uncertain",
                    location_completeness_status="complete",
                    location_gap_reason=None,
                )
            ],
            phone_values=["+33122334455"],
            verified_evidence_count=5,
        )
    )

    assert result.publication_class == "review_required"
    assert result.gate_results["physical_location_in_target_country"]["status"] == "uncertain"
    assert result.gate_results["location_and_phone_complete"]["status"] == "passed"


def test_private_residential_profile_requires_residential_signal():
    result = hard_gate_verification_service.evaluate(
        HardGateVerificationInput(
            target_country_code="US",
            mission_profile="private_residential",
            facility_country_containment_status="confirmed_target",
            locations=[
                HardGateLocation(
                    full_address="1 Main Street, Denver, CO",
                    country_containment_status="confirmed_target",
                    location_completeness_status="complete",
                    location_gap_reason=None,
                )
            ],
            phone_values=["+13035550199"],
            facility_type="outpatient_clinic",
            verified_evidence_count=4,
        )
    )

    assert result.publication_class == "review_required"
    assert result.gate_results["private_residential_signal"]["status"] == "uncertain"


def test_conflicting_country_evidence_is_recorded_as_contradiction():
    result = hard_gate_verification_service.evaluate(
        HardGateVerificationInput(
            target_country_code="AT",
            mission_profile="full_national_census",
            facility_country_containment_status="confirmed_target",
            locations=[
                HardGateLocation(
                    full_address="Mariahilfer Strasse 10, Wien, Austria",
                    country_containment_status="confirmed_target",
                    location_completeness_status="complete",
                    location_gap_reason=None,
                )
            ],
            phone_values=["+4312345678"],
            verified_evidence_count=4,
            evidence=[
                HardGateEvidence(field_name="country", raw_value="Austria"),
                HardGateEvidence(field_name="country", raw_value="Germany"),
            ],
        )
    )

    codes = {item["code"] for item in result.contradictions}
    assert "conflicting_country_evidence" in codes


async def test_publication_persists_hard_gate_results_and_review_class(
    db: AsyncSession, auth
):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution, name="Centre Gate Audit")

    summary = await publish(db, auth, execution, candidate)

    facility = await db.get(RehabilitationFacility, summary.final_facility_id)
    assert facility is not None
    assert facility.publication_class == "review_required"
    assert facility.hard_gate_results_json is not None
    assert facility.hard_gate_results_json["publication_class"] == "review_required"
    assert (
        facility.hard_gate_results_json["gate_results"]["physical_location_in_target_country"][
            "status"
        ]
        == "uncertain"
    )
