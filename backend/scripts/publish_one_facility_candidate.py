"""Publish one verified staged facility candidate into the final dataset."""

from __future__ import annotations

import argparse
import asyncio

from app.db.session import AsyncSessionLocal
from app.services.scraping.facility_candidate_publication_service import (
    FacilityCandidatePublicationContext,
    facility_candidate_publication_service,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--facility-candidate-id", required=True)
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        summary = await facility_candidate_publication_service.publish_one_candidate(
            db,
            FacilityCandidatePublicationContext(
                organization_id=args.organization_id,
                execution_id=args.execution_id,
                facility_candidate_id=args.facility_candidate_id,
            ),
        )
        print(f"publication_status={summary.status}")
        print(f"publication_reason_code={summary.reason_code or ''}")
        print(f"candidate_id={summary.candidate_id}")
        print(f"publication_id={summary.publication_id}")
        print(f"final_facility_id={summary.final_facility_id or ''}")
        print(f"normalized_facility_name={summary.normalized_facility_name or ''}")
        print(f"country_code={summary.country_code or ''}")
        print(f"aliases_created={summary.aliases_created}")
        print(f"locations_created={summary.locations_created}")
        print(f"contacts_created={summary.contacts_created}")
        print(f"sources_linked={summary.sources_linked}")
        print(f"field_evidence_created={summary.field_evidence_created}")
        print(f"unresolved_fields_created={summary.unresolved_fields_created}")
        print(f"reused_existing_publication={summary.reused_existing_publication}")


if __name__ == "__main__":
    asyncio.run(main())
