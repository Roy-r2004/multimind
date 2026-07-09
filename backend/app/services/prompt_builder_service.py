"""Prompt builder support — rewrite rough user prompts into better prompts."""

from app.core.dependencies import AuthContext
from app.core.exceptions import AppError, ValidationError
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry
from app.schemas.api import PromptBuilderImproveResponse
from app.services.brain_service import DEFAULT_BRAIN_MODEL


class PromptBuilderService:
    async def improve(
        self,
        _auth: AuthContext,
        raw_prompt: str | None,
    ) -> PromptBuilderImproveResponse:
        prompt = (raw_prompt or "").strip()
        if not prompt:
            raise ValidationError("raw_prompt is required and cannot be empty")
        if len(prompt) > 4000:
            raise ValidationError("raw_prompt must be 4000 characters or fewer")

        providers = get_provider_registry()
        providers.validate_configured()

        model = get_model(DEFAULT_BRAIN_MODEL)
        provider = providers.get_provider(model.provider)
        system = (
            "You are a prompt editor. Rewrite the user's rough prompt into a stronger prompt. "
            "Preserve the original intent. Make it clearer, more specific, and more actionable. "
            "Add useful structure and output expectations when helpful. Add placeholders only when useful. "
            "Do not answer the prompt. Do not invent facts. Return only the upgraded prompt text."
        )
        response = await provider.complete(
            system=system,
            user=prompt,
            model=model.provider_model,
            max_tokens=512,
        )
        improved = response.text.strip()
        if not improved:
            raise AppError("Prompt improvement failed", code="LLM_ERROR")
        return PromptBuilderImproveResponse(improved_prompt=improved)


prompt_builder_service = PromptBuilderService()
