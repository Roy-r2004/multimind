"""Circuit breaker + LLM budget tracker for maps census — prevent cost spirals and cascading failures."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker state machine."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing; reject calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker per provider."""

    failure_threshold: int = 5  # Failures before opening
    recovery_timeout_seconds: int = 60  # Time before trying recovery
    success_threshold_half_open: int = 2  # Successes needed to close


@dataclass
class CircuitBreakerMetrics:
    """Metrics for observability."""

    failures: int = 0
    successes: int = 0
    last_failure_at: datetime | None = None
    opened_at: datetime | None = None
    state_changes: list[dict[str, Any]] = field(default_factory=list)


class CircuitBreaker:
    """Per-provider circuit breaker — fail fast instead of hanging."""

    def __init__(self, provider_name: str, config: CircuitBreakerConfig | None = None):
        self.provider_name = provider_name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.metrics = CircuitBreakerMetrics()
        self._lock = asyncio.Lock()

    async def call(self, fn, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_recovery():
                    self.state = CircuitState.HALF_OPEN
                    self._log_state_change("OPEN → HALF_OPEN (recovery attempt)")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker OPEN for {self.provider_name}; "
                        f"{self.metrics.failures} failures; retry in "
                        f"{self._seconds_until_recovery()}s"
                    )

        try:
            result = await fn(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure()
            raise

    async def _on_success(self) -> None:
        """Record success and possibly close breaker."""
        async with self._lock:
            self.metrics.successes += 1
            if self.state == CircuitState.HALF_OPEN:
                if self.metrics.successes >= self.config.success_threshold_half_open:
                    self.state = CircuitState.CLOSED
                    self.metrics.successes = 0
                    self.metrics.failures = 0
                    self._log_state_change("HALF_OPEN → CLOSED (recovered)")

    async def _on_failure(self) -> None:
        """Record failure and possibly open breaker."""
        async with self._lock:
            self.metrics.failures += 1
            self.metrics.last_failure_at = datetime.now(UTC)
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.metrics.opened_at = datetime.now(UTC)
                self._log_state_change("HALF_OPEN → OPEN (recovery failed)")
            elif self.metrics.failures >= self.config.failure_threshold and self.state == CircuitState.CLOSED:
                self.state = CircuitState.OPEN
                self.metrics.opened_at = datetime.now(UTC)
                self._log_state_change(f"CLOSED → OPEN ({self.metrics.failures} failures)")

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time passed to attempt recovery."""
        if self.metrics.opened_at is None:
            return False
        elapsed = (datetime.now(UTC) - self.metrics.opened_at).total_seconds()
        return elapsed >= self.config.recovery_timeout_seconds

    def _seconds_until_recovery(self) -> int:
        """Seconds until next recovery attempt."""
        if self.metrics.opened_at is None:
            return 0
        elapsed = (datetime.now(UTC) - self.metrics.opened_at).total_seconds()
        remaining = self.config.recovery_timeout_seconds - elapsed
        return max(0, int(remaining))

    def _log_state_change(self, msg: str) -> None:
        """Log and track state change."""
        logger.warning(f"maps_circuit_breaker provider={self.provider_name} {msg}")
        self.metrics.state_changes.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "provider": self.provider_name,
                "message": msg,
                "state": self.state.value,
                "failures": self.metrics.failures,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """For monitoring/dashboards."""
        return {
            "provider": self.provider_name,
            "state": self.state.value,
            "failures": self.metrics.failures,
            "successes": self.metrics.successes,
            "last_failure_at": self.metrics.last_failure_at.isoformat() if self.metrics.last_failure_at else None,
            "seconds_until_recovery": self._seconds_until_recovery(),
            "recent_changes": self.metrics.state_changes[-5:],  # Last 5 changes
        }


@dataclass
class LLMCallBudget:
    """Per-cell or per-run LLM call budget."""

    run_id: str
    cell_id: str | None
    max_calls: int = 10
    spent: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def remaining(self) -> int:
        """Calls remaining in budget."""
        return max(0, self.max_calls - self.spent)

    @property
    def is_exhausted(self) -> bool:
        """True if budget is spent."""
        return self.spent >= self.max_calls

    def try_spend(self, cost: int = 1) -> bool:
        """Attempt to spend budget. Returns True if successful."""
        if self.spent + cost <= self.max_calls:
            self.spent += cost
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """For logging/monitoring."""
        return {
            "run_id": self.run_id,
            "cell_id": self.cell_id,
            "max_calls": self.max_calls,
            "spent": self.spent,
            "remaining": self.remaining,
            "utilization_pct": int(100 * self.spent / self.max_calls) if self.max_calls else 0,
            "elapsed_seconds": int((datetime.now(UTC) - self.start_time).total_seconds()),
        }


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""

    pass


class LLMBudgetExhaustedError(Exception):
    """Raised when LLM budget exhausted for cell/run."""

    pass
