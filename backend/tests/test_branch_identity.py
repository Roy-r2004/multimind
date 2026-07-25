from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RehabilitationFacility
from app.services.scraping.branch_identity_service import (
    BranchIdentityInput,
    branch_identity_service,
)
from test_scraping_facility_publication import (
    create_execution,
    create_staged_candidate,
    publish,
)


def test_exact_same_facility_merges_when_address_and_phone_match():
    left = BranchIdentityInput(
        canonical_name="Centre Alpha",
        address="10 Rue Exemple, Paris",
        postal_code="75001",
        phone_values=["+33122334455"],
        website_host="alpha.example",
    )
    right = BranchIdentityInput(
        canonical_name="Centre Alpha",
        address="10 Rue Exemple, Paris",
        postal_code="75001",
        phone_values=["+33 1 22 33 44 55"],
        website_host="alpha.example",
    )

    result = branch_identity_service.compare(left, right)

    assert result.outcome == "exact_same_facility"
    assert result.should_merge is True
    assert result.should_flag_possible_duplicate is False


def test_different_treatment_addresses_stay_separate_even_if_ai_says_merge():
    left = BranchIdentityInput(
        canonical_name="Hope Recovery",
        address="100 North Street, Lyon",
        postal_code="69001",
        phone_values=["+33412345678"],
        website_host="hope.example",
        parent_org_name="Hope Recovery",
    )
    right = BranchIdentityInput(
        canonical_name="Hope Recovery",
        address="200 South Street, Marseille",
        postal_code="13001",
        phone_values=["+33499887766"],
        website_host="hope.example",
        parent_org_name="Hope Recovery",
    )

    result = branch_identity_service.compare(
        left,
        right,
        ai_recommendation="exact_same_facility",
    )

    assert result.outcome == "same_parent_different_branch"
    assert result.should_merge is False


def test_clearly_distinct_facilities_are_not_flagged_for_merge():
    left = BranchIdentityInput(
        canonical_name="Centre Alpha",
        address="Paris, France",
        postal_code="75001",
        phone_values=["+33122334455"],
        website_host="alpha.example",
    )
    right = BranchIdentityInput(
        canonical_name="Centre Alpha",
        address="Berlin, Germany",
        postal_code="10115",
        phone_values=["+4930123456"],
        website_host="beta.example",
    )

    result = branch_identity_service.compare(left, right)

    assert result.outcome == "clearly_distinct"
    assert result.should_merge is False
    assert result.should_flag_possible_duplicate is False


async def test_publication_does_not_merge_same_name_branches_with_different_addresses(
    db: AsyncSession, auth
):
    execution = await create_execution(db, auth)
    first = await create_staged_candidate(
        db,
        auth,
        execution,
        name="Hope Recovery",
        extra_evidence=[
            ("addresses", "10 Rue Exemple, Paris", "10 Rue Exemple, Paris", "verified"),
            ("phones", "+33 1 22 33 44 55", "+33 1 22 33 44 55", "verified"),
            ("websites", "https://hope.example/paris", "https://hope.example/paris", "verified"),
        ],
    )

    first_summary = await publish(db, auth, execution, first)
    second = await create_staged_candidate(
        db,
        auth,
        execution,
        name="Hope Recovery",
        extra_evidence=[
            ("addresses", "22 Avenue Example, Lyon", "22 Avenue Example, Lyon", "verified"),
            ("phones", "+33 4 72 00 00 00", "+33 4 72 00 00 00", "verified"),
            ("websites", "https://hope.example/lyon", "https://hope.example/lyon", "verified"),
        ],
    )
    second_summary = await publish(db, auth, execution, second)

    assert first_summary.final_facility_id != second_summary.final_facility_id
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 2
