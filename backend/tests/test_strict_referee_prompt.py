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
  - When one or more supplied answers contain a materially useful table or structured comparison, preserve that information in the final answer using a table whenever a table remains an effective way to present it, even if the user did not explicitly request one. If multiple answers contain overlapping tables, reconcile and synthesize them into a single coherent table where practical. Do not collapse materially important comparative rows, columns, values, distinctions, or rankings into prose merely for brevity.
  - If multiple supplied answers independently use tables for the same comparison, treat that as strong evidence that tabular presentation is useful and include an appropriate table in the final answer. You may omit a source table only when it is redundant, irrelevant, factually unreliable, or genuinely clearer in another structure. Never copy tables merely verbatim; synthesize their useful information into the unified answer.
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
        "COUNCIL_ANSWER_ALPHA",
        "Gemini 2.5 Pro",
        "COUNCIL_ANSWER_BETA",
        "Grok 4",
        "AGENT_FAILURE_SENTINEL",
        '"evaluations"',
        '"text"',
        '"reason"',
    ):
        assert expected in rendered
    for internal_id in ("answer-alpha", "answer-beta", "answer-failed", "gpt-4.1"):
        assert internal_id not in rendered
    assert "COUNCIL ANSWER 1" in rendered
    assert "Model: GPT-4.1" in rendered
    assert "Never expose internal answer IDs, UUIDs, database IDs" in rendered
    assert "**Confidence**" not in rendered
    assert "Correctness / accuracy: 30 points" in rendered
    assert "highest-scoring answer must not automatically become the verdict" in rendered
    assert '"text": "<the fully explicit and exhaustive synthesis in Markdown>"' in rendered
    assert (
        '"reason": "<1-3 sentences identifying how the supplied answers were synthesized '
        'and any unresolved conflicts>"' in rendered
    )
    assert '"evaluations": [' in rendered


def test_strict_referee_preserves_useful_structure_without_blind_copying():
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="Compare the options",
        model_answers=_answers(),
    )

    assert "preserve that information in the final answer using a table" in rendered
    assert "even if the user did not explicitly request one" in rendered
    assert "reconcile and synthesize them into a single coherent table" in rendered
    assert "multiple supplied answers independently use tables" in rendered
    assert "treat that as strong evidence that tabular presentation is useful" in rendered
    assert "Do not collapse materially important comparative rows, columns, values" in rendered
    assert "merely for brevity" in rendered
    assert "redundant, irrelevant, factually unreliable" in rendered
    assert "Never copy tables merely verbatim" in rendered


def test_mandatory_structural_check_is_late_and_overrides_narrative_conciseness():
    custom = "CUSTOM_CONCISENESS_SENTINEL: use a unified narrative and be concise."
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="Compare the options",
        model_answers=_answers(),
        referee_instructions=custom,
    )

    custom_index = rendered.index(custom)
    answers_index = rendered.index("## Supplied AI Agent Answers")
    scoring_index = rendered.index("## Per-Answer Quality Evaluation")
    structural_index = rendered.index("## Mandatory Structural Synthesis Check")
    output_index = rendered.index("## Output Protocol")

    assert custom_index < answers_index < scoring_index < structural_index < output_index
    assert "two or more supplied answers contain tables for the same substantive comparison" in rendered
    assert "final Verdict **must include a synthesized table**" in rendered
    assert "Do not let the highest-scoring answer determine the final answer's structure" in rendered
    assert "unified narrative" in rendered
    assert "conciseness, or redundancy removal must not be interpreted" in rendered


def test_mandatory_structural_check_preserves_exceptions_and_requires_synthesis():
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="Compare the options",
        model_answers=_answers(),
    )

    assert "unless the user explicitly requests another format" in rendered
    assert "tabular content is irrelevant or factually unreliable" in rendered
    assert "Reconcile overlapping rows and columns" in rendered
    assert "Retain materially important values, distinctions, rankings" in rendered
    assert "Do not copy tables verbatim merely because they exist" in rendered


def test_strict_referee_includes_custom_verdict_instructions_but_excludes_runtime_context():
    excluded = (
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
        referee_instructions="CUSTOM_VERDICT_SENTINEL",
        user_brain_context=excluded[3],
        rolling_chat_memory=excluded[4],
        recent_conversation_context=excluded[5],
        playbook_context=excluded[6],
    )
    assert "## Custom Verdict Instructions" in rendered
    assert "CUSTOM_VERDICT_SENTINEL" in rendered
    for sentinel in excluded:
        assert sentinel not in rendered
    assert "Verdict AI (Chief Synthesizer)" not in rendered
    assert "Strategy: Referee (Chafiq)" not in rendered
    assert "Decompose â†’ Cluster & Map â†’ Evaluate & Score" not in rendered
    assert "Conciseness" not in rendered


def test_council_prompt_keeps_all_existing_context_sources():
    sentinels = {
        "council_runtime_context": (
            "ATTACHMENT_SENTINEL\nREFERENCE_SENTINEL\nREQUEST_RUNTIME_SENTINEL"
        ),
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
    assert "## Runtime Context" in rendered
    assert "ORIGINAL_QUESTION_SENTINEL" in rendered
    for sentinel in sentinels.values():
        assert sentinel in rendered


def test_custom_verdict_instructions_can_never_enter_council_prompt():
    rendered = PromptEngine().model_answer_prompt(
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_id="gpt-4.1",
        model_name="GPT-4.1",
        vendor="OpenAI",
        model_set_name="Council",
        council_runtime_context="ATTACHMENT_SENTINEL\nREFERENCE_SENTINEL",
    )
    assert "CUSTOM_VERDICT_SENTINEL" not in rendered
    assert "User / Organization Instructions" not in rendered


def test_empty_referee_instructions_omit_custom_section():
    rendered = PromptEngine().verdict_prompt(
        strategy="Referee",
        user_message="ORIGINAL_QUESTION_SENTINEL",
        model_answers=_answers(),
        referee_instructions=None,
    )
    assert "## Custom Verdict Instructions" not in rendered


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
