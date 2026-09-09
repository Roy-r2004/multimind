"""Post-verdict Simple Explanation — derived UI artifact, isolated from memory."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    CostRecord,
    UsageKind,
    Verdict,
    VerdictSimpleExplanation,
    VerdictSimpleExplanationStatus,
)
from app.llm.catalog import get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry

logger = get_logger(__name__)

SIMPLIFIER_MAX_TOKENS = 1024
SIMPLIFIER_USER_PROMPT = "Explain the supplied final Verdict now."


def _succeeded_with_content(row: VerdictSimpleExplanation | None) -> bool:
    if row is None:
        return False
    if row.status != VerdictSimpleExplanationStatus.SUCCEEDED:
        return False
    return bool((row.content or "").strip())


class VerdictSimplifierService:
    async def explain_verdict(
        self,
        db: AsyncSession,
        *,
        verdict_id: str,
        org_id: str,
        chat_id: str,
        turn_id: str,
        project_id: str | None = None,
    ) -> VerdictSimpleExplanation | None:
        """Generate or reuse a Simple Explanation. Never raises to fail a turn."""
        try:
            return await self._explain_verdict(
                db,
                verdict_id=verdict_id,
                org_id=org_id,
                chat_id=chat_id,
                turn_id=turn_id,
                project_id=project_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "verdict_simplifier_failed",
                verdict_id=verdict_id,
                turn_id=turn_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            try:
                await db.rollback()
                await self._mark_failed(db, verdict_id, str(exc))
                await db.commit()
            except Exception:  # noqa: BLE001
                await db.rollback()
            return None

    async def _explain_verdict(
        self,
        db: AsyncSession,
        *,
        verdict_id: str,
        org_id: str,
        chat_id: str,
        turn_id: str,
        project_id: str | None,
    ) -> VerdictSimpleExplanation | None:
        verdict = await db.get(Verdict, verdict_id)
        if verdict is None or not (verdict.text or "").strip():
            return None

        existing = await self._get_by_verdict_id(db, verdict_id)
        if _succeeded_with_content(existing):
            return existing

        model_id = (get_settings().chat_verdict_simplifier_model or "gpt-4.1-mini").strip()
        row = existing
        if row is None:
            row = VerdictSimpleExplanation(
                verdict_id=verdict_id,
                model_id=model_id,
                status=VerdictSimpleExplanationStatus.PENDING,
                content=None,
                error_message=None,
            )
            db.add(row)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing = await self._get_by_verdict_id(db, verdict_id)
                if _succeeded_with_content(existing):
                    return existing
                row = existing
                if row is None:
                    return None
                await db.refresh(row)

        row.status = VerdictSimpleExplanationStatus.PENDING
        row.model_id = model_id
        row.error_message = None
        await db.commit()

        system = get_prompt_engine().verdict_simple_explanation_prompt()
        user = self._user_payload(verdict.text, verdict.reason)
        model = get_model(model_id)
        provider = get_provider_registry().get_provider(model.provider)
        response = await provider.complete(
            system=system,
            user=user,
            model=model.provider_model,
            max_tokens=SIMPLIFIER_MAX_TOKENS,
            temperature=0.3,
        )
        content = (response.text or "").strip()
        if not content:
            row.status = VerdictSimpleExplanationStatus.FAILED
            row.error_message = "Simplifier returned an empty response"
            row.content = None
            await db.commit()
            logger.warning("verdict_simplifier_empty_output", verdict_id=verdict_id)
            return row

        cost_usd = resolve_llm_cost(
            model_id,
            response.tokens_input,
            response.tokens_output,
            response.cost_usd,
        )
        row.content = content
        row.status = VerdictSimpleExplanationStatus.SUCCEEDED
        row.error_message = None
        row.tokens_input = response.tokens_input
        row.tokens_output = response.tokens_output
        row.cost_usd = cost_usd
        db.add(
            CostRecord(
                org_id=org_id,
                chat_id=chat_id,
                project_id=project_id,
                turn_id=turn_id,
                model_id=model_id,
                kind=UsageKind.VERDICT_EXPLAIN,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                cost_usd=cost_usd,
            )
        )
        await db.commit()
        return row

    async def _get_by_verdict_id(
        self, db: AsyncSession, verdict_id: str
    ) -> VerdictSimpleExplanation | None:
        result = await db.execute(
            select(VerdictSimpleExplanation).where(
                VerdictSimpleExplanation.verdict_id == verdict_id
            )
        )
        return result.scalar_one_or_none()

    async def _mark_failed(self, db: AsyncSession, verdict_id: str, error: str) -> None:
        row = await self._get_by_verdict_id(db, verdict_id)
        if row is None:
            return
        if row.status == VerdictSimpleExplanationStatus.SUCCEEDED and (row.content or "").strip():
            return
        row.status = VerdictSimpleExplanationStatus.FAILED
        row.error_message = error[:4000]
        await db.flush()

    @staticmethod
    def _user_payload(verdict_text: str, verdict_reason: str | None) -> str:
        parts = [
            SIMPLIFIER_USER_PROMPT,
            "",
            "## Final Verdict",
            (verdict_text or "").strip(),
        ]
        reason = (verdict_reason or "").strip()
        if reason:
            parts.extend(["", "## Verdict rationale", reason])
        return "\n".join(parts)


verdict_simplifier_service = VerdictSimplifierService()
