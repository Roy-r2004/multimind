"""Derived presentation outcomes for scraping executions."""

from __future__ import annotations

from typing import Any

from app.db.models import ScrapingCoverageCell, ScrapingCoverageStatus, ScrapingExecutionStatus

GAP_COVERAGE_STATUSES = {
    ScrapingCoverageStatus.NOT_STARTED.value,
    ScrapingCoverageStatus.QUEUED.value,
    ScrapingCoverageStatus.IN_PROGRESS.value,
    ScrapingCoverageStatus.PARTIALLY_COVERED.value,
    ScrapingCoverageStatus.BLOCKED.value,
    ScrapingCoverageStatus.HUMAN_REVIEW_REQUIRED.value,
    ScrapingCoverageStatus.FAILED.value,
    ScrapingCoverageStatus.CANCELLED.value,
}


def execution_outcome_label(status: Any, coverage_debt: int = 0) -> str:
    status_value = _status_value(status)
    if status_value == ScrapingExecutionStatus.COMPLETED.value:
        return "Completed with Gaps" if coverage_debt > 0 else "Completed"
    explicit = {
        ScrapingExecutionStatus.FAILED.value: "Failed",
        ScrapingExecutionStatus.CANCELLED.value: "Cancelled",
        ScrapingExecutionStatus.QUEUED.value: "Queued",
        ScrapingExecutionStatus.RUNNING.value: "Running",
        ScrapingExecutionStatus.CANCEL_REQUESTED.value: "Cancellation Requested",
    }
    return explicit.get(status_value, status_value.replace("_", " ").title())


def coverage_gap_count(coverage: list[ScrapingCoverageCell]) -> int:
    return len([cell for cell in coverage if _status_value(cell.status) in GAP_COVERAGE_STATUSES])


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value or "")
