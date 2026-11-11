"""Structured I/O contracts for AI Debate Mode (peer review + rebuttal).

Fully isolated from Council/Verdict code: this module defines pydantic
schemas and validation for the JSON a model returns for the Round 2
(``debate_challenge.j2``) and Round 3 (``debate_response.j2``) prompts in
``app.llm.debate_prompts``, plus a structured Findings contract that a later
Debate orchestrator (Phase 3) will populate deterministically from completed
challenges/responses — no LLM summarization happens in this module.

A challenge or concession produced here is a model's own stated assessment,
never independently verified fact. Nothing in this module writes to the
database or calls a provider; it only parses and validates text already
returned by a model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.llm.providers import LLMProvider

Severity = Literal["low", "medium", "high"]
Stance = Literal["defends", "revises", "concedes"]


class DebateContractError(ValueError):
    """Raised when a model's structured debate output is missing, malformed,
    or fails validation against the expected contract."""


def _require_non_blank(value: str, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be blank")
    return cleaned


class ChallengeItem(BaseModel):
    """One Round-2 critique raised by ``author_model_id`` against another model."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    target_model_id: str
    issue: str
    reasoning: str
    severity: Severity
    uncertainty: str | None = None

    @field_validator("challenge_id", "target_model_id", "issue", "reasoning")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("uncertainty")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ChallengeSet(BaseModel):
    """The full Round-2 output from one author model in one debate session."""

    model_config = ConfigDict(extra="forbid")

    author_model_id: str
    challenges: list[ChallengeItem] = Field(default_factory=list)

    @field_validator("author_model_id")
    @classmethod
    def _author_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "author_model_id")

    @model_validator(mode="after")
    def _unique_challenge_ids(self) -> "ChallengeSet":
        ids = [item.challenge_id for item in self.challenges]
        if len(ids) != len(set(ids)):
            raise ValueError("challenge_id values must be unique within one author's response")
        return self

    @model_validator(mode="after")
    def _no_self_targeting(self) -> "ChallengeSet":
        for item in self.challenges:
            if item.target_model_id == self.author_model_id:
                raise ValueError("a model cannot target its own answer with a challenge")
        return self


class ResponseItem(BaseModel):
    """One Round-3 rebuttal entry addressing a single challenge."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    challenger_model_id: str
    stance: Stance
    explanation: str
    updated_position: str | None = None

    @field_validator("challenge_id", "challenger_model_id", "explanation")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("updated_position")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _revision_requires_updated_position(self) -> "ResponseItem":
        if self.stance in ("revises", "concedes") and not self.updated_position:
            raise ValueError(f"stance '{self.stance}' requires a non-empty updated_position")
        return self


class ResponseSet(BaseModel):
    """The full Round-3 output from one challenged model in one debate session."""

    model_config = ConfigDict(extra="forbid")

    author_model_id: str
    responses: list[ResponseItem] = Field(default_factory=list)

    @field_validator("author_model_id")
    @classmethod
    def _author_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "author_model_id")

    @model_validator(mode="after")
    def _unique_challenge_coverage(self) -> "ResponseSet":
        # challenge_id alone may legitimately collide across different
        # reviewing models (each author only has to keep IDs unique within
        # its OWN Round-2 response — see ChallengeSet._unique_challenge_ids).
        # The stable identity for one challenge is therefore the composite
        # (challenger_model_id, challenge_id), never challenge_id alone.
        keys = [(item.challenger_model_id, item.challenge_id) for item in self.responses]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "(challenger_model_id, challenge_id) pairs must be unique within one response set"
            )
        return self


def parse_challenge_output(
    raw_text: str,
    *,
    expected_author_model_id: str,
    known_target_model_ids: set[str],
) -> ChallengeSet:
    """Parse and validate a model's Round-2 JSON output.

    Tolerant of prose/code-fence wrapping and truncation via the same
    best-effort recovery the orchestrator already uses for Verdict JSON
    (``LLMProvider.parse_json_object_lenient``), but strict about the
    resulting contract: unknown target IDs or a mismatched author are
    rejected rather than silently accepted.
    """
    parsed = LLMProvider.parse_json_object_lenient(raw_text)
    if parsed is None:
        raise DebateContractError("response was not valid or recoverable JSON")
    try:
        challenge_set = ChallengeSet.model_validate(parsed)
    except Exception as exc:  # pydantic.ValidationError or our own ValueError
        raise DebateContractError(str(exc)) from exc
    if challenge_set.author_model_id != expected_author_model_id:
        raise DebateContractError(
            f"author_model_id '{challenge_set.author_model_id}' does not match "
            f"the requesting model '{expected_author_model_id}'"
        )
    unknown = {item.target_model_id for item in challenge_set.challenges} - known_target_model_ids
    if unknown:
        raise DebateContractError(f"unknown target_model_id(s): {sorted(unknown)}")
    return challenge_set


def parse_response_output(
    raw_text: str,
    *,
    expected_author_model_id: str,
    known_challenge_keys: set[tuple[str, str]],
) -> ResponseSet:
    """Parse and validate a model's Round-3 JSON output against the exact
    set of challenges it was actually asked to respond to.

    ``known_challenge_keys`` must be ``(challenger_model_id, challenge_id)``
    pairs scoped to the single debate session this call belongs to — passing
    a global or cross-session set would let a response accidentally
    reference a challenge from a different debate session.
    """
    parsed = LLMProvider.parse_json_object_lenient(raw_text)
    if parsed is None:
        raise DebateContractError("response was not valid or recoverable JSON")
    try:
        response_set = ResponseSet.model_validate(parsed)
    except Exception as exc:
        raise DebateContractError(str(exc)) from exc
    if response_set.author_model_id != expected_author_model_id:
        raise DebateContractError(
            f"author_model_id '{response_set.author_model_id}' does not match "
            f"the requesting model '{expected_author_model_id}'"
        )
    unknown = {
        (item.challenger_model_id, item.challenge_id) for item in response_set.responses
    } - known_challenge_keys
    if unknown:
        raise DebateContractError(f"unknown challenge_id(s): {sorted(unknown)}")
    return response_set


# --- Findings contract (Phase 3 will populate this deterministically; no LLM
# summarization happens here or in Phase 2) -----------------------------------


class Agreement(BaseModel):
    """A point where the panel's peer review surfaced no real disagreement.

    This reflects an absence of a sustained challenge, not independent
    verification that the agreed-upon point is actually correct.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    model_ids: tuple[str, ...]
    supporting_challenge_ids: tuple[str, ...] = ()

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "description")

    @field_validator("model_ids")
    @classmethod
    def _at_least_one_model(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("model_ids must not be empty")
        return value


class UnresolvedDisagreement(BaseModel):
    """A challenge the target model defended and the challenger's concern remains live."""

    model_config = ConfigDict(extra="forbid")

    description: str
    challenge_id: str
    challenger_model_id: str
    target_model_id: str
    severity: Severity

    @field_validator("description", "challenge_id", "challenger_model_id", "target_model_id")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        return _require_non_blank(value, info.field_name)


class Correction(BaseModel):
    """A model's own stated revision or concession.

    This is the model's self-reported correction, surfaced for the user to
    judge — it is explicitly NOT an independently established factual
    correction, and must never be presented to the user as verified fact.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    model_id: str
    challenge_id: str
    stance: Literal["revises", "concedes"]
    previous_position: str | None = None
    updated_position: str

    @field_validator("description", "model_id", "challenge_id", "updated_position")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        return _require_non_blank(value, info.field_name)


class Risk(BaseModel):
    """A risk, limitation, or uncontested high-severity concern raised during review."""

    model_config = ConfigDict(extra="forbid")

    description: str
    severity: Severity
    raised_by_model_ids: tuple[str, ...]

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "description")

    @field_validator("raised_by_model_ids")
    @classmethod
    def _at_least_one_model(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("raised_by_model_ids must not be empty")
        return value


class Uncertainty(BaseModel):
    """A point the panel explicitly flagged as insufficiently supported either way."""

    model_config = ConfigDict(extra="forbid")

    description: str
    related_model_ids: tuple[str, ...] = ()

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "description")


class DebateFindings(BaseModel):
    """The complete structured output of a debate session.

    Populated deterministically by the orchestrator from completed
    ``ChallengeSet``/``ResponseSet`` rows — no LLM call is required to build
    this, and none happens in this module.

    ``agreements`` is reserved for a future contract in which a model
    explicitly states it agrees with another model's answer. Nothing in the
    current Round 2/3 contracts asks a model to do that, so the reducer
    never populates it today — it is always empty rather than inferring
    agreement from an absence of challenge. An answer nobody challenged is
    reported separately, in ``unchallenged_model_ids``, which carries no
    claim about correctness or consensus — only that peer review did not
    raise an objection to it.
    """

    model_config = ConfigDict(extra="forbid")

    agreements: tuple[Agreement, ...] = ()
    unchallenged_model_ids: tuple[str, ...] = ()
    unresolved_disagreements: tuple[UnresolvedDisagreement, ...] = ()
    corrections: tuple[Correction, ...] = ()
    risks: tuple[Risk, ...] = ()
    uncertainties: tuple[Uncertainty, ...] = ()
