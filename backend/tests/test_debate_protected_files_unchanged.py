"""Phase 3 regression guard: protected Council/Verdict/Challenge files must be
byte-identical to the commit immediately before any Debate Mode work began.

Unlike Phase 2's self-computed SHA-256 snapshot (hashes generated only after
the fact, which can't catch a change already baked into that same snapshot),
this uses `git diff` against a fixed, independent baseline commit that
predates Debate Mode entirely — the merge commit immediately before Phase
1's first commit (f803fe9) on this branch.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_REF = "edc4333"  # merge commit immediately preceding Phase 1 (f803fe9)

PROTECTED_PATHS = [
    "backend/app/prompts/system/base.j2",
    "backend/app/prompts/system/model_answer.j2",
    "backend/app/prompts/system/verdict.j2",
    "backend/app/prompts/system/referee.j2",
    "backend/app/prompts/system/disagree_discuss.j2",
    "backend/app/prompts/system/disagree_finalize.j2",
    "backend/app/prompts/system/verdict_lesson.j2",
    "backend/app/prompts/system/brain_update.j2",
    "backend/app/prompts/partials/strategy_instructions.j2",
    "backend/app/llm/prompt_engine.py",
    "backend/app/llm/orchestrator.py",
    "backend/app/llm/providers.py",
    "backend/app/services/chat_service.py",
    "backend/app/services/lesson_service.py",
    "backend/app/api/v1/lessons.py",
    "backend/app/api/v1/chats.py",
    "backend/app/api/v1/verdicts.py",
]


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], cwd=REPO_ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return (REPO_ROOT / ".git").exists()


def _baseline_ref_exists() -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{BASELINE_REF}^{{commit}}"], cwd=REPO_ROOT, capture_output=True
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _git_available() or not _baseline_ref_exists(),
    reason="git or the stable baseline commit is unavailable in this environment",
)


def test_protected_files_have_zero_diff_against_pre_debate_baseline():
    result = subprocess.run(
        ["git", "diff", "--stat", BASELINE_REF, "--", *PROTECTED_PATHS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", (
        f"Protected file(s) changed since the pre-Debate-Mode baseline ({BASELINE_REF}):\n"
        f"{result.stdout}"
    )


def test_protected_paths_list_itself_still_exists_in_the_repo():
    """A path silently renamed/removed would make the diff above trivially
    pass with nothing to compare — catch that case explicitly."""
    for relative_path in PROTECTED_PATHS:
        assert (REPO_ROOT / relative_path).exists(), f"{relative_path} is missing"
