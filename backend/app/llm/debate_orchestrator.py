"""AI Debate Mode execution engine — isolated from Council/Verdict orchestration.

Runs Round 2 (peer-review challenges) and Round 3 (rebuttals), then reduces
the structured results into a ``DebateFindings`` with no additional LLM call.
This module has no database access and no knowledge of ``DebateSession``
rows — it is a pure async engine over already-loaded participant data, kept
deliberately separate from ``app.llm.orchestrator`` (Council/Verdict) so it
can be tested with mocked providers and can never read or write a Turn,
ModelAnswer, or Verdict row. Persistence and eligibility live in
``app.services.debate_service``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger
from app.llm.catalog import get_model
from app.llm.debate_contracts import (
    ChallengeSet,
    DebateContractError,
    DebateFindings,
    ResponseSet,
    parse_challenge_output,
    parse_response_output,
)
from app.llm.debate_prompts import (
    ChallengeForResponse,
    PanelistAnswer,
    build_challenge_prompt,
    build_response_prompt,
)
from app.llm.providers import ProviderRegistry

logger = get_logger(__name__)

# Debate calls return short, structured JSON — a fraction of Council/Verdict's
# free-text budgets (COUNCIL_MAX_TOKENS=20000, VERDICT_MAX_TOKENS=15000 in
# orchestrator.py). These are independent constants; changing Council/Verdict
# budgets never affects Debate Mode and vice versa.
DEBATE_CHALLENGE_MAX_TOKENS = 3_000
DEBATE_RESPONSE_MAX_TOKENS = 3_000
DEBATE_DEFAULT_MAX_CONCURRENCY = 5
DEBATE_DEFAULT_PER_CALL_TIMEOUT_SECONDS = 45.0
DEBATE_DEFAULT_OVERALL_DEADLINE_SECONDS = 180.0

# Round 2 is always exactly one call per participant reviewing every other
# participant at once (O(n)); this module never issues a pairwise call.

DebateEventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class DebateParticipant:
    """One completed Council ModelAnswer, as loaded read-only by the service layer."""

    model_id: str
    model_name: str
    answer_text: str


@dataclass
class DebateRunConfig:
    user_message: str
    participants: list[DebateParticipant]
    max_concurrency: int = DEBATE_DEFAULT_MAX_CONCURRENCY
    per_call_timeout_seconds: float = DEBATE_DEFAULT_PER_CALL_TIMEOUT_SECONDS
    overall_deadline_seconds: float = DEBATE_DEFAULT_OVERALL_DEADLINE_SECONDS

    def __post_init__(self) -> None:
        if len(self.participants) < 2:
            raise ValueError("DebateRunConfig requires at least two participants")
        ids = [p.model_id for p in self.participants]
        if len(ids) != len(set(ids)):
            raise ValueError("DebateRunConfig participants must have unique model_ids")


@dataclass
class LLMCallOutcome:
    """Usage/error bookkeeping for one debate LLM call, independent of whether
    the call's JSON output ultimately validated."""

    model_id: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class ChallengeCallResult:
    model_id: str
    challenge_set: ChallengeSet | None
    outcome: LLMCallOutcome


@dataclass
class ResponseCallResult:
    model_id: str
    response_set: ResponseSet | None
    outcome: LLMCallOutcome


@dataclass
class DebateRunResult:
    challenge_results: list[ChallengeCallResult] = field(default_factory=list)
    response_results: list[ResponseCallResult] = field(default_factory=list)
    findings: DebateFindings = field(default_factory=DebateFindings)
    timed_out: bool = False

    @property
    def any_challenge_succeeded(self) -> bool:
        return any(r.outcome.succeeded for r in self.challenge_results)

    @property
    def all_calls_failed(self) -> bool:
        calls = list(self.challenge_results) + list(self.response_results)
        return bool(calls) and all(not c.outcome.succeeded for c in calls)


class _Deadline:
    """Wall-clock budget shared across both rounds of one debate run."""

    def __init__(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0


async def _gather_bounded(
    coros: list[Awaitable[Any]], *, deadline: _Deadline
) -> list[Any | None]:
    """Run ``coros`` concurrently, returning ``None`` for any still pending
    once the shared deadline elapses instead of blocking indefinitely.

    Each coroutine must already catch its own exceptions (debate call
    wrappers below do), so a ``None`` here means only "the overall debate
    deadline was reached before this call finished."
    """
    if not coros:
        return []
    tasks = [asyncio.ensure_future(c) for c in coros]
    timeout = deadline.remaining()
    done, pending = await asyncio.wait(tasks, timeout=timeout if timeout > 0 else 0)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return [None if task in pending else task.result() for task in tasks]


class DebateOrchestrator:
    """Stateless engine — construct once, call ``run()`` per debate session."""

    def __init__(self, providers: ProviderRegistry) -> None:
        self._providers = providers

    async def _call_model_json(
        self,
        *,
        model_id: str,
        system_prompt: str,
        max_tokens: int,
        semaphore: asyncio.Semaphore,
        per_call_timeout: float,
    ) -> tuple[str | None, LLMCallOutcome]:
        """Issue one bounded provider call. Never raises — failures become
        an ``LLMCallOutcome.error`` so one model's failure cannot abort the
        round for everyone else. Never substitutes a different model on
        failure (unlike Council's narrow ULTIMATE_COUNCIL_FALLBACKS escape
        hatch) — a debate call either succeeds on the requested model or is
        recorded as failed for that model.
        """
        try:
            model = get_model(model_id)
        except Exception as exc:  # unknown model_id
            return None, LLMCallOutcome(model_id=model_id, error=f"unknown model_id: {exc}")

        async def _call() -> Any:
            provider = self._providers.get_provider(model.provider)
            return await provider.complete(
                system=system_prompt,
                user="Return the JSON now.",
                model=model.provider_model,
                max_tokens=max_tokens,
            )

        async with semaphore:
            try:
                response = await asyncio.wait_for(_call(), timeout=per_call_timeout)
                if response is None or not hasattr(response, "text"):
                    raise TypeError(f"provider returned an unusable response: {response!r}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "debate_llm_call_failed", model_id=model_id, error_type=type(exc).__name__
                )
                return None, LLMCallOutcome(model_id=model_id, error=str(exc))

        cost = response.cost_usd
        outcome = LLMCallOutcome(
            model_id=model_id,
            tokens_input=response.tokens_input,
            tokens_output=response.tokens_output,
            cost_usd=float(cost) if cost is not None else 0.0,
        )
        return response.text, outcome

    async def _run_challenge_call(
        self,
        *,
        author: DebateParticipant,
        others: list[DebateParticipant],
        config: DebateRunConfig,
        semaphore: asyncio.Semaphore,
    ) -> ChallengeCallResult:
        prompt = build_challenge_prompt(
            user_message=config.user_message,
            author=PanelistAnswer(
                model_id=author.model_id, model_name=author.model_name, text=author.answer_text
            ),
            other_answers=[
                PanelistAnswer(model_id=o.model_id, model_name=o.model_name, text=o.answer_text)
                for o in others
            ],
        )
        raw_text, outcome = await self._call_model_json(
            model_id=author.model_id,
            system_prompt=prompt,
            max_tokens=DEBATE_CHALLENGE_MAX_TOKENS,
            semaphore=semaphore,
            per_call_timeout=config.per_call_timeout_seconds,
        )
        if raw_text is None:
            return ChallengeCallResult(model_id=author.model_id, challenge_set=None, outcome=outcome)
        try:
            challenge_set = parse_challenge_output(
                raw_text,
                expected_author_model_id=author.model_id,
                known_target_model_ids={o.model_id for o in others},
            )
        except DebateContractError as exc:
            logger.warning("debate_challenge_output_invalid", model_id=author.model_id, error=str(exc))
            outcome.error = str(exc)
            return ChallengeCallResult(model_id=author.model_id, challenge_set=None, outcome=outcome)
        return ChallengeCallResult(model_id=author.model_id, challenge_set=challenge_set, outcome=outcome)

    async def _run_response_call(
        self,
        *,
        target: DebateParticipant,
        challenges: list[ChallengeForResponse],
        config: DebateRunConfig,
        semaphore: asyncio.Semaphore,
    ) -> ResponseCallResult:
        prompt = build_response_prompt(
            user_message=config.user_message,
            author=PanelistAnswer(
                model_id=target.model_id, model_name=target.model_name, text=target.answer_text
            ),
            challenges=challenges,
        )
        raw_text, outcome = await self._call_model_json(
            model_id=target.model_id,
            system_prompt=prompt,
            max_tokens=DEBATE_RESPONSE_MAX_TOKENS,
            semaphore=semaphore,
            per_call_timeout=config.per_call_timeout_seconds,
        )
        if raw_text is None:
            return ResponseCallResult(model_id=target.model_id, response_set=None, outcome=outcome)
        try:
            response_set = parse_response_output(
                raw_text,
                expected_author_model_id=target.model_id,
                known_challenge_keys={(c.challenger_model_id, c.challenge_id) for c in challenges},
            )
        except DebateContractError as exc:
            logger.warning("debate_response_output_invalid", model_id=target.model_id, error=str(exc))
            outcome.error = str(exc)
            return ResponseCallResult(model_id=target.model_id, response_set=None, outcome=outcome)
        return ResponseCallResult(model_id=target.model_id, response_set=response_set, outcome=outcome)

    async def run(
        self,
        config: DebateRunConfig,
        *,
        on_event: DebateEventCallback | None = None,
    ) -> DebateRunResult:
        async def emit(event: str, data: dict[str, Any]) -> None:
            if on_event:
                await on_event(event, data)

        deadline = _Deadline(config.overall_deadline_seconds)
        semaphore = asyncio.Semaphore(max(1, config.max_concurrency))
        result = DebateRunResult()

        # --- Round 2: every participant reviews every other participant in
        # ONE request each — O(n) calls total, never pairwise O(n^2).
        await emit("debate_round2_started", {"participant_count": len(config.participants)})

        async def challenge_call(author: DebateParticipant) -> ChallengeCallResult:
            others = [p for p in config.participants if p.model_id != author.model_id]
            call_result = await self._run_challenge_call(
                author=author, others=others, config=config, semaphore=semaphore
            )
            event = "debate_challenge_completed" if call_result.outcome.succeeded else "debate_challenge_failed"
            await emit(
                event,
                {
                    "model_id": author.model_id,
                    "challenges": [item.model_dump() for item in call_result.challenge_set.challenges]
                    if call_result.challenge_set
                    else None,
                    "tokens_input": call_result.outcome.tokens_input,
                    "tokens_output": call_result.outcome.tokens_output,
                    "cost_usd": call_result.outcome.cost_usd,
                    "error": call_result.outcome.error,
                },
            )
            return call_result

        challenge_results = await _gather_bounded(
            [challenge_call(p) for p in config.participants], deadline=deadline
        )
        result.challenge_results = [
            r if r is not None
            else ChallengeCallResult(
                model_id=participant.model_id,
                challenge_set=None,
                outcome=LLMCallOutcome(model_id=participant.model_id, error="debate deadline exceeded"),
            )
            for r, participant in zip(challenge_results, config.participants, strict=True)
        ]
        if any(r is None for r in challenge_results):
            result.timed_out = True
        await emit("debate_round2_completed", {})

        if deadline.expired():
            result.timed_out = True
            await emit("debate_round3_skipped", {"reason": "deadline exceeded before round 3"})
            result.findings = reduce_findings(result)
            await emit("debate_findings_ready", {})
            return result

        # --- Round 3: group valid challenges by TARGET model. Identity is
        # the composite (challenger_model_id, challenge_id) — challenge_id
        # alone may collide across different reviewing models.
        challenges_by_target: dict[str, list[ChallengeForResponse]] = {}
        for call_result in result.challenge_results:
            if call_result.challenge_set is None:
                continue
            for item in call_result.challenge_set.challenges:
                challenges_by_target.setdefault(item.target_model_id, []).append(
                    ChallengeForResponse(
                        challenge_id=item.challenge_id,
                        challenger_model_id=call_result.model_id,
                        issue=item.issue,
                        reasoning=item.reasoning,
                        severity=item.severity,
                        uncertainty=item.uncertainty,
                    )
                )

        participants_by_id = {p.model_id: p for p in config.participants}
        await emit("debate_round3_started", {"targeted_model_count": len(challenges_by_target)})

        async def response_call(model_id: str, challenges: list[ChallengeForResponse]) -> ResponseCallResult:
            call_result = await self._run_response_call(
                target=participants_by_id[model_id], challenges=challenges, config=config, semaphore=semaphore
            )
            event = "debate_response_completed" if call_result.outcome.succeeded else "debate_response_failed"
            await emit(
                event,
                {
                    "model_id": model_id,
                    "responses": [item.model_dump() for item in call_result.response_set.responses]
                    if call_result.response_set
                    else None,
                    "tokens_input": call_result.outcome.tokens_input,
                    "tokens_output": call_result.outcome.tokens_output,
                    "cost_usd": call_result.outcome.cost_usd,
                    "error": call_result.outcome.error,
                },
            )
            return call_result

        # A model that received zero challenges is never called in Round 3.
        targeted_items = list(challenges_by_target.items())
        response_results = await _gather_bounded(
            [response_call(model_id, challenges) for model_id, challenges in targeted_items],
            deadline=deadline,
        )
        result.response_results = [
            r if r is not None
            else ResponseCallResult(
                model_id=model_id,
                response_set=None,
                outcome=LLMCallOutcome(model_id=model_id, error="debate deadline exceeded"),
            )
            for r, (model_id, _challenges) in zip(response_results, targeted_items, strict=True)
        ]
        if any(r is None for r in response_results):
            result.timed_out = True
        await emit("debate_round3_completed", {})

        result.findings = reduce_findings(result)
        await emit("debate_findings_ready", {})
        return result


def reduce_findings(result: DebateRunResult) -> DebateFindings:
    """Deterministic findings reducer — no LLM call.

    Every entry here is derived directly from a completed ``ChallengeSet``/
    ``ResponseSet``: never synthesized, and never presented as independently
    verified fact. See the field-level docstrings on
    ``app.llm.debate_contracts`` for what each category does and does not
    claim.
    """
    from app.llm.debate_contracts import (
        Agreement,
        Correction,
        Risk,
        Uncertainty,
        UnresolvedDisagreement,
    )

    all_model_ids = {r.model_id for r in result.challenge_results}

    # Responses keyed by the exact (challenger_model_id, challenge_id) they address.
    responses_by_key: dict[tuple[str, str], Any] = {}
    for call_result in result.response_results:
        if call_result.response_set is None:
            continue
        for item in call_result.response_set.responses:
            responses_by_key[(item.challenger_model_id, item.challenge_id)] = item

    challenged_model_ids: set[str] = set()
    unresolved: list[UnresolvedDisagreement] = []
    corrections: list[Correction] = []
    risks: list[Risk] = []
    uncertainties: list[Uncertainty] = []

    for call_result in result.challenge_results:
        if call_result.challenge_set is None:
            continue
        for item in call_result.challenge_set.challenges:
            challenged_model_ids.add(item.target_model_id)
            key = (call_result.model_id, item.challenge_id)
            response_item = responses_by_key.get(key)

            if item.severity == "high":
                risks.append(
                    Risk(
                        description=item.issue,
                        severity=item.severity,
                        raised_by_model_ids=(call_result.model_id,),
                    )
                )
            if item.uncertainty:
                uncertainties.append(
                    Uncertainty(
                        description=item.uncertainty,
                        related_model_ids=(call_result.model_id, item.target_model_id),
                    )
                )

            if response_item is None:
                unresolved.append(
                    UnresolvedDisagreement(
                        description=f"{item.target_model_id} did not produce a usable response "
                        f"to this challenge: {item.issue}",
                        challenge_id=item.challenge_id,
                        challenger_model_id=call_result.model_id,
                        target_model_id=item.target_model_id,
                        severity=item.severity,
                    )
                )
            elif response_item.stance == "defends":
                unresolved.append(
                    UnresolvedDisagreement(
                        description=item.issue,
                        challenge_id=item.challenge_id,
                        challenger_model_id=call_result.model_id,
                        target_model_id=item.target_model_id,
                        severity=item.severity,
                    )
                )
            else:  # "revises" or "concedes"
                corrections.append(
                    Correction(
                        description=item.issue,
                        model_id=item.target_model_id,
                        challenge_id=item.challenge_id,
                        stance=response_item.stance,
                        previous_position=None,
                        updated_position=response_item.updated_position or "",
                    )
                )

    agreements = [
        Agreement(description=f"No participating model raised a challenge against {model_id}'s answer", model_ids=(model_id,))
        for model_id in sorted(all_model_ids - challenged_model_ids)
    ]

    return DebateFindings(
        agreements=tuple(agreements),
        unresolved_disagreements=tuple(unresolved),
        corrections=tuple(corrections),
        risks=tuple(risks),
        uncertainties=tuple(uncertainties),
    )


def build_final_summary(findings: DebateFindings) -> str:
    """A short, template-assembled summary — no LLM call. Counts only; never
    states that agreement implies correctness or that a concession is an
    independently established fact."""
    parts = [
        f"{len(findings.agreements)} point(s) drew no challenge from the panel.",
        f"{len(findings.unresolved_disagreements)} challenge(s) remain unresolved.",
        f"{len(findings.corrections)} model(s) revised or conceded a point.",
        f"{len(findings.risks)} high-severity concern(s) were raised.",
        f"{len(findings.uncertainties)} point(s) were flagged by a reviewer as uncertain.",
    ]
    return " ".join(parts)
