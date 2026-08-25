"""Static frontend contract for the backend-owned effective Referee prompt."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_model_set_modal_displays_effective_referee_prompt_read_only() -> None:
    source = (ROOT / "src/components/ModelSetModal.tsx").read_text(encoding="utf-8")

    assert 'strategy === "Referee"' in source
    assert ">Referee Prompt<" in source
    assert 'value={initial?.effectiveRefereePrompt ?? ""}' in source
    assert "readOnly" in source
    assert "REFEREE_CUSTOM_INSTRUCTIONS" not in source


def test_frontend_model_set_mapping_uses_api_field_without_prompt_copy() -> None:
    store = (ROOT / "src/lib/store.tsx").read_text(encoding="utf-8")
    api_types = (ROOT / "src/lib/api/types.ts").read_text(encoding="utf-8")

    assert "effective_referee_prompt?: string | null" in api_types
    assert "effectiveRefereePrompt: s.effective_referee_prompt ?? undefined" in store
    assert "You are the **Referee AI**" not in api_types
    assert "You are the **Referee AI**" not in store
