"""AI Debate Mode prompt construction — isolated from Council/Verdict prompts.

Builds the Round 2 (peer review / challenge) and Round 3 (rebuttal /
response) prompts for AI Debate Mode. This module never touches
``model_answer.j2``, ``verdict.j2``, ``referee.j2``, or any Challenge/
Lessons/disagree-discuss template, and it does not modify ``prompt_engine.py``
— it only calls that engine's already-public, generic ``render()`` method
with two new, separate template files under ``prompts/system/``.

Debate Mode runs strictly after the Council and Verdict already exist. The
other models' original answers (and, in Round 3, their challenges) are
untrusted content from the model's own point of view: each is wrapped in an
explicit ``<panelist_answer>``/``<challenge>`` tag in the templates and the
templates instruct the model to treat that content as data, never as
instructions to follow.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.prompt_engine import get_prompt_engine

# Soft character caps, not a token-budget system. Debate Mode deliberately
# does not reuse the orchestrator's document-context window budgeting
# (app.services.document_context) — that machinery is tightly coupled to
# Council/Verdict context packing, which Phase 1's isolation requirement
# says this feature must not touch or depend on. These are simple, explicit
# limits so an unusually long panelist answer or challenge still produces a
# bounded, predictable prompt.
DEFAULT_MAX_ANSWER_CHARS = 6_000
DEFAULT_MAX_CHALLENGE_FIELD_CHARS = 2_000
_TRUNCATION_NOTE = "\n\n[...truncated for length; {omitted} characters omitted...]"


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + _TRUNCATION_NOTE.format(omitted=omitted)


@dataclass(frozen=True)
class PanelistAnswer:
    """One model's original, already-persisted Council answer.

    Carries only what Debate Mode needs to display and attribute an answer —
    never constructed from anything but already-completed ``ModelAnswer``
    rows; this module does not read the database itself.
    """

    model_id: str
    model_name: str
    text: str

    def __post_init__(self) -> None:
        if not (self.model_id or "").strip():
            raise ValueError("PanelistAnswer.model_id must not be blank")
        if not (self.model_name or "").strip():
            raise ValueError("PanelistAnswer.model_name must not be blank")


@dataclass(frozen=True)
class ChallengeForResponse:
    """One challenge formatted for display inside a Round-3 response prompt."""

    challenge_id: str
    challenger_model_id: str
    issue: str
    reasoning: str
    severity: str
    uncertainty: str | None = None

    def __post_init__(self) -> None:
        if not (self.challenge_id or "").strip():
            raise ValueError("ChallengeForResponse.challenge_id must not be blank")
        if not (self.challenger_model_id or "").strip():
            raise ValueError("ChallengeForResponse.challenger_model_id must not be blank")


def build_challenge_prompt(
    *,
    user_message: str,
    author: PanelistAnswer,
    other_answers: list[PanelistAnswer],
    max_answer_chars: int = DEFAULT_MAX_ANSWER_CHARS,
) -> str:
    """Render the Round-2 peer-review prompt for ``author``.

    ``author`` reviews every other participating model in this single
    request (O(n) prompts per debate round, never a pairwise O(n^2) call per
    (reviewer, target) pair).
    """
    if not other_answers:
        raise ValueError("build_challenge_prompt requires at least one other panelist answer")
    duplicate_ids = {a.model_id for a in other_answers if a.model_id == author.model_id}
    if duplicate_ids:
        raise ValueError("other_answers must not include the author's own model_id")

    return get_prompt_engine().render(
        "system/debate_challenge.j2",
        user_message=user_message,
        author_model_id=author.model_id,
        author_model_name=author.model_name,
        author_answer_text=_truncate(author.text, max_answer_chars),
        other_answers=[
            {
                "model_id": answer.model_id,
                "model_name": answer.model_name,
                "text": _truncate(answer.text, max_answer_chars),
            }
            for answer in other_answers
        ],
    )


def build_response_prompt(
    *,
    user_message: str,
    author: PanelistAnswer,
    challenges: list[ChallengeForResponse],
    supporting_answers: list[PanelistAnswer] | None = None,
    max_answer_chars: int = DEFAULT_MAX_ANSWER_CHARS,
    max_challenge_field_chars: int = DEFAULT_MAX_CHALLENGE_FIELD_CHARS,
) -> str:
    """Render the Round-3 rebuttal prompt for the model named in ``author``.

    ``challenges`` must be exactly the challenges targeting this model for
    this debate session — never challenges targeting other models.
    """
    if not challenges:
        raise ValueError("build_response_prompt requires at least one challenge to respond to")
    mistargeted = [c for c in challenges if c.challenger_model_id == author.model_id]
    if mistargeted:
        raise ValueError("a model cannot be asked to respond to its own challenge")

    supporting = supporting_answers or []
    duplicate_ids = {a.model_id for a in supporting if a.model_id == author.model_id}
    if duplicate_ids:
        raise ValueError("supporting_answers must not include the author's own model_id")

    return get_prompt_engine().render(
        "system/debate_response.j2",
        user_message=user_message,
        author_model_id=author.model_id,
        author_model_name=author.model_name,
        author_answer_text=_truncate(author.text, max_answer_chars),
        supporting_answers=[
            {
                "model_id": answer.model_id,
                "model_name": answer.model_name,
                "text": _truncate(answer.text, max_answer_chars),
            }
            for answer in supporting
        ],
        challenges=[
            {
                "challenge_id": challenge.challenge_id,
                "challenger_model_id": challenge.challenger_model_id,
                "issue": _truncate(challenge.issue, max_challenge_field_chars),
                "reasoning": _truncate(challenge.reasoning, max_challenge_field_chars),
                "severity": challenge.severity,
                "uncertainty": (
                    _truncate(challenge.uncertainty, max_challenge_field_chars)
                    if challenge.uncertainty
                    else None
                ),
            }
            for challenge in challenges
        ],
    )
