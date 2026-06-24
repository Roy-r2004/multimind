"""OpenRouter pricing — live rates from /api/v1/models, actual cost from response usage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
FALLBACK_PRICE = {"input": 0.002, "output": 0.004}


@dataclass(frozen=True)
class ModelPricing:
    """USD rates — OpenRouter returns price per token; we store per 1K for display."""

    input_per_1k: float
    output_per_1k: float
    source: str  # openrouter | fallback
    openrouter_slug: str | None = None
    fetched_at: datetime | None = None

    def cost_for_tokens(self, tokens_input: int, tokens_output: int) -> float:
        return (tokens_input / 1000) * self.input_per_1k + (tokens_output / 1000) * self.output_per_1k


class OpenRouterPricingService:
    def __init__(self) -> None:
        settings = get_settings()
        self._cache_ttl = settings.openrouter_pricing_cache_ttl_seconds
        self._by_slug: dict[str, ModelPricing] = {}
        self._by_model_id: dict[str, ModelPricing] = {}
        self._meta_by_slug: dict[str, dict[str, Any]] = {}
        self._last_refresh: datetime | None = None
        self._lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        headers: dict[str, str] = {}
        if settings.openrouter_api_key:
            headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
        return headers

    @staticmethod
    def _parse_rate(value: str | float | int | None) -> float:
        if value is None:
            return 0.0
        return float(value)

    def _pricing_from_or_entry(self, slug: str, pricing: dict[str, Any]) -> ModelPricing:
        # OpenRouter: USD per token (string). Convert to USD per 1K tokens.
        prompt = self._parse_rate(pricing.get("prompt"))
        completion = self._parse_rate(pricing.get("completion"))
        return ModelPricing(
            input_per_1k=prompt * 1000,
            output_per_1k=completion * 1000,
            source="openrouter",
            openrouter_slug=slug,
            fetched_at=datetime.now(UTC),
        )

    def _fallback_pricing(self, model_id: str) -> ModelPricing:
        from app.llm.catalog import MODEL_CATALOG, model_id_to_slug

        slug = model_id_to_slug(model_id)
        if model_id in MODEL_CATALOG:
            slug = MODEL_CATALOG[model_id].provider_model
        rates = FALLBACK_PRICE
        return ModelPricing(
            input_per_1k=rates["input"],
            output_per_1k=rates["output"],
            source="fallback",
            openrouter_slug=slug,
        )

    def _cache_stale(self) -> bool:
        if self._last_refresh is None:
            return True
        age = (datetime.now(UTC) - self._last_refresh).total_seconds()
        return age >= self._cache_ttl

    async def refresh(self, *, force: bool = False) -> int:
        async with self._lock:
            if not force and not self._cache_stale():
                return len(self._by_model_id)

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(OPENROUTER_MODELS_URL, headers=self._headers())
                    resp.raise_for_status()
                    payload = resp.json()
            except Exception as exc:
                logger.warning("openrouter_pricing_fetch_failed", error=str(exc))
                return len(self._by_model_id)

            slug_map: dict[str, ModelPricing] = {}
            meta_map: dict[str, dict[str, Any]] = {}
            for item in payload.get("data", []):
                slug = item.get("id")
                pricing = item.get("pricing")
                if not slug or not pricing:
                    continue
                slug_map[slug] = self._pricing_from_or_entry(slug, pricing)
                meta_map[slug] = item

            self._by_slug = slug_map
            self._meta_by_slug = meta_map
            self._by_model_id = {}
            matched = 0
            from app.llm.catalog import MODEL_CATALOG

            for model_id, entry in MODEL_CATALOG.items():
                or_pricing = slug_map.get(entry.provider_model)
                if or_pricing:
                    self._by_model_id[model_id] = or_pricing
                    matched += 1
                else:
                    self._by_model_id[model_id] = self._fallback_pricing(model_id)

            self._last_refresh = datetime.now(UTC)
            logger.info(
                "openrouter_pricing_refreshed",
                matched=matched,
                total=len(MODEL_CATALOG),
                catalog_slugs=len(slug_map),
            )
            return matched

    async def ensure_loaded(self) -> None:
        if self._cache_stale():
            await self.refresh()

    def get_pricing(self, model_id: str) -> ModelPricing:
        if model_id in self._by_model_id:
            return self._by_model_id[model_id]
        from app.llm.catalog import model_id_to_slug

        slug = model_id_to_slug(model_id)
        if slug in self._by_slug:
            return self._by_slug[slug]
        return self._fallback_pricing(model_id)

    def compute_cost(self, model_id: str, tokens_input: int, tokens_output: int) -> float:
        return self.get_pricing(model_id).cost_for_tokens(tokens_input, tokens_output)

    def resolve_actual_cost(
        self,
        *,
        model_id: str,
        tokens_input: int,
        tokens_output: int,
        reported_cost_usd: float | None,
    ) -> float:
        """Prefer OpenRouter-reported charge from usage.cost when present."""
        if reported_cost_usd is not None and reported_cost_usd >= 0:
            return round(reported_cost_usd, 6)
        return round(self.compute_cost(model_id, tokens_input, tokens_output), 6)

    def catalog_snapshot(self) -> list[dict[str, Any]]:
        from app.llm.catalog import MODEL_CATALOG

        return [
            {
                "model_id": model_id,
                "openrouter_slug": entry.provider_model,
                "input_per_1k": self.get_pricing(model_id).input_per_1k,
                "output_per_1k": self.get_pricing(model_id).output_per_1k,
                "source": self.get_pricing(model_id).source,
            }
            for model_id, entry in MODEL_CATALOG.items()
        ]

    @property
    def last_refresh(self) -> datetime | None:
        return self._last_refresh

    def get_slug_metadata(self, slug: str) -> dict[str, Any] | None:
        return self._meta_by_slug.get(slug)

    def search_models(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Search live OpenRouter catalog by name, slug, or description."""
        q = query.strip().lower()
        if not q:
            return []

        scored: list[tuple[int, dict[str, Any]]] = []
        for slug, item in self._meta_by_slug.items():
            name = (item.get("name") or "").lower()
            desc = (item.get("description") or "").lower()
            slug_l = slug.lower()
            score = 0
            if slug_l == q or name == q:
                score = 100
            elif slug_l.startswith(q) or name.startswith(q):
                score = 80
            elif q in slug_l:
                score = 60
            elif q in name:
                score = 50
            elif q in desc:
                score = 30
            else:
                continue

            pricing = self._by_slug.get(slug)
            scored.append(
                (
                    score,
                    {
                        "openrouter_slug": slug,
                        "name": item.get("name") or slug,
                        "description": item.get("description") or "",
                        "vendor": vendor_from_slug(slug),
                        "context_length": item.get("context_length"),
                        "input_per_1k": pricing.input_per_1k if pricing else 0.0,
                        "output_per_1k": pricing.output_per_1k if pricing else 0.0,
                    },
                )
            )

        scored.sort(key=lambda x: (-x[0], x[1]["name"]))
        return [item for _, item in scored[:limit]]

    def pricing_for_slug(self, slug: str) -> ModelPricing:
        return self._by_slug.get(slug) or ModelPricing(
            input_per_1k=FALLBACK_PRICE["input"],
            output_per_1k=FALLBACK_PRICE["output"],
            source="fallback",
            openrouter_slug=slug,
        )


_pricing_service: OpenRouterPricingService | None = None


def get_pricing_service() -> OpenRouterPricingService:
    global _pricing_service
    if _pricing_service is None:
        _pricing_service = OpenRouterPricingService()
    return _pricing_service


def vendor_from_slug(slug: str) -> str:
    provider = slug.split("/")[0] if "/" in slug else slug
    labels = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google",
        "meta-llama": "Meta",
        "mistralai": "Mistral",
        "deepseek": "DeepSeek",
        "qwen": "Alibaba",
        "x-ai": "xAI",
        "cohere": "Cohere",
        "perplexity": "Perplexity",
    }
    return labels.get(provider, provider.replace("-", " ").title())
