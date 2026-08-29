"""Static frontend contract for the backend-owned effective Referee prompt."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_model_set_modal_displays_effective_referee_prompt_read_only() -> None:
    source = (ROOT / "src/components/ModelSetModal.tsx").read_text(encoding="utf-8")

    assert 'strategy === "Referee"' in source
    assert ">Fixed Referee Prompt<" in source
    assert 'value={initial?.effectiveRefereePrompt ?? ""}' in source
    assert "readOnly" in source
    assert "REFEREE_CUSTOM_INSTRUCTIONS" not in source


def test_referee_strategy_exposes_editable_custom_verdict_instructions() -> None:
    source = (ROOT / "src/components/ModelSetModal.tsx").read_text(encoding="utf-8")

    referee_branch = source.split('{strategy === "Referee" ? (', 1)[1].split(") : (", 1)[0]
    assert "Custom Verdict Instructions" in referee_branch
    assert "value={custom}" in referee_branch
    assert "onChange={(e) => setCustom(e.target.value)}" in referee_branch


def test_frontend_model_set_mapping_uses_api_field_without_prompt_copy() -> None:
    store = (ROOT / "src/lib/store.tsx").read_text(encoding="utf-8")
    api_types = (ROOT / "src/lib/api/types.ts").read_text(encoding="utf-8")

    assert "effective_referee_prompt?: string | null" in api_types
    assert "effectiveRefereePrompt: s.effective_referee_prompt ?? undefined" in store
    assert "You are the **Referee AI**" not in api_types
    assert "You are the **Referee AI**" not in store
