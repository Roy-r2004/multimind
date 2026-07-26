"""Centralized lifecycle rules for asynchronous blueprint versions."""

from typing import ClassVar

from app.core.exceptions import ValidationError
from app.db.models import ScrapingBlueprintStatus


class BlueprintStateService:
    _transitions: ClassVar[dict[ScrapingBlueprintStatus, set[ScrapingBlueprintStatus]]] = {
        ScrapingBlueprintStatus.DRAFT: {ScrapingBlueprintStatus.QUEUED, ScrapingBlueprintStatus.DISCARDED},
        ScrapingBlueprintStatus.QUEUED: {ScrapingBlueprintStatus.RUNNING, ScrapingBlueprintStatus.FAILED},
        ScrapingBlueprintStatus.RUNNING: {
            ScrapingBlueprintStatus.READY_FOR_REVIEW,
            ScrapingBlueprintStatus.FAILED,
        },
        ScrapingBlueprintStatus.READY_FOR_REVIEW: {
            ScrapingBlueprintStatus.APPROVED,
            ScrapingBlueprintStatus.REJECTED,
            ScrapingBlueprintStatus.DISCARDED,
        },
    }

    def require_transition(
        self, current: ScrapingBlueprintStatus, target: ScrapingBlueprintStatus
    ) -> None:
        if target not in self._transitions.get(current, set()):
            raise ValidationError(f"Cannot transition blueprint from {current.value} to {target.value}.")

    def transition(self, blueprint, target: ScrapingBlueprintStatus) -> None:
        self.require_transition(blueprint.status, target)
        blueprint.status = target


blueprint_state_service = BlueprintStateService()
