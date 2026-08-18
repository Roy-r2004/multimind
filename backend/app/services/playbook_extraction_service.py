"""Playbook extraction: render, model calls, JSON parse, validation, consolidation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.services.playbook_source_service import (
    PlaybookBrainSnapshot,
    PlaybookChatTranscript,
    PlaybookExtractionBatch,
    PlaybookTurnSource,
    canonical_dumps,
)

logger = get_logger(__name__)

PLAYBOOK_CATEGORIES = frozenset(
    {
        "preference",
        "project",
        "architecture",
        "decision",
        "plan",
        "completed_work",
        "blocker",
        "priority",
        "next_step",
        "rejected_option",
        "superseded_information",
        "relationship",
        "important_fact",
    }
)
PLAYBOOK_STATUSES = frozenset(
    {
        "active",
        "confirmed",
        "planned",
        "completed",
        "rejected",
        "superseded",
        "uncertain",
    }
)
PLAYBOOK_SOURCE_KINDS = frozenset(
    {
        "user_message",
        "council_answer",
        "verdict",
        "lesson",
        "brain",
        "attachment",
        "referenced_chat_handoff",
    }
)
PLAYBOOK_EPISTEMIC_ROLES = frozenset(
    {
        "user_stated",
        "user_confirmed",
        "ai_suggested",
        "selected",
        "planned",
        "implemented",
        "tested",
        "completed",
        "rejected",
        "superseded",
        "uncertain",
    }
)
CONFIRMING_ROLES = frozenset(
    {
        "user_confirmed",
        "selected",
        "implemented",
        "tested",
        "completed",
        "rejected",
        "superseded",
    }
)
CONFIRMED_STATUS_ROLES = frozenset(
    {
        "user_stated",
        "user_confirmed",
        "selected",
        "implemented",
        "tested",
        "completed",
    }
)
COMPLETED_STATUS_ROLES = frozenset({"implemented", "tested", "completed"})

SECRET_RE = re.compile(
    r"(?i)(api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+|secret\s*[:=]\s*\S+"
    r"|access[_-]?token\s*[:=]\s*\S+|bearer\s+[a-z0-9\-._~+/]+=*"
    r"|sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,})"
)

EXTRACT_MAX_TOKENS = 4096
CONSOLIDATE_MAX_TOKENS = 4096
SUMMARY_MAX_TOKENS = 1200
COMPRESS_MAX_TOKENS = 800
QUOTE_MAX_CHARS = 400
SUBJECT_MAX_CHARS = 512
OBSERVATION_MAX_CHARS = 4000
CONSOLIDATION_CHUNK_SIZE = 40
MAX_CONSOLIDATION_ROUNDS = 6
REPAIR_PREVIOUS_MAX_CHARS = 4000
UNCERTAIN_CONFIDENCE_CAP = 0.79

USER_EVIDENCE_KINDS = frozenset({"user_message", "lesson"})
AI_EVIDENCE_KINDS = frozenset({"council_answer", "verdict"})
DOCUMENT_EVIDENCE_KINDS = frozenset({"attachment"})
USER_ADOPTION_ROLES = frozenset({"user_confirmed", "selected"})
USER_INTENT_ROLES = frozenset({"user_confirmed", "selected", "planned"})
USER_COMPLETION_ROLES = frozenset({"implemented", "tested", "completed"})
USER_REJECTION_ROLES = frozenset({"rejected", "superseded"})
USER_PROJECT_ROLES = frozenset(
    {"user_stated", "user_confirmed", "selected", "planned", "implemented", "tested", "completed"}
)
USER_GROUNDED_CATEGORIES = frozenset(
    {"decision", "plan", "priority", "next_step", "rejected_option"}
)

EPHEMERAL_RE = re.compile(
    r"(?i)\b(this session|this chat|for today|for now|temporar(?:y|ily)|"
    r"just for this conversation|session[- ]only)\b"
)
LOW_INFORMATION_RE = re.compile(
    r"(?i)\b(change something|something (?:will|may|might) change|"
    r"not (?:yet )?specified|unspecified (?:change|decision)|details? (?:pending|unknown)|"
    r"no further (?:context|details?)(?: (?:is|are) provided)?)\b"
)
EXPLICIT_PREFERENCE_RE = re.compile(
    r"(?i)\b(i prefer|i like|i dislike|i want (?:answers?|responses?)|"
    r"please (?:always|avoid)|my preference|do not|don't)\b"
)
SOURCE_ATTRIBUTION_RE = re.compile(
    r"(?i)\b(document|source|attachment|report|specification|proposal|referenced)\b"
)
USER_ADOPTION_RE = re.compile(
    r"(?i)\b(i|we)\s+(?:decided|choose|chose|selected|approved|accept|are adopting|will use)\b|"
    r"\b(?:yes|okay|ok|good),?\s+(?:let(?:'s| us)|we(?:'ll| will))\b|\blet(?:'s| us) use\b"
)
USER_COMPLETION_RE = re.compile(
    r"(?i)\b(i|we)\s+(?:built|completed|finished|implemented|deployed|merged|tested|fixed)\b|"
    r"\b(?:is|are|was|were) (?:built|complete|completed|working|deployed|merged|tested|fixed)\b"
)
USER_REJECTION_RE = re.compile(
    r"(?i)\b(i|we)\s+(?:reject|rejected|decline|declined|abandoned|dropped)\b|"
    r"\b(?:do not|don't|won't|will not) (?:use|adopt|choose)\b"
)


@dataclass
class ExtractionWarning:
    code: str
    message: str
    chat_id: str | None = None
    turn_id: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class ValidatedEvidence:
    source_kind: str
    epistemic_role: str
    quote: str
    chat_id: str | None = None
    turn_id: str | None = None
    source_created_at: datetime | None = None
    brain_source_id: str | None = None


@dataclass
class ExtractedCandidate:
    candidate_id: str
    category: str
    subject: str | None
    observation: str
    status: str
    confidence: float
    evidence: tuple[ValidatedEvidence, ...]
    created_at: datetime | None = None
    source_candidate_ids: tuple[str, ...] = ()


@dataclass
class CanonicalObservation:
    category: str
    subject: str | None
    observation: str
    status: str
    confidence: float
    candidate_ids: tuple[str, ...]
    evidence: tuple[ValidatedEvidence, ...]
    first_observed_at: datetime | None = None
    last_confirmed_at: datetime | None = None


@dataclass
class BatchExtractionResult:
    candidates: list[ExtractedCandidate]
    warnings: list[ExtractionWarning]
    succeeded: bool


@dataclass
class SourceCatalogue:
    turns: dict[tuple[str, str], PlaybookTurnSource]
    chats: dict[str, PlaybookChatTranscript]
    brain_ids: set[str]
    brain_timestamps: dict[str, datetime | None]


class PlaybookExtractionService:
    def render_extraction_input(
        self,
        batch: PlaybookExtractionBatch,
        brain: PlaybookBrainSnapshot | None,
        *,
        include_brain: bool = True,
    ) -> str:
        parts: list[str] = []
        if include_brain and brain is not None:
            parts.append(self._render_brain(brain))
        for chat in batch.chats:
            parts.append(self._render_chat(chat))
        return "\n\n".join(part for part in parts if part.strip())

    def render_brain_only_input(self, brain: PlaybookBrainSnapshot) -> str:
        return self._render_brain(brain)

    async def extract_batch(
        self,
        batch: PlaybookExtractionBatch,
        brain: PlaybookBrainSnapshot | None,
        *,
        include_brain: bool = True,
        source_text: str | None = None,
    ) -> BatchExtractionResult:
        catalogue = build_source_catalogue(batch, brain)
        text = source_text or self.render_extraction_input(
            batch, brain, include_brain=include_brain
        )
        system = get_prompt_engine().render(
            "playbooks/extract_batch.j2",
            categories=sorted(PLAYBOOK_CATEGORIES),
            statuses=sorted(PLAYBOOK_STATUSES),
            source_kinds=sorted(PLAYBOOK_SOURCE_KINDS),
            epistemic_roles=sorted(PLAYBOOK_EPISTEMIC_ROLES),
        )
        payload, warnings = await self._request_json(
            system,
            text,
            max_tokens=EXTRACT_MAX_TOKENS,
            expect_object=True,
        )
        if payload is None:
            return BatchExtractionResult(candidates=[], warnings=warnings, succeeded=False)
        candidates, extra = self.validate_batch_payload(payload, catalogue)
        warnings.extend(extra)
        return BatchExtractionResult(
            candidates=candidates, warnings=warnings, succeeded=True
        )

    def validate_batch_payload(
        self, payload: dict[str, Any], catalogue: SourceCatalogue
    ) -> tuple[list[ExtractedCandidate], list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        raw_obs = payload.get("observations")
        if not isinstance(raw_obs, list):
            warnings.append(
                ExtractionWarning(
                    code="invalid_extraction_payload",
                    message="Batch extraction JSON is missing an observations list.",
                )
            )
            return [], warnings
        candidates: list[ExtractedCandidate] = []
        for item in raw_obs:
            candidate, extra = self._validate_candidate(item, catalogue)
            warnings.extend(extra)
            if candidate is not None:
                candidates.append(candidate)
        return candidates, warnings

    async def consolidate_candidates(
        self, candidates: Sequence[ExtractedCandidate]
    ) -> tuple[list[CanonicalObservation], list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        current = merge_duplicate_candidates(candidates)
        if not current:
            return [], warnings
        original_by_id = {item.candidate_id: item for item in current}
        final: list[CanonicalObservation] = []

        for round_index in range(1, MAX_CONSOLIDATION_ROUNDS + 1):
            input_count = len(current)
            chunks = chunk_candidates(current, CONSOLIDATION_CHUNK_SIZE)
            if len(chunks) > 1:
                warnings.append(
                    ExtractionWarning(
                        code="consolidation_chunked",
                        message=f"Consolidated {input_count} candidates in {len(chunks)} chunks.",
                    )
                )
            round_output: list[CanonicalObservation] = []
            round_map = _candidate_lineage_map(current)
            for chunk_index, chunk in enumerate(chunks, start=1):
                result, extra = await self._consolidate_chunk(
                    chunk, round_map, original_by_id=original_by_id
                )
                warnings.extend(extra)
                if result is None:
                    reason_code = _consolidation_failure_reason(extra)
                    result = [_candidate_as_canonical(item) for item in chunk]
                    warnings.append(
                        ExtractionWarning(
                            code="consolidation_chunk_passthrough",
                            message="A structurally invalid consolidation chunk was kept as-is.",
                        )
                    )
                    logger.warning(
                        "playbook_consolidation_event",
                        reason_code=reason_code,
                        round_index=round_index,
                        chunk_index=chunk_index,
                        chunk_count=len(chunks),
                        input_candidate_count=len(chunk),
                        output_candidate_count=len(result),
                        passthrough_count=len(chunk),
                    )
                round_output.extend(result)

            next_candidates = merge_duplicate_candidates(
                [_canonical_as_candidate(item, original_by_id) for item in round_output]
            )
            output_count = len(next_candidates)
            final = [_candidate_as_canonical(item) for item in next_candidates]
            if input_count <= CONSOLIDATION_CHUNK_SIZE or output_count <= CONSOLIDATION_CHUNK_SIZE:
                break
            if output_count >= input_count:
                warnings.append(
                    ExtractionWarning(
                        code="consolidation_no_progress",
                        message="Hierarchical consolidation stopped because candidate count did not decrease.",
                    )
                )
                logger.warning(
                    "playbook_consolidation_event",
                    reason_code="no_progress",
                    round_index=round_index,
                    chunk_index=None,
                    chunk_count=len(chunks),
                    input_candidate_count=input_count,
                    output_candidate_count=output_count,
                    passthrough_count=0,
                )
                break
            current = next_candidates
        else:
            warnings.append(
                ExtractionWarning(
                    code="consolidation_max_rounds_reached",
                    message="Hierarchical consolidation stopped at the configured round limit.",
                )
            )
            logger.warning(
                "playbook_consolidation_event",
                reason_code="max_rounds_reached",
                round_index=MAX_CONSOLIDATION_ROUNDS,
                chunk_index=None,
                chunk_count=len(chunk_candidates(current, CONSOLIDATION_CHUNK_SIZE)),
                input_candidate_count=len(current),
                output_candidate_count=len(final),
                passthrough_count=0,
            )

        validated: list[CanonicalObservation] = []
        for item in final:
            ok, extra = self.validate_canonical(item, original_by_id)
            warnings.extend(extra)
            if ok is not None:
                validated.append(ok)
        return validated, warnings

    def validate_canonical(
        self, item: CanonicalObservation, by_id: dict[str, ExtractedCandidate]
    ) -> tuple[CanonicalObservation | None, list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        category = normalize_token(item.category)
        status = normalize_token(item.status)
        observation = (item.observation or "").strip()
        if category not in PLAYBOOK_CATEGORIES or status not in PLAYBOOK_STATUSES:
            warnings.append(
                ExtractionWarning(
                    code="invalid_canonical_observation",
                    message="Dropped a final observation with an unsupported category or status.",
                )
            )
            return None, warnings
        if not observation or looks_like_secret(observation):
            warnings.append(
                ExtractionWarning(
                    code="invalid_canonical_observation",
                    message="Dropped a final observation with empty or secret-like text.",
                )
            )
            return None, warnings
        candidate_ids = tuple(
            cid for cid in dict.fromkeys(item.candidate_ids) if cid in by_id
        )
        supporting = [by_id[cid] for cid in candidate_ids]
        if not supporting:
            warnings.append(
                ExtractionWarning(
                    code="unsupported_candidate_reference",
                    message="Dropped a final observation without real candidate references.",
                )
            )
            return None, warnings
        evidence = dedupe_evidence(
            ev for cand in supporting for ev in cand.evidence
        )
        if not evidence:
            warnings.append(
                ExtractionWarning(
                    code="invalid_canonical_observation",
                    message="Dropped a final observation with no validated evidence.",
                )
            )
            return None, warnings
        confidence = supporting[0].confidence
        try:
            confidence = validate_confidence(item.confidence)
        except ValueError:
            warnings.append(
                ExtractionWarning(
                    code="invalid_canonical_confidence",
                    message="Dropped a final observation with invalid confidence.",
                )
            )
            return None, warnings
        guarded, extra = apply_epistemic_guard(
            category=category,
            status=status,
            confidence=confidence,
            subject=item.subject or supporting[0].subject,
            observation=observation,
            evidence=evidence,
        )
        warnings.extend(extra)
        if guarded is None:
            return None, warnings
        category, status, confidence = guarded
        first_at = min(
            (ev.source_created_at for ev in evidence if ev.source_created_at is not None),
            default=None,
        )
        last_conf = max(
            (
                ev.source_created_at
                for ev in evidence
                if ev.source_created_at is not None and ev.epistemic_role in CONFIRMING_ROLES
            ),
            default=None,
        )
        subject = (item.subject or supporting[0].subject or "")[:SUBJECT_MAX_CHARS] or None
        return (
            CanonicalObservation(
                category=category,
                subject=subject,
                observation=observation[:OBSERVATION_MAX_CHARS],
                status=status,
                confidence=confidence,
                candidate_ids=candidate_ids,
                evidence=tuple(evidence),
                first_observed_at=first_at,
                last_confirmed_at=last_conf,
            ),
            warnings,
        )

    async def generate_core_summary(
        self, observations: Sequence[CanonicalObservation]
    ) -> tuple[str, list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        settings = get_settings()
        max_chars = int(settings.playbook_core_summary_max_chars or 4000)
        if not observations:
            raise PlaybookExtractionError("Cannot render a core summary without observations.")
        selected = [item for item in observations if _is_core_summary_worthy(item)]
        if not selected:
            selected = list(observations)
        elif len(selected) < len(observations):
            warnings.append(
                ExtractionWarning(
                    code="core_summary_observations_filtered",
                    message="Lower-value or uncertain observations were omitted from the core summary input.",
                )
            )
        system = get_prompt_engine().render(
            "playbooks/render_core_summary.j2",
            max_chars=max_chars,
            observations=[
                {
                    "category": item.category,
                    "status": item.status,
                    "subject": item.subject or "",
                    "observation": item.observation,
                }
                for item in selected
            ],
        )
        text = (
            await self._complete(
                system=system,
                user="Write the compact Playbook now.",
                max_tokens=SUMMARY_MAX_TOKENS,
                json_mode=False,
            )
        ).strip()
        text = strip_source_ids(text)
        if looks_like_secret(text):
            raise PlaybookExtractionError("Core summary contained secret-like content.")
        if not text:
            raise PlaybookExtractionError("Core summary was empty.")
        if len(text) <= max_chars:
            return text, warnings
        compressed = (
            await self._complete(
                system=(
                    f"Compress the Playbook below to at most {max_chars} characters. "
                    "Keep the highest-value current operating information. "
                    "Do not include source IDs, credentials, or confidence numbers.\n\n"
                    f"{text[: max_chars * 2]}"
                ),
                user="Compress now.",
                max_tokens=COMPRESS_MAX_TOKENS,
                json_mode=False,
            )
        ).strip()
        compressed = strip_source_ids(compressed)
        if compressed and len(compressed) <= max_chars and not looks_like_secret(compressed):
            warnings.append(
                ExtractionWarning(
                    code="core_summary_compressed",
                    message="Core summary exceeded the limit and was compressed.",
                )
            )
            return compressed, warnings
        truncated = truncate_text(compressed or text, max_chars)
        warnings.append(
            ExtractionWarning(
                code="core_summary_truncated",
                message="Core summary exceeded the limit after compression and was truncated.",
            )
        )
        return truncated, warnings

    async def _consolidate_chunk(
        self,
        chunk: Sequence[ExtractedCandidate],
        by_id: dict[str, ExtractedCandidate],
        *,
        original_by_id: dict[str, ExtractedCandidate] | None = None,
    ) -> tuple[list[CanonicalObservation] | None, list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        originals = original_by_id or by_id
        payload = [
            {
                "candidate_id": item.candidate_id,
                "category": item.category,
                "subject": item.subject,
                "observation": item.observation,
                "status": item.status,
                "confidence": item.confidence,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "evidence_summary": [
                    {
                        "source_kind": ev.source_kind,
                        "epistemic_role": ev.epistemic_role,
                        "chat_id": ev.chat_id,
                        "turn_id": ev.turn_id,
                    }
                    for ev in item.evidence
                ],
            }
            for item in chunk
        ]
        system = get_prompt_engine().render(
            "playbooks/consolidate_observations.j2",
            categories=sorted(PLAYBOOK_CATEGORIES),
            statuses=sorted(PLAYBOOK_STATUSES),
            candidates_json=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        parsed, extra = await self._request_json(
            system,
            "Consolidate the candidates into canonical observations.",
            max_tokens=CONSOLIDATE_MAX_TOKENS,
            expect_object=True,
        )
        warnings.extend(extra)
        if parsed is None:
            warnings.append(
                ExtractionWarning(
                    code="consolidation_json_invalid_after_repair",
                    message="Consolidation JSON remained invalid after one repair attempt.",
                )
            )
            return None, warnings
        raw_obs = parsed.get("observations")
        if not isinstance(raw_obs, list):
            warnings.append(
                ExtractionWarning(
                    code="consolidation_observations_not_a_list",
                    message="Consolidation JSON did not contain an observations array.",
                )
            )
            return None, warnings
        results: list[CanonicalObservation] = []
        for item in raw_obs:
            if not isinstance(item, dict):
                warnings.append(
                    ExtractionWarning(
                        code="invalid_canonical_observation",
                        message="Ignored a non-object consolidation result.",
                    )
                )
                continue
            ids_raw = item.get("candidate_ids") or []
            if not isinstance(ids_raw, list) or not ids_raw:
                warnings.append(
                    ExtractionWarning(
                        code="unsupported_candidate_reference",
                        message="Ignored a consolidation observation without candidate IDs.",
                    )
                )
                continue
            ids = tuple(
                cid
                for cid in _expand_candidate_ids(ids_raw, by_id)
                if cid in originals
            )
            if not ids:
                warnings.append(
                    ExtractionWarning(
                        code="unsupported_candidate_reference",
                        message="Ignored a consolidation observation with unknown candidate IDs.",
                    )
                )
                continue
            supporting = [originals[cid] for cid in ids]
            evidence = dedupe_evidence(ev for cand in supporting for ev in cand.evidence)
            try:
                confidence = validate_confidence(item.get("confidence", supporting[0].confidence))
            except ValueError:
                confidence = max(c.confidence for c in supporting)
            category = normalize_token(item.get("category") or supporting[0].category)
            status = normalize_token(item.get("status") or supporting[0].status)
            observation = str(item.get("observation") or supporting[0].observation).strip()
            subject = str(item.get("subject") or supporting[0].subject or "")[:SUBJECT_MAX_CHARS] or None
            results.append(
                CanonicalObservation(
                    category=category,
                    subject=subject,
                    observation=observation[:OBSERVATION_MAX_CHARS],
                    status=status,
                    confidence=confidence,
                    candidate_ids=ids,
                    evidence=tuple(evidence),
                )
            )
        used: set[str] = set()
        for item in results:
            used.update(item.candidate_ids)
        for cand in chunk:
            owned = cand.source_candidate_ids or (cand.candidate_id,)
            if used.isdisjoint(owned):
                warnings.append(
                    ExtractionWarning(
                        code="consolidation_candidate_passthrough",
                        message="A candidate was omitted by consolidation and was kept as-is.",
                    )
                )
                results.append(
                    CanonicalObservation(
                        category=cand.category,
                        subject=cand.subject,
                        observation=cand.observation,
                        status=cand.status,
                        confidence=cand.confidence,
                        candidate_ids=owned,
                        evidence=cand.evidence,
                    )
                )
                used.update(owned)
        return results, warnings

    def _validate_candidate(
        self, item: Any, catalogue: SourceCatalogue
    ) -> tuple[ExtractedCandidate | None, list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        if not isinstance(item, dict):
            warnings.append(
                ExtractionWarning(code="invalid_candidate", message="Skipped a non-object observation.")
            )
            return None, warnings
        category = normalize_token(item.get("category"))
        status = normalize_token(item.get("status"))
        observation = str(item.get("observation") or "").strip()
        subject = str(item.get("subject") or "").strip()[:SUBJECT_MAX_CHARS] or None
        if category not in PLAYBOOK_CATEGORIES:
            warnings.append(
                ExtractionWarning(
                    code="unsupported_category",
                    message=f"Rejected unsupported category {item.get('category')!r}.",
                )
            )
            return None, warnings
        if status not in PLAYBOOK_STATUSES:
            warnings.append(
                ExtractionWarning(
                    code="unsupported_status",
                    message=f"Rejected unsupported status {item.get('status')!r}.",
                )
            )
            return None, warnings
        if not observation:
            warnings.append(
                ExtractionWarning(code="empty_observation", message="Rejected an empty observation.")
            )
            return None, warnings
        if looks_like_secret(observation) or (subject and looks_like_secret(subject)):
            warnings.append(
                ExtractionWarning(
                    code="secret_content",
                    message="Rejected an observation that looked like a credential.",
                )
            )
            return None, warnings
        try:
            confidence = validate_confidence(item.get("confidence"))
        except ValueError:
            warnings.append(
                ExtractionWarning(
                    code="invalid_confidence",
                    message="Rejected an observation with confidence outside 0–1.",
                )
            )
            return None, warnings
        raw_evidence = item.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            warnings.append(
                ExtractionWarning(
                    code="missing_evidence",
                    message="Rejected an observation with no evidence.",
                )
            )
            return None, warnings
        evidence: list[ValidatedEvidence] = []
        for raw in raw_evidence:
            validated, extra = self._validate_evidence(raw, catalogue)
            warnings.extend(extra)
            if validated is not None:
                evidence.append(validated)
        evidence = dedupe_evidence(evidence)
        if not evidence:
            warnings.append(
                ExtractionWarning(
                    code="invalid_evidence",
                    message="Dropped a candidate because every evidence reference was invalid.",
                )
            )
            return None, warnings
        roles = {ev.epistemic_role for ev in evidence}
        if status == "confirmed" and roles.isdisjoint(CONFIRMED_STATUS_ROLES):
            status = "uncertain"
            warnings.append(
                ExtractionWarning(
                    code="status_downgraded",
                    message="Downgraded confirmed status because evidence was only AI-suggested.",
                )
            )
        if status == "completed" and roles.isdisjoint(COMPLETED_STATUS_ROLES):
            status = "planned" if "planned" in roles else "uncertain"
            warnings.append(
                ExtractionWarning(
                    code="status_downgraded",
                    message="Downgraded completed status because evidence did not show implementation.",
                )
            )
        guarded, extra = apply_epistemic_guard(
            category=category,
            status=status,
            confidence=confidence,
            subject=subject,
            observation=observation,
            evidence=evidence,
        )
        warnings.extend(extra)
        if guarded is None:
            return None, warnings
        category, status, confidence = guarded
        created_at = min(
            (ev.source_created_at for ev in evidence if ev.source_created_at is not None),
            default=None,
        )
        candidate = ExtractedCandidate(
            candidate_id="",
            category=category,
            subject=subject,
            observation=observation[:OBSERVATION_MAX_CHARS],
            status=status,
            confidence=confidence,
            evidence=tuple(evidence),
            created_at=created_at,
        )
        candidate_id = assign_candidate_id(candidate)
        return (
            replace(
                candidate,
                candidate_id=candidate_id,
                source_candidate_ids=(candidate_id,),
            ),
            warnings,
        )

    def _validate_evidence(
        self, raw: Any, catalogue: SourceCatalogue
    ) -> tuple[ValidatedEvidence | None, list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        if not isinstance(raw, dict):
            warnings.append(
                ExtractionWarning(code="invalid_evidence", message="Skipped a non-object evidence item.")
            )
            return None, warnings
        kind = normalize_token(raw.get("source_kind"))
        role = normalize_token(raw.get("epistemic_role"))
        if kind not in PLAYBOOK_SOURCE_KINDS:
            warnings.append(
                ExtractionWarning(
                    code="invalid_source_kind",
                    message=f"Rejected unsupported source_kind {raw.get('source_kind')!r}.",
                )
            )
            return None, warnings
        if role not in PLAYBOOK_EPISTEMIC_ROLES:
            warnings.append(
                ExtractionWarning(
                    code="invalid_epistemic_role",
                    message=f"Rejected unsupported epistemic_role {raw.get('epistemic_role')!r}.",
                )
            )
            return None, warnings
        quote = str(raw.get("quote") or "").strip()
        if looks_like_secret(quote):
            warnings.append(
                ExtractionWarning(
                    code="secret_content",
                    message="Rejected evidence whose quote looked like a credential.",
                )
            )
            return None, warnings
        quote = quote[:QUOTE_MAX_CHARS]
        chat_id = str(raw.get("chat_id") or "").strip() or None
        turn_id = str(raw.get("turn_id") or "").strip() or None
        brain_source_id = str(raw.get("source_id") or raw.get("brain_source_id") or "").strip() or None
        if kind == "brain":
            if brain_source_id and brain_source_id not in catalogue.brain_ids:
                warnings.append(
                    ExtractionWarning(
                        code="hallucinated_brain_id",
                        message="Rejected Brain evidence that was not in the source catalogue.",
                    )
                )
                return None, warnings
            if not catalogue.brain_ids:
                warnings.append(
                    ExtractionWarning(
                        code="hallucinated_brain_id",
                        message="Rejected Brain evidence because no Brain sources were supplied.",
                    )
                )
                return None, warnings
            stamp = catalogue.brain_timestamps.get(brain_source_id or next(iter(catalogue.brain_ids)))
            return (
                ValidatedEvidence(
                    source_kind=kind,
                    epistemic_role=role,
                    quote=quote,
                    chat_id=None,
                    turn_id=None,
                    source_created_at=stamp,
                    brain_source_id=brain_source_id,
                ),
                warnings,
            )
        if not chat_id or chat_id not in catalogue.chats:
            warnings.append(
                ExtractionWarning(
                    code="hallucinated_chat_id",
                    message="Rejected evidence with a chat ID that was not in this batch.",
                )
            )
            return None, warnings
        if not turn_id or (chat_id, turn_id) not in catalogue.turns:
            warnings.append(
                ExtractionWarning(
                    code="hallucinated_turn_id",
                    message="Rejected evidence with a turn ID that was not in this batch.",
                )
            )
            return None, warnings
        turn = catalogue.turns[(chat_id, turn_id)]
        if kind == "attachment":
            attachment_id = str(raw.get("attachment_id") or "").strip()
            if attachment_id:
                known = {item.attachment_id for item in turn.attachments}
                if attachment_id not in known:
                    warnings.append(
                        ExtractionWarning(
                            code="hallucinated_attachment_id",
                            message="Rejected evidence with an attachment ID that was not in this batch.",
                        )
                    )
                    return None, warnings
        return (
            ValidatedEvidence(
                source_kind=kind,
                epistemic_role=role,
                quote=quote,
                chat_id=chat_id,
                turn_id=turn_id,
                source_created_at=turn.created_at,
            ),
            warnings,
        )

    async def _request_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        expect_object: bool,
    ) -> tuple[dict[str, Any] | None, list[ExtractionWarning]]:
        warnings: list[ExtractionWarning] = []
        raw = await self._complete(
            system=system, user=user, max_tokens=max_tokens, json_mode=True
        )
        parsed = parse_model_json(raw)
        if parsed is not None:
            return parsed, warnings
        warnings.append(
            ExtractionWarning(
                code="json_parse_failed",
                message="Model JSON was invalid; retrying once with a repair instruction.",
            )
        )
        repair_user = (
            "Your previous output was invalid JSON. "
            "Return a single corrected JSON object only. Do not include source transcripts.\n\n"
            f"Previous output:\n{raw[:REPAIR_PREVIOUS_MAX_CHARS]}"
        )
        repaired = await self._complete(
            system=system, user=repair_user, max_tokens=max_tokens, json_mode=True
        )
        parsed = parse_model_json(repaired)
        if parsed is None:
            warnings.append(
                ExtractionWarning(
                    code="json_repair_failed",
                    message="Model JSON was still invalid after one repair attempt.",
                )
            )
            return None, warnings
        if expect_object and not isinstance(parsed, dict):
            return None, warnings
        return parsed, warnings

    async def _complete(
        self, *, system: str, user: str, max_tokens: int, json_mode: bool = False
    ) -> str:
        settings = get_settings()
        model = get_model(settings.playbook_extraction_model_id)
        provider = get_provider_registry().get_provider(model.provider)
        response = await provider.complete(
            system=system,
            user=user,
            model=model.provider_model,
            max_tokens=max_tokens,
            temperature=0.2,
            response_format={"type": "json_object"} if json_mode else None,
        )
        return response.text or ""

    def _render_chat(self, chat: PlaybookChatTranscript) -> str:
        blocks = [
            f"## CHAT id={chat.chat_id} title={chat.chat_title} project_id={chat.project_id or ''}"
        ]
        for turn in chat.turns:
            blocks.append(self._render_turn(chat.chat_id, turn))
        return "\n\n".join(blocks)

    def _render_turn(self, chat_id: str, turn: PlaybookTurnSource) -> str:
        lines = [
            f"### TURN id={turn.turn_id} chat_id={chat_id} status={turn.status} created_at={_iso(turn.created_at)}",
            "#### USER MESSAGE",
            turn.user_message or "",
        ]
        if turn.custom_instructions is not None:
            lines.extend(["#### CUSTOM INSTRUCTIONS", turn.custom_instructions])
        if turn.has_referenced_chat_handoff and turn.referenced_chat_handoff:
            lines.extend(
                [
                    "#### REFERENCED CHAT HANDOFF",
                    "(Derived extraction from custom instructions; cite as referenced_chat_handoff, not a second user message.)",
                    turn.referenced_chat_handoff,
                ]
            )
        for answer in turn.council_answers:
            lines.extend(
                [
                    f"#### COUNCIL ANSWER id={answer.model_answer_id} model_id={answer.model_id} status={answer.status}",
                    answer.text,
                ]
            )
        lines.extend(
            [
                f"#### VERDICT id={turn.verdict.verdict_id}",
                turn.verdict.text,
                "#### VERDICT REASON",
                turn.verdict.reason,
            ]
        )
        if turn.lesson is not None:
            lines.extend(
                [
                    f"#### DISAGREEMENT LESSON id={turn.lesson.lesson_id} status={turn.lesson.status}",
                    f"Disagreement: {turn.lesson.disagreement_reason}",
                    "#### USER POSITION",
                    turn.lesson.user_position,
                ]
            )
            for message in turn.lesson.discussion_messages:
                lines.append(f"- {message.role}: {message.content}")
        else:
            lines.extend(
                [
                    "#### DISAGREEMENT LESSON",
                    "(none)",
                    "#### USER POSITION",
                    "(none)",
                ]
            )
        if turn.attachments:
            for attachment in turn.attachments:
                lines.append(
                    f"#### ATTACHMENT EXCERPT id={attachment.attachment_id} filename={attachment.filename} "
                    f"content_type={attachment.content_type} excerpt_status={attachment.excerpt_status}"
                )
                if attachment.excerpt_is_ready:
                    lines.append(attachment.text_excerpt or "")
                else:
                    lines.append("[excerpt not ready]")
        else:
            lines.extend(["#### ATTACHMENT EXCERPT", "(none)"])
        return "\n".join(lines)

    def _render_brain(self, brain: PlaybookBrainSnapshot) -> str:
        lines = ["## BRAIN PROFILE"]
        if brain.user_brain is None:
            lines.append("(no UserBrain row)")
        else:
            profile = brain.user_brain
            lines.extend(
                [
                    f"id={profile.id} user_global={profile.is_user_global}",
                    f"summary: {profile.summary}",
                    f"thinking_style: {profile.thinking_style}",
                    f"likes: {', '.join(profile.likes)}",
                    f"dislikes: {', '.join(profile.dislikes)}",
                    f"lesson_count: {profile.lesson_count}",
                ]
            )
            for memory in profile.memories:
                lines.append(f"- memory: {canonical_dumps(memory)}")
        lines.append("## BRAIN KNOWLEDGE")
        if not brain.knowledge_items:
            lines.append("(none)")
        for item in brain.knowledge_items:
            lines.extend(
                [
                    f"### BRAIN KNOWLEDGE id={item.id} source_type={item.source_type} source_id={item.source_id}",
                    f"title: {item.title}",
                    item.content,
                ]
            )
        return "\n".join(lines)


class PlaybookExtractionError(RuntimeError):
    pass


def parse_model_json(text: str) -> dict[str, Any] | None:
    parsed = LLMProvider.parse_json_object_lenient(text)
    if isinstance(parsed, dict):
        return parsed
    try:
        value = LLMProvider.parse_json_response(text or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def assign_candidate_id(candidate: ExtractedCandidate) -> str:
    payload = {
        "category": candidate.category,
        "subject": candidate.subject,
        "observation": candidate.observation,
        "status": candidate.status,
        "evidence": [
            {
                "chat_id": ev.chat_id,
                "turn_id": ev.turn_id,
                "source_kind": ev.source_kind,
                "epistemic_role": ev.epistemic_role,
                "quote": ev.quote,
            }
            for ev in candidate.evidence
        ],
    }
    digest = hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()[:16]
    return f"c-{digest}"


def apply_epistemic_guard(
    *,
    category: str,
    status: str,
    confidence: float,
    subject: str | None,
    observation: str,
    evidence: Sequence[ValidatedEvidence],
) -> tuple[tuple[str, str, float] | None, list[ExtractionWarning]]:
    """Enforce durable-knowledge authority rules using validated evidence only."""
    warnings: list[ExtractionWarning] = []
    text = " ".join(part for part in (subject, observation) if part).strip()
    direct = [item for item in evidence if item.source_kind in USER_EVIDENCE_KINDS]
    ai_only = bool(evidence) and all(item.source_kind in AI_EVIDENCE_KINDS for item in evidence)
    document = [item for item in evidence if item.source_kind in DOCUMENT_EVIDENCE_KINDS]
    derived_only = bool(evidence) and not direct and not document and not ai_only

    durable_direct = [item for item in direct if not EPHEMERAL_RE.search(item.quote or "")]
    if EPHEMERAL_RE.search(text) and not durable_direct:
        return None, [
            ExtractionWarning(
                code="playbook_ephemeral_observation_rejected",
                message="Rejected a session-scoped observation from the durable Playbook.",
            )
        ]
    if LOW_INFORMATION_RE.search(text):
        return None, [
            ExtractionWarning(
                code="playbook_low_information_observation_rejected",
                message="Rejected an observation without a concrete durable object.",
            )
        ]

    roles = {item.epistemic_role for item in direct}
    has_adoption = not roles.isdisjoint(USER_ADOPTION_ROLES) or any(
        USER_ADOPTION_RE.search(item.quote or "") for item in direct
    )
    has_intent = not roles.isdisjoint(USER_INTENT_ROLES)
    has_intent = has_intent or has_adoption
    has_completion = not roles.isdisjoint(USER_COMPLETION_ROLES) or any(
        USER_COMPLETION_RE.search(item.quote or "") for item in direct
    )
    has_rejection = not roles.isdisjoint(USER_REJECTION_ROLES) or any(
        USER_REJECTION_RE.search(item.quote or "") for item in direct
    )

    if category == "completed_work" or status == "completed":
        if not has_completion:
            return None, [
                ExtractionWarning(
                    code="playbook_completed_without_completion_evidence",
                    message="Rejected completed work without completion evidence.",
                )
            ]

    elif category == "preference":
        explicit = any(
            EXPLICIT_PREFERENCE_RE.search(item.quote or "")
            or item.epistemic_role == "user_confirmed"
            for item in direct
        )
        interactions = {
            (item.chat_id, item.turn_id)
            for item in direct
            if item.chat_id is not None and item.turn_id is not None
        }
        if explicit:
            pass
        elif len(interactions) >= 2:
            status = "uncertain"
            confidence = min(confidence, UNCERTAIN_CONFIDENCE_CAP)
            warnings.append(
                ExtractionWarning(
                    code="playbook_inferred_preference_downgraded",
                    message="Kept a repeated inferred preference with uncertain status.",
                )
            )
        elif derived_only:
            status = "uncertain"
            confidence = min(confidence, UNCERTAIN_CONFIDENCE_CAP)
            warnings.append(
                ExtractionWarning(
                    code="playbook_missing_user_grounding",
                    message="Downgraded derived preference context without direct user evidence.",
                )
            )
        else:
            return None, [
                ExtractionWarning(
                    code="playbook_ai_only_user_claim_rejected" if ai_only else "playbook_missing_user_grounding",
                    message="Rejected a preference without sufficient user grounding.",
                )
            ]

    elif category in USER_GROUNDED_CATEGORIES:
        supported = has_rejection if category == "rejected_option" else has_intent
        if not supported:
            if category == "decision" and document and SOURCE_ATTRIBUTION_RE.search(text):
                category = "important_fact"
                status = "uncertain"
                confidence = min(confidence, UNCERTAIN_CONFIDENCE_CAP)
                warnings.append(
                    ExtractionWarning(
                        code="playbook_document_recommendation_reclassified",
                        message="Reclassified a source recommendation that lacked user adoption.",
                    )
                )
            else:
                return None, [
                    ExtractionWarning(
                        code="playbook_ai_only_user_claim_rejected" if ai_only else "playbook_missing_user_grounding",
                        message="Rejected a user-action claim without user adoption evidence.",
                    )
                ]

    elif category == "project" and status == "active":
        if roles.isdisjoint(USER_PROJECT_ROLES):
            return None, [
                ExtractionWarning(
                    code="playbook_missing_user_grounding",
                    message="Rejected an active project without user ownership evidence.",
                )
            ]

    elif category == "architecture" and not direct:
        if not document or not SOURCE_ATTRIBUTION_RE.search(text):
            return None, [
                ExtractionWarning(
                    code="playbook_ai_only_user_claim_rejected" if ai_only else "playbook_missing_user_grounding",
                    message="Rejected architecture presented without accurate source provenance.",
                )
            ]
        status = "uncertain"
        confidence = min(confidence, UNCERTAIN_CONFIDENCE_CAP)

    elif category == "important_fact" and ai_only and any(
        item.epistemic_role == "ai_suggested" for item in evidence
    ):
        return None, [
            ExtractionWarning(
                code="playbook_ai_only_user_claim_rejected",
                message="Rejected AI-only advice as durable user knowledge.",
            )
        ]

    if status == "confirmed" and not direct and (document or derived_only):
        status = "uncertain"
    if status == "uncertain" and confidence > UNCERTAIN_CONFIDENCE_CAP:
        confidence = UNCERTAIN_CONFIDENCE_CAP
        warnings.append(
            ExtractionWarning(
                code="playbook_uncertain_confidence_capped",
                message="Capped confidence for an uncertain observation.",
            )
        )
    return (category, status, confidence), warnings


def _is_core_summary_worthy(item: CanonicalObservation) -> bool:
    text = " ".join(part for part in (item.subject, item.observation) if part)
    if EPHEMERAL_RE.search(text) or LOW_INFORMATION_RE.search(text):
        return False
    if item.status in {"uncertain", "superseded", "rejected"}:
        return False
    return item.category in {
        "preference",
        "project",
        "decision",
        "completed_work",
        "blocker",
        "priority",
        "next_step",
        "plan",
    }


def _semantic_duplicate_text(value: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    return re.sub(r"\b(?:web app|application|app|project)\b$", "", normalized).strip()


def merge_duplicate_candidates(
    candidates: Sequence[ExtractedCandidate],
) -> list[ExtractedCandidate]:
    grouped: dict[tuple[str, str, str, str], ExtractedCandidate] = {}
    order: list[tuple[str, str, str, str]] = []
    for item in candidates:
        subject = (item.subject or "").strip().lower()
        observation = item.observation.strip().lower()
        if item.category == "project":
            subject = _semantic_duplicate_text(item.subject)
            observation = _semantic_duplicate_text(item.observation)
        key = (
            item.category,
            subject,
            observation,
            item.status,
        )
        if key not in grouped:
            grouped[key] = item
            order.append(key)
            continue
        existing = grouped[key]
        grouped[key] = replace(
            existing,
            evidence=tuple(dedupe_evidence([*existing.evidence, *item.evidence])),
            confidence=max(existing.confidence, item.confidence),
            source_candidate_ids=tuple(
                dict.fromkeys(
                    [
                        *(existing.source_candidate_ids or (existing.candidate_id,)),
                        *(item.source_candidate_ids or (item.candidate_id,)),
                    ]
                )
            ),
        )
    return [grouped[key] for key in order]


def chunk_candidates(
    candidates: Sequence[ExtractedCandidate], max_size: int
) -> list[list[ExtractedCandidate]]:
    if max_size <= 0:
        raise ValueError("max_size must be positive")
    groups: dict[tuple[str, str], list[ExtractedCandidate]] = {}
    group_order: list[tuple[str, str]] = []
    for item in candidates:
        key = (item.category, (item.subject or "").strip().lower())
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(item)
    chunks: list[list[ExtractedCandidate]] = []
    current: list[ExtractedCandidate] = []
    for key in group_order:
        group = groups[key]
        for start in range(0, len(group), max_size):
            piece = group[start : start + max_size]
            if current and len(current) + len(piece) > max_size:
                chunks.append(current)
                current = []
            if len(piece) == max_size and not current:
                chunks.append(piece)
            else:
                current.extend(piece)
                if len(current) >= max_size:
                    chunks.append(current)
                    current = []
    if current:
        chunks.append(current)
    return chunks


def dedupe_evidence(items: Iterable[ValidatedEvidence]) -> list[ValidatedEvidence]:
    seen: set[tuple] = set()
    out: list[ValidatedEvidence] = []
    for item in items:
        key = (
            item.chat_id,
            item.turn_id,
            item.source_kind,
            item.epistemic_role,
            item.quote,
            item.brain_source_id,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_source_catalogue(
    batch: PlaybookExtractionBatch | None,
    brain: PlaybookBrainSnapshot | None,
) -> SourceCatalogue:
    turns: dict[tuple[str, str], PlaybookTurnSource] = {}
    chats: dict[str, PlaybookChatTranscript] = {}
    if batch is not None:
        for chat in batch.chats:
            chats[chat.chat_id] = chat
            for turn in chat.turns:
                turns[(chat.chat_id, turn.turn_id)] = turn
    brain_ids: set[str] = set()
    brain_timestamps: dict[str, datetime | None] = {}
    if brain is not None and brain.user_brain is not None:
        brain_ids.add(brain.user_brain.id)
        brain_timestamps[brain.user_brain.id] = brain.user_brain.updated_at or brain.user_brain.created_at
    if brain is not None:
        for item in brain.knowledge_items:
            brain_ids.add(item.id)
            brain_timestamps[item.id] = item.updated_at or item.created_at
    return SourceCatalogue(
        turns=turns, chats=chats, brain_ids=brain_ids, brain_timestamps=brain_timestamps
    )


def normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def validate_confidence(value: Any) -> float:
    if value is None:
        raise ValueError("missing confidence")
    number = float(value)
    if 1.0 < number <= 1.05:
        number = 1.0
    if -0.05 <= number < 0.0:
        number = 0.0
    if number < 0.0 or number > 1.0:
        raise ValueError("confidence out of range")
    return number


def looks_like_secret(text: str | None) -> bool:
    return bool(text and SECRET_RE.search(text))


def strip_source_ids(text: str) -> str:
    cleaned = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def truncate_text(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    snippet = cleaned[:max_chars].rstrip()
    for sep in ("\n", ". ", " "):
        idx = snippet.rfind(sep)
        if idx >= max_chars // 2:
            snippet = snippet[: idx + (0 if sep == "\n" else 1)].rstrip()
            break
    return snippet


def _expand_candidate_ids(
    ids_raw: Sequence[Any], by_id: dict[str, ExtractedCandidate]
) -> tuple[str, ...]:
    expanded: list[str] = []
    for raw in ids_raw:
        cid = str(raw)
        cand = by_id.get(cid)
        if cand is None:
            continue
        owned = cand.source_candidate_ids or (cand.candidate_id,)
        for owned_id in owned:
            if owned_id in by_id:
                expanded.append(owned_id)
            else:
                expanded.append(cid)
    return tuple(dict.fromkeys(expanded))


def _canonical_as_candidate(
    item: CanonicalObservation, by_id: dict[str, ExtractedCandidate]
) -> ExtractedCandidate:
    supporting = [by_id[cid] for cid in item.candidate_ids if cid in by_id]
    evidence = item.evidence or tuple(ev for cand in supporting for ev in cand.evidence)
    source_ids = item.candidate_ids or tuple(c.candidate_id for c in supporting)
    first = source_ids[0] if source_ids else assign_candidate_id(
        ExtractedCandidate(
            candidate_id="tmp",
            category=item.category,
            subject=item.subject,
            observation=item.observation,
            status=item.status,
            confidence=item.confidence,
            evidence=tuple(evidence),
        )
    )
    return ExtractedCandidate(
        candidate_id=first,
        category=item.category,
        subject=item.subject,
        observation=item.observation,
        status=item.status,
        confidence=item.confidence,
        evidence=tuple(dedupe_evidence(evidence)),
        created_at=item.first_observed_at,
        source_candidate_ids=source_ids or (first,),
    )


def _candidate_as_canonical(item: ExtractedCandidate) -> CanonicalObservation:
    owned = item.source_candidate_ids or (item.candidate_id,)
    return CanonicalObservation(
        category=item.category,
        subject=item.subject,
        observation=item.observation,
        status=item.status,
        confidence=item.confidence,
        candidate_ids=owned,
        evidence=item.evidence,
    )


def _candidate_lineage_map(
    candidates: Sequence[ExtractedCandidate],
) -> dict[str, ExtractedCandidate]:
    result: dict[str, ExtractedCandidate] = {}
    for item in candidates:
        result[item.candidate_id] = item
        for source_id in item.source_candidate_ids or (item.candidate_id,):
            result.setdefault(source_id, item)
    return result


def _consolidation_failure_reason(warnings: Sequence[ExtractionWarning]) -> str:
    codes = {item.code for item in warnings}
    if "consolidation_json_invalid_after_repair" in codes:
        return "json_invalid_after_repair"
    if "consolidation_observations_not_a_list" in codes:
        return "observations_not_a_list"
    return "chunk_passthrough"


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


playbook_extraction_service = PlaybookExtractionService()
