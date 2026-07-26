"""Focused Phase 1B workflow-core tests; all provider work is mocked."""

from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.db.models import ScrapingBlueprintStatus
from app.services.scraping.blueprint_state_service import blueprint_state_service


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ScrapingBlueprintStatus.DRAFT, ScrapingBlueprintStatus.QUEUED),
        (ScrapingBlueprintStatus.QUEUED, ScrapingBlueprintStatus.RUNNING),
        (ScrapingBlueprintStatus.RUNNING, ScrapingBlueprintStatus.READY_FOR_REVIEW),
        (ScrapingBlueprintStatus.RUNNING, ScrapingBlueprintStatus.FAILED),
        (ScrapingBlueprintStatus.READY_FOR_REVIEW, ScrapingBlueprintStatus.APPROVED),
        (ScrapingBlueprintStatus.READY_FOR_REVIEW, ScrapingBlueprintStatus.REJECTED),
        (ScrapingBlueprintStatus.READY_FOR_REVIEW, ScrapingBlueprintStatus.DISCARDED),
    ],
)
def test_valid_workflow_transitions_succeed(
    current: ScrapingBlueprintStatus, target: ScrapingBlueprintStatus
) -> None:
    blueprint = SimpleNamespace(status=current)
    blueprint_state_service.transition(blueprint, target)
    assert blueprint.status == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ScrapingBlueprintStatus.APPROVED, ScrapingBlueprintStatus.QUEUED),
        (ScrapingBlueprintStatus.QUEUED, ScrapingBlueprintStatus.APPROVED),
        (ScrapingBlueprintStatus.FAILED, ScrapingBlueprintStatus.READY_FOR_REVIEW),
        (ScrapingBlueprintStatus.DISCARDED, ScrapingBlueprintStatus.APPROVED),
    ],
)
def test_invalid_workflow_transitions_fail(
    current: ScrapingBlueprintStatus, target: ScrapingBlueprintStatus
) -> None:
    with pytest.raises(ValidationError, match="Cannot transition"):
        blueprint_state_service.require_transition(current, target)
