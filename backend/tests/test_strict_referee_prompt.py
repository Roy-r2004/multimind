"""Strict synthesis-only Referee prompt isolation."""

from app.llm.prompt_engine import STRICT_REFEREE_BEHAVIOR, PromptEngine

EXPECTED_STRICT_REFEREE_BEHAVIOR = """You are the **Referee AI** embedded within my LLM platform. Your role is strictly limited to the following:

#### 1. Data Scope and Boundaries

- Your **only source of information** is the set of answers provided by the individual AI agents in response to my prompt.
- You must **not** consult external web resources, your own pre-trained knowledge, or any information outside the supplied AI answers.

#### 2. Task Definition

- **Synthesize**: Carefully read all AI responses and construct a single, comprehensive answer that:
  - Reflects the strongest, most relevant, and well-supported points from each AI response.
  - Resolves contradictions, highlights areas of consensus, and faithfully incorporates important nuances or minority viewpoints where relevant.
  - Presents the unified answer in a logically structured, clear, and unambiguous manner, avoiding unnecessary repetition.
  - **Do NOT** provide a summary, a resume, or a concise answer. Your output must be fully explicit and elaborate, spelling out all reasoning, details, and supporting logic from the provided AI responses. Every component of your output should be as detailed and explicit as possible, leaving no reasoning or nuance implicit or abbreviated.
- **Do NOT** answer the original prompt independently, nor inject new content or reasoning not present in the AI responses.

#### 3. Operational Logic

- Treat yourself as a specialized synthesis engine, not a general-purpose AI assistant.
- Your **entire output** must be derived solely from the set of AI-generated answers provided for each prompt.

---

This prompt strictly prohibits any summarization, resumes, or concise forms. The Referee AI is directed to be as explicit and detailed as possible, ensuring exhaustive elaboration and clarity in every output."""


def _answers() -> list[dict]:
    return [
        {
            "answer_id": "answer-alpha",
            "model_id": "gpt-4.1",
            "model_name": "GPT-4.1",
            "text": "COUNCIL_ANSWER_ALPHA",
            "confidence": 91,
            "failed": False,
            "error_message": None,
        },
        {
            "answer_id": "answer-beta",
            "model_id": "gemini",
            "model_name": "Gemini 2.5 Pro",
            "text": "COUNCIL_ANSWER_BETA",
            "confidence": 83,
            "failed": False,
            "error_message": None,
        },
        {
            "answer_id": "answer-failed",
            "model_id": "or:x-ai--grok-4",
            "model_name": "Grok 4",
            "text": "",
            "confidence": 0,
            "failed": True,
            "error_message": "AGENT_FAILURE_SENTINEL",
        },
    ]


def test_strict_referee_behavior_is_exact_and_rendered_verbatim():
    assert STRICT_REFEREE_BEHAVIOR == EXPECTED_STRICT_REFEREE_BEHAVIOR
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_answers=_answers(),
    )
    assert rendered.startswith(f"{EXPECTED_STRICT_REFEREE_BEHAVIOR}\n\n")
    assert rendered.count(EXPECTED_STRICT_REFEREE_BEHAVIOR) == 1


def test_strict_referee_receives_question_answers_metadata_and_json_protocol():
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_answers=_answers(),
    )
    for expected in (
        "ORIGINAL_QUESTION_SENTINEL",
        "GPT-4.1",
        "gpt-4.1",
        "COUNCIL_ANSWER_ALPHA",
        "answer-alpha",
        "Gemini 2.5 Pro",
        "COUNCIL_ANSWER_BETA",
        "Grok 4",
        "AGENT_FAILURE_SENTINEL",
        '"evaluations"',
        '"text"',
        '"reason"',
    ):
        assert expected in rendered
    assert "**Confidence**" not in rendered
    assert "Correctness / accuracy: 30 points" in rendered
    assert "highest-scoring answer must not automatically become the verdict" in rendered


def test_strict_referee_excludes_all_direct_non_council_context_and_legacy_rules():
    excluded = (
        "CHAFIC_LEGACY_CUSTOM_SENTINEL",
        "REFERENCE_HANDOFF_SENTINEL",
        "ATTACHMENT_EXCERPT_SENTINEL",
        "IMAGE_CONTEXT_SENTINEL",
        "BRAIN_CONTEXT_SENTINEL",
        "ROLLING_MEMORY_SENTINEL",
        "RECENT_HISTORY_SENTINEL",
        "PLAYBOOK_CONTEXT_SENTINEL",
    )
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_answers=_answers(),
        custom_instructions="\n".join(excluded[:4]),
        user_brain_context=excluded[4],
        rolling_chat_memory=excluded[5],
        recent_conversation_context=excluded[6],
        playbook_context=excluded[7],
    )
    for sentinel in excluded:
        assert sentinel not in rendered
    assert "Verdict AI (Chief Synthesizer)" not in rendered
    assert "Strategy: Referee (Chafiq)" not in rendered
    assert "Decompose â†’ Cluster & Map â†’ Evaluate & Score" not in rendered
    assert "Conciseness" not in rendered


def test_council_prompt_keeps_all_existing_context_sources():
    sentinels = {
        "custom_instructions": "CHAFIC_REFERENCE_ATTACHMENT_IMAGE_SENTINEL",
        "user_brain_context": "BRAIN_CONTEXT_SENTINEL",
        "rolling_chat_memory": "ROLLING_MEMORY_SENTINEL",
        "recent_conversation_context": "RECENT_HISTORY_SENTINEL",
        "playbook_context": "PLAYBOOK_CONTEXT_SENTINEL",
    }
    rendered = PromptEngine().model_answer_prompt(
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_id="gpt-4.1",
        model_name="GPT-4.1",
        vendor="OpenAI",
        model_set_name="Chafic ultimate model set",
        **sentinels,
    )
    assert "Independent Expert Responder" in rendered
    assert "ORIGINAL_QUESTION_SENTINEL" in rendered
    for sentinel in sentinels.values():
        assert sentinel in rendered


def test_non_referee_verdict_strategy_remains_on_existing_template():
    rendered = PromptEngine().verdict_prompt(
        strategy="Synthesize",
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_answers=_answers(),
        custom_instructions="EXISTING_CUSTOM_SENTINEL",
        user_brain_context="BRAIN_CONTEXT_SENTINEL",
    )
    assert "Verdict AI (Chief Synthesizer)" in rendered
    assert "EXISTING_CUSTOM_SENTINEL" in rendered
    assert "BRAIN_CONTEXT_SENTINEL" in rendered
    assert EXPECTED_STRICT_REFEREE_BEHAVIOR not in rendered
