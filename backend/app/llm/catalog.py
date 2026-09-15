"""Canonical model catalog — built-in shortcuts + dynamic OpenRouter models."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.pricing import get_pricing_service, vendor_from_slug


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    name: str
    vendor: str
    color: str
    blurb: str
    provider: str  # openrouter
    provider_model: str  # OpenRouter model slug
    is_custom: bool = False


# Built-in shortcuts — slugs track OpenRouter flagships.
MODEL_CATALOG: dict[str, ModelCatalogEntry] = {
    "gpt-4.1": ModelCatalogEntry(
        id="gpt-4.1",
        name="GPT-4.1",
        vendor="OpenAI",
        color="oklch(0.75 0.13 155)",
        blurb="OpenAI flagship",
        provider="openrouter",
        provider_model="openai/gpt-4.1",
    ),
    "gpt-4.1-mini": ModelCatalogEntry(
        id="gpt-4.1-mini",
        name="GPT-4.1 Mini",
        vendor="OpenAI",
        color="oklch(0.75 0.13 155)",
        blurb="Cheaper OpenAI structured extraction",
        provider="openrouter",
        provider_model="openai/gpt-4.1-mini",
    ),
    "gpt-5-nano": ModelCatalogEntry(
        id="gpt-5-nano",
        name="GPT-5 Nano",
        vendor="OpenAI",
        color="oklch(0.75 0.13 155)",
        blurb="Low-cost high-volume classification",
        provider="openrouter",
        provider_model="openai/gpt-5-nano",
    ),
    "sonar": ModelCatalogEntry(
        id="sonar",
        name="Sonar",
        vendor="Perplexity",
        color="oklch(0.68 0.14 210)",
        blurb="Fast web-grounded search",
        provider="openrouter",
        provider_model="perplexity/sonar",
    ),
    "sonar-pro": ModelCatalogEntry(
        id="sonar-pro",
        name="Sonar Pro",
        vendor="Perplexity",
        color="oklch(0.62 0.16 210)",
        blurb="Deep web search for official sources",
        provider="openrouter",
        provider_model="perplexity/sonar-pro",
    ),
    "claude": ModelCatalogEntry(
        id="claude",
        name="Claude Sonnet 4",
        vendor="Anthropic",
        color="oklch(0.78 0.12 60)",
        blurb="Careful reasoner",
        provider="openrouter",
        provider_model="anthropic/claude-sonnet-4",
    ),
    "gemini": ModelCatalogEntry(
        id="gemini",
        name="Gemini 2.5 Pro",
        vendor="Google",
        color="oklch(0.72 0.14 240)",
        blurb="Multimodal frontier",
        provider="openrouter",
        provider_model="google/gemini-2.5-pro",
    ),
    "grok": ModelCatalogEntry(
        id="grok",
        name="Grok",
        vendor="xAI",
        color="oklch(0.42 0.04 260)",
        blurb="xAI frontier model",
        provider="openrouter",
        provider_model="x-ai/grok-4.3",
    ),
    "mistral": ModelCatalogEntry(
        id="mistral",
        name="Mistral Large",
        vendor="Mistral",
        color="oklch(0.68 0.16 25)",
        blurb="Fast European frontier",
        provider="openrouter",
        provider_model="mistralai/mistral-large-2512",
    ),
    "deepseek": ModelCatalogEntry(
        id="deepseek",
        name="DeepSeek V3",
        vendor="DeepSeek",
        color="oklch(0.65 0.17 280)",
        blurb="Coding specialist",
        provider="openrouter",
        provider_model="deepseek/deepseek-chat-v3-0324",
    ),
    "llama": ModelCatalogEntry(
        id="llama",
        name="Llama 3.3 70B",
        vendor="Meta",
        color="oklch(0.72 0.14 200)",
        blurb="Open-weight workhorse",
        provider="openrouter",
        provider_model="meta-llama/llama-3.3-70b-instruct",
    ),
    "qwen": ModelCatalogEntry(
        id="qwen",
        name="Qwen 2.5 72B",
        vendor="Alibaba",
        color="oklch(0.70 0.13 195)",
        blurb="Strong multilingual reasoning",
        provider="openrouter",
        provider_model="qwen/qwen-2.5-72b-instruct",
    ),
}

FALLBACK_PRICE = {"input": 0.002, "output": 0.004}

_BUILTIN_IDS = frozenset(MODEL_CATALOG.keys())

# Display-only Council members. They run and persist like production models
# but must never enter Verdict, Brain, memory, Playbook, or other intelligence.
SHADOW_MODEL_SLUGS = frozenset(
    {
        "nvidia/nemotron-3-ultra-550b-a55b",
        "qwen/qwen3.8-max-0902",
        "deepseek/deepseek-v4.1-flash",
    }
)


def is_builtin_model_id(model_id: str) -> bool:
    return model_id in _BUILTIN_IDS


def is_shadow_model(model_id: str) -> bool:
    """True only for the three explicit test/shadow OpenRouter models."""
    raw = (model_id or "").strip()
    if not raw:
        return False
    if raw in SHADOW_MODEL_SLUGS:
        return True
    return model_id_to_slug(raw) in SHADOW_MODEL_SLUGS


def is_intelligence_eligible_model(model_id: str) -> bool:
    return not is_shadow_model(model_id)


def intelligence_eligible_model_ids(model_ids: Iterable[str]) -> list[str]:
    """Preserve caller order while dropping shadow model ids."""
    return [model_id for model_id in model_ids if is_intelligence_eligible_model(model_id)]


def intelligence_eligible_answers(answers: Sequence[Any]) -> list[Any]:
    """Filter ModelAnswer rows or answer-context dicts for intelligence pipelines."""
    eligible: list[Any] = []
    for answer in answers:
        if isinstance(answer, dict):
            model_id = answer.get("model_id")
        else:
            model_id = getattr(answer, "model_id", None)
        if isinstance(model_id, str) and is_intelligence_eligible_model(model_id):
            eligible.append(answer)
    return eligible


def slug_to_model_id(openrouter_slug: str) -> str:
    """Stable internal id for org-added OpenRouter models."""
    return f"or:{openrouter_slug.replace('/', '--')}"


def model_id_to_slug(model_id: str) -> str:
    if model_id in MODEL_CATALOG:
        return MODEL_CATALOG[model_id].provider_model
    if model_id.startswith("or:"):
        return model_id[3:].replace("--", "/")
    return model_id


def color_for_key(key: str) -> str:
    hue = sum(ord(c) for c in key) % 360
    return f"oklch(0.68 0.14 {hue})"


def enrich_entry(entry: ModelCatalogEntry) -> ModelCatalogEntry:
    """Apply live OpenRouter name/description when cached."""
    pricing = get_pricing_service()
    meta = pricing.get_slug_metadata(entry.provider_model)
    if not meta:
        return entry
    live_name = meta.get("name") or entry.name
    live_blurb = (meta.get("description") or entry.blurb)[:280]
    return ModelCatalogEntry(
        id=entry.id,
        name=live_name,
        vendor=entry.vendor,
        color=entry.color,
        blurb=live_blurb,
        provider=entry.provider,
        provider_model=entry.provider_model,
        is_custom=entry.is_custom,
    )


def entry_from_slug(model_id: str, slug: str, *, name: str | None = None, vendor: str | None = None, blurb: str = "") -> ModelCatalogEntry:
    pricing = get_pricing_service()
    meta = pricing.get_slug_metadata(slug) or {}
    resolved_name = name or meta.get("name") or slug.split("/")[-1].replace("-", " ").title()
    resolved_vendor = vendor or vendor_from_slug(slug)
    resolved_blurb = blurb or (meta.get("description") or "")[:280]
    return ModelCatalogEntry(
        id=model_id,
        name=resolved_name,
        vendor=resolved_vendor,
        color=color_for_key(slug),
        blurb=resolved_blurb,
        provider="openrouter",
        provider_model=slug,
        is_custom=model_id.startswith("or:"),
    )


def get_model(model_id: str) -> ModelCatalogEntry:
    if model_id in MODEL_CATALOG:
        return enrich_entry(MODEL_CATALOG[model_id])
    slug = model_id_to_slug(model_id)
    return entry_from_slug(model_id, slug)


def compute_cost(model_id: str, tokens_input: int, tokens_output: int) -> float:
    return get_pricing_service().compute_cost(model_id, tokens_input, tokens_output)


def resolve_llm_cost(
    model_id: str,
    tokens_input: int,
    tokens_output: int,
    reported_cost_usd: float | None,
) -> float:
    return get_pricing_service().resolve_actual_cost(
        model_id=model_id,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        reported_cost_usd=reported_cost_usd,
    )


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))
