"""User-run guarded Package A extraction/verification; never publishes facilities."""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from sqlalchemy import func, select

from app.db.models import (
    ScrapingExecution,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateDecision,
    ScrapingFacilityCandidateDuplicate,
    ScrapingFacilityCandidateEvidence,
    ScrapingFacilityCandidatePublication,
    ScrapingFacilityExtractionAttempt,
    ScrapingFacilityPhaseWorkJob,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
    ScrapingSourceDocumentText,
    ScrapingSourceCandidate,
    ScrapingPhase5RetrievalResult,
    ScrapingDirectoryObservation,
    ScrapingCrawlNode,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.facility_phase_execution_service import run_work_slice
from app.services.scraping.facility_phase_orchestration_service import seed_document_preparation

KNOWN_ZERO_CANDIDATE_EXECUTIONS = {"bdda236a-9810-47f4-b2f6-2bf24cd48b90"}
TEXT_REPRESENTATIONS = {
    "text/html", "application/xhtml+xml", "text/plain", "application/json",
    "application/xml", "text/xml",
}
FACILITY_SIGNAL = re.compile(
    r"\b(?:rehab(?:ilitation)?|treatment (?:center|centre|facility)|detox|clinic|hospital)\b",
    re.IGNORECASE,
)
ADDRESS_SIGNAL = re.compile(
    r"\b(?:address|street|st\.|road|rd\.|avenue|ave\.|building|postal|zip)\b",
    re.IGNORECASE,
)
CONTACT_SIGNAL = re.compile(
    r"(?:\b(?:phone|telephone|email|contact|website|license|licence|accredit)\b|"
    r"\+?\d[\d\s().-]{6,}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)


def rank_smoke_target(item: dict) -> tuple[int, str, list[str]]:
    warnings: list[str] = []
    if item["execution_id"] in KNOWN_ZERO_CANDIDATE_EXECUTIONS:
        return -10_000, "known_zero_candidate_source", ["excluded_known_zero_candidate"]
    if item["execution_status"] in {
        "queued", "running", "pause_requested", "cancel_requested"
    }:
        return -10_000, "unsafe_active_execution", ["excluded_active_execution"]
    if item["retrieval_failure"] or not item["retrieval_result_id"]:
        return -10_000, "failed_or_missing_retrieval", ["excluded_failed_or_empty_retrieval"]
    if item["text_character_count"] < 200:
        return -10_000, "insufficient_text", ["excluded_empty_or_too_short"]
    if item["representation_type"] not in TEXT_REPRESENTATIONS:
        return -10_000, "unsupported_representation", ["excluded_unsupported_representation"]
    if item["document_count"] > 100:
        return -10_000, "very_large_execution", ["excluded_very_large_execution"]
    if item["existing_candidate_count"]:
        return -10_000, "candidate_already_extracted", ["excluded_existing_candidate"]
    score = 0
    score += 40 if item["execution_status"] == "paused" else 10
    score += 35 if item["document_count"] <= 3 else max(0, 20 - item["document_count"])
    score += min(item["facility_signals"], 5) * 8
    score += min(item["address_signals"], 5) * 7
    score += min(item["contact_signals"], 5) * 6
    score += 12 if item["source_classification"] == "facility_profile" else 0
    score += 8 if item["directory_observations"] else 0
    score -= min(item["existing_candidate_count"] * 10, 30)
    if item["text_character_count"] > 200_000:
        score -= 30
        warnings.append("large_text_document")
    if item["document_count"] > 3:
        warnings.append("execution_has_more_than_three_documents")
    if item["existing_attempt_count"]:
        warnings.append("package_a_attempt_already_exists")
    if item["facility_signals"] and item["address_signals"] and item["contact_signals"]:
        reason = "explicit_address_and_contact_signals"
    elif item["source_classification"] == "facility_profile" and item["document_count"] == 1:
        reason = "single_official_facility_profile"
    elif item["directory_observations"] and item["facility_signals"] > 1:
        reason = "structured_multi_facility_page"
    else:
        reason = "small_paused_execution"
    return score, reason, warnings


def validate_discovery_request(
    organization_id: str | None, country: str | None, limit: int
) -> tuple[str, str | None, int]:
    if not organization_id:
        raise ValueError("organization_id_required")
    if not 1 <= limit <= 100:
        raise ValueError("limit_out_of_range")
    normalized_country = country.strip().upper() if country else None
    if normalized_country and (
        len(normalized_country) != 2 or not normalized_country.isalpha()
    ):
        raise ValueError("country_must_be_iso2")
    return organization_id, normalized_country, limit


async def discover_smoke_targets(
    db, *, organization_id: str, country: str | None, limit: int
) -> list[dict]:
    execution_query = select(ScrapingExecution).where(
        ScrapingExecution.organization_id == organization_id,
        ScrapingExecution.id.not_in(KNOWN_ZERO_CANDIDATE_EXECUTIONS),
    )
    if country:
        execution_query = execution_query.where(
            ScrapingExecution.country_code == country.upper()
        )
    executions = list((await db.execute(execution_query)).scalars())
    if not executions:
        return []
    execution_map = {row.id: row for row in executions}
    execution_ids = list(execution_map)
    documents = list((await db.execute(
        select(ScrapingSourceDocument).where(
            ScrapingSourceDocument.organization_id == organization_id,
            ScrapingSourceDocument.execution_id.in_(execution_ids),
            func.length(ScrapingSourceDocument.content_text) >= 200,
        ).order_by(ScrapingSourceDocument.retrieval_timestamp.desc()).limit(2000)
    )).scalars())
    if not documents:
        return []
    candidate_ids = {row.source_candidate_id for row in documents}
    source_candidates = {
        row.id: row for row in (await db.execute(
            select(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.organization_id == organization_id,
                ScrapingSourceCandidate.id.in_(candidate_ids),
            )
        )).scalars()
    }
    crawl_node_ids = {
        row.crawl_node_id for row in source_candidates.values() if row.crawl_node_id
    }
    crawl_nodes = {
        row.id: row for row in (await db.execute(
            select(ScrapingCrawlNode).where(
                ScrapingCrawlNode.organization_id == organization_id,
                ScrapingCrawlNode.execution_id.in_(execution_ids),
                ScrapingCrawlNode.id.in_(crawl_node_ids),
            )
        )).scalars()
    } if crawl_node_ids else {}
    document_ids = [row.id for row in documents]
    retrievals = {
        row.source_document_id: row for row in (await db.execute(
            select(ScrapingPhase5RetrievalResult).where(
                ScrapingPhase5RetrievalResult.organization_id == organization_id,
                ScrapingPhase5RetrievalResult.execution_id.in_(execution_ids),
                ScrapingPhase5RetrievalResult.source_document_id.in_(document_ids),
                ScrapingPhase5RetrievalResult.failure_category.is_(None),
            ).order_by(ScrapingPhase5RetrievalResult.result_ordinal)
        )).scalars()
    }
    async def grouped_count(model, key):
        rows = (await db.execute(
            select(key, func.count()).where(
                model.organization_id == organization_id,
                model.execution_id.in_(execution_ids),
            ).group_by(key)
        )).all()
        return {value: int(total) for value, total in rows}
    document_counts = await grouped_count(
        ScrapingSourceDocument, ScrapingSourceDocument.execution_id)
    observations = await grouped_count(
        ScrapingDirectoryObservation, ScrapingDirectoryObservation.execution_id)
    attempts = await grouped_count(
        ScrapingFacilityExtractionAttempt, ScrapingFacilityExtractionAttempt.source_document_id)
    candidates = await grouped_count(
        ScrapingFacilityCandidate, ScrapingFacilityCandidate.source_document_id)
    decisions = {
        document_id: int(total) for document_id, total in (await db.execute(
            select(ScrapingFacilityCandidate.source_document_id, func.count())
            .join(
                ScrapingFacilityCandidateDecision,
                ScrapingFacilityCandidateDecision.facility_candidate_id
                == ScrapingFacilityCandidate.id,
            ).where(
                ScrapingFacilityCandidate.organization_id == organization_id,
                ScrapingFacilityCandidate.execution_id.in_(execution_ids),
            ).group_by(ScrapingFacilityCandidate.source_document_id)
        )).all()
    }
    ranked: list[tuple[int, dict]] = []
    for document in documents:
        execution = execution_map[document.execution_id]
        source = source_candidates.get(document.source_candidate_id)
        retrieval = retrievals.get(document.id)
        text = document.content_text or ""
        item = {
            "organization_id": organization_id,
            "execution_id": execution.id,
            "execution_status": getattr(execution.status, "value", execution.status),
            "country_code": execution.country_code,
            "source_document_id": document.id,
            "retrieval_result_id": retrieval.id if retrieval else None,
            "crawl_node_id": source.crawl_node_id if source else None,
            "source_url": document.final_url,
            "representation_type": (document.content_type or "").split(";", 1)[0].lower(),
            "text_character_count": len(text),
            "document_count": document_counts[document.execution_id],
            "directory_observations": observations.get(document.execution_id, 0),
            "existing_attempt_count": attempts.get(document.id, 0),
            "existing_candidate_count": candidates.get(document.id, 0),
            "existing_decision_count": decisions.get(document.id, 0),
            "source_classification": (
                getattr(
                    crawl_nodes[source.crawl_node_id].source_classification,
                    "value",
                    crawl_nodes[source.crawl_node_id].source_classification,
                )
                if source and source.crawl_node_id in crawl_nodes else None
            ),
            "retrieval_failure": retrieval.failure_category if retrieval else "missing",
            "facility_signals": len(FACILITY_SIGNAL.findall(text)),
            "address_signals": len(ADDRESS_SIGNAL.findall(text)),
            "contact_signals": len(CONTACT_SIGNAL.findall(text)),
        }
        score, reason, warnings = rank_smoke_target(item)
        if score < 0:
            continue
        safe = {key: value for key, value in item.items() if key not in {
            "facility_signals", "address_signals", "contact_signals",
            "retrieval_failure", "source_classification",
        }}
        safe["recommendation_reason"] = reason
        safe["warnings"] = warnings
        safe["signal_counts"] = {
            "facility": item["facility_signals"],
            "address": item["address_signals"],
            "contact": item["contact_signals"],
        }
        ranked.append((score, safe))
    ranked.sort(key=lambda row: (-row[0], row[1]["execution_id"], row[1]["source_document_id"]))
    result = []
    for rank, (_, item) in enumerate(ranked[:limit], 1):
        result.append({"rank": rank, **item})
    return result

def terminal_explanation(summary: dict) -> str:
    if summary["source_documents"] == 0:
        return "no_source_documents"
    if summary["failed_work"]:
        return "blocked_by_failed_work"
    pending = summary["pending_work_by_kind"]
    if summary["prepared_documents"] < summary["source_documents"] or pending.get("prepare_document"):
        return "preparation_pending"
    if pending.get("extract_chunk"):
        return "extraction_pending"
    if summary["extracted_candidates"] == 0:
        return "no_facilities_extracted"
    if summary["country_decisions"] < summary["extracted_candidates"] or pending.get("verify_candidate"):
        return "verification_pending"
    dedup_seeded = any(
        key.startswith("deduplicate_candidate:")
        for key in summary["work_jobs_by_kind_and_status"]
    )
    if pending.get("deduplicate_candidate") or not dedup_seeded:
        return "deduplication_pending"
    return "package_a_complete_with_candidates"


async def execution_summary(db, *, organization_id: str, execution_id: str) -> dict:
    owned = (
        ScrapingExecution.organization_id == organization_id,
        ScrapingExecution.id == execution_id,
    )
    if not await db.scalar(select(ScrapingExecution.id).where(*owned)):
        raise SystemExit("Owned execution not found")

    async def count(model) -> int:
        return int(await db.scalar(select(func.count()).select_from(model).where(
            model.organization_id == organization_id,
            model.execution_id == execution_id,
        )) or 0)

    attempt_groups = (await db.execute(
        select(ScrapingFacilityExtractionAttempt.status, func.count())
        .where(
            ScrapingFacilityExtractionAttempt.organization_id == organization_id,
            ScrapingFacilityExtractionAttempt.execution_id == execution_id,
        ).group_by(ScrapingFacilityExtractionAttempt.status)
    )).all()
    attempts_by_status = {
        getattr(status, "value", status): int(total) for status, total in attempt_groups
    }
    relevance_rows = (await db.execute(
        select(ScrapingFacilityExtractionAttempt.metadata_json).where(
            ScrapingFacilityExtractionAttempt.organization_id == organization_id,
            ScrapingFacilityExtractionAttempt.execution_id == execution_id,
        )
    )).scalars().all()
    relevant = sum(item.get("document_relevant") is True for item in relevance_rows)
    irrelevant = sum(item.get("document_relevant") is False for item in relevance_rows)
    attempt_rows = (await db.execute(
        select(ScrapingFacilityExtractionAttempt).where(
            ScrapingFacilityExtractionAttempt.organization_id == organization_id,
            ScrapingFacilityExtractionAttempt.execution_id == execution_id,
        ).order_by(ScrapingFacilityExtractionAttempt.requested_at)
    )).scalars().all()
    job_groups = (await db.execute(
        select(
            ScrapingFacilityPhaseWorkJob.work_kind,
            ScrapingFacilityPhaseWorkJob.status,
            func.count(),
        ).where(
            ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
            ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ).group_by(
            ScrapingFacilityPhaseWorkJob.work_kind,
            ScrapingFacilityPhaseWorkJob.status,
        )
    )).all()
    jobs = {f"{kind}:{status}": int(total) for kind, status, total in job_groups}
    job_rows = (await db.execute(
        select(ScrapingFacilityPhaseWorkJob).where(
            ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
            ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ).order_by(ScrapingFacilityPhaseWorkJob.created_at)
    )).scalars().all()
    pending_statuses = {"pending", "running", "retry_scheduled"}
    pending_by_kind: dict[str, int] = {}
    for kind, status, total in job_groups:
        if status in pending_statuses:
            pending_by_kind[kind] = pending_by_kind.get(kind, 0) + int(total)
    decision_groups = (await db.execute(
        select(ScrapingFacilityCandidateDecision.final_status, func.count()).where(
            ScrapingFacilityCandidateDecision.organization_id == organization_id,
            ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ).group_by(ScrapingFacilityCandidateDecision.final_status)
    )).all()
    decisions = {status: int(total) for status, total in decision_groups}
    failed_work = int(await db.scalar(select(func.count()).select_from(
        ScrapingFacilityPhaseWorkJob
    ).where(
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.status == "failed",
    )) or 0)
    summary = {
        "source_documents": await count(ScrapingSourceDocument),
        "prepared_documents": await count(ScrapingSourceDocumentText),
        "chunks": await count(ScrapingSourceDocumentChunk),
        "extraction_attempts_by_status": attempts_by_status,
        "extraction_attempt_details": [{
            "id": row.id,
            "status": getattr(row.status, "value", row.status),
            "document_relevant": (row.metadata_json or {}).get("document_relevant"),
            "provider": row.provider,
            "model": row.model,
            "output_candidate_count": row.output_candidate_count,
            "failure_classification": row.failure_classification,
        } for row in attempt_rows],
        "relevant_extraction_attempts": relevant,
        "irrelevant_extraction_attempts": irrelevant,
        "extracted_candidates": await count(ScrapingFacilityCandidate),
        "candidate_evidence": await count(ScrapingFacilityCandidateEvidence),
        "country_decisions": await count(ScrapingFacilityCandidateDecision),
        "accepted": decisions.get("accepted", 0),
        "needs_review": decisions.get("needs_review", 0),
        "rejected": decisions.get("rejected", 0),
        "duplicate_relationships": await count(ScrapingFacilityCandidateDuplicate),
        "work_jobs_by_kind_and_status": jobs,
        "work_job_details": [{
            "id": row.id,
            "work_kind": row.work_kind,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "failure_classification": row.failure_classification,
        } for row in job_rows],
        "pending_work_by_kind": pending_by_kind,
        "failed_work": failed_work,
        "publication_invoked": bool(await count(ScrapingFacilityCandidatePublication)),
        "excel_invoked_by_guarded_runner": False,
    }
    summary["terminal_explanation"] = terminal_explanation(summary)
    return summary


async def print_summary(db, *, organization_id: str, execution_id: str) -> None:
    summary = await execution_summary(
        db, organization_id=organization_id, execution_id=execution_id
    )
    print(json.dumps(summary, sort_keys=True, indent=2))


async def run(execution_id: str, *, confirm: bool, max_slices: int) -> None:
    async with AsyncSessionLocal() as db:
        execution = await db.scalar(select(ScrapingExecution).where(
            ScrapingExecution.id == execution_id
        ))
        if execution is None:
            raise SystemExit("Execution not found")
        print({
            "execution_id": execution.id,
            "organization_id": execution.organization_id,
            "country": execution.country_code,
            "status": execution.status.value,
            "action": "seed and run Phase 6/7 only; publication disabled",
        })
        if not confirm:
            print("Preview only. Re-run with --confirm to perform provider-backed extraction.")
            await print_summary(
                db, organization_id=execution.organization_id, execution_id=execution.id
            )
            return
        created = await seed_document_preparation(
            db, organization_id=execution.organization_id, execution_id=execution.id
        )
        print({"preparation_jobs_created": created})
        for index in range(max_slices):
            summary = await run_work_slice(
                db, organization_id=execution.organization_id, execution_id=execution.id
            )
            print({"slice": index + 1, **summary})
            remaining = await db.scalar(select(func.count()).select_from(
                ScrapingFacilityPhaseWorkJob
            ).where(
                ScrapingFacilityPhaseWorkJob.organization_id == execution.organization_id,
                ScrapingFacilityPhaseWorkJob.execution_id == execution.id,
                ScrapingFacilityPhaseWorkJob.status.in_(("pending", "running", "retry_scheduled")),
            ))
            if not remaining:
                break
        else:
            print("Slice guard reached; re-run safely to continue. No campaign result ceiling was applied.")
        await print_summary(
            db, organization_id=execution.organization_id, execution_id=execution.id
        )


async def list_smoke_targets(
    *, organization_id: str, country: str | None, limit: int
) -> None:
    organization_id, country, limit = validate_discovery_request(
        organization_id, country, limit
    )
    async with AsyncSessionLocal() as db:
        targets = await discover_smoke_targets(
            db, organization_id=organization_id, country=country, limit=limit
        )
        if not targets:
            print("no_suitable_existing_smoke_target")
            print(
                "Safest fallback: preview an explicit official facility-profile crawl node with "
                "`python -m scripts.phase5_guarded_smoke --organization-id <ORG_ID> "
                "--execution-id <SMALL_PAUSED_EXECUTION_ID> --crawl-node-id <PROFILE_NODE_ID>`; "
                "add `--confirm-http` only after reviewing that preview."
            )
            return
        print(json.dumps({"smoke_targets": targets}, sort_keys=True, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("execution_id", nargs="?")
    parser.add_argument("--list-smoke-targets", action="store_true")
    parser.add_argument("--organization-id")
    parser.add_argument("--country")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--max-slices", type=int, default=1)
    args = parser.parse_args()
    if args.list_smoke_targets:
        if args.confirm:
            parser.error("--confirm cannot be combined with --list-smoke-targets")
        try:
            validate_discovery_request(args.organization_id, args.country, args.limit)
        except ValueError as exc:
            parser.error(str(exc))
        asyncio.run(list_smoke_targets(
            organization_id=args.organization_id,
            country=args.country,
            limit=args.limit,
        ))
        return
    if not args.execution_id:
        parser.error("execution_id is required unless --list-smoke-targets is used")
    if args.max_slices < 1:
        raise SystemExit("--max-slices must be at least 1")
    asyncio.run(run(args.execution_id, confirm=args.confirm, max_slices=args.max_slices))


if __name__ == "__main__":
    main()
