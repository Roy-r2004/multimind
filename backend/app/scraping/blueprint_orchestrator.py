"""Blueprint planning orchestrator for Scraping Council."""

import json
from typing import Any

from app.core.exceptions import AppError
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.schemas.api import ScrapingBlueprintContent


PLANNING_ROLES = [
    ("Mission Interpreter", "scraping/mission_interpreter.j2"),
    ("Country and Culture Researcher", "scraping/country_researcher.j2"),
    ("Source Strategy Expert", "scraping/source_strategist.j2"),
    ("Regulation and Compliance Expert", "scraping/regulation_reviewer.j2"),
    ("Data Schema and Verification Expert", "scraping/schema_verifier.j2"),
]


class BlueprintGenerationError(AppError):
    def __init__(self, message: str = "Blueprint generation failed") -> None:
        super().__init__(message, code="BLUEPRINT_GENERATION_FAILED")


class BlueprintOrchestrator:
    def __init__(self) -> None:
        self._prompts = get_prompt_engine()
        self._providers = get_provider_registry()

    async def generate(
        self,
        mission: Any,
        model_set: Any,
        previous_blueprint: dict[str, Any] | None = None,
        change_instructions: str | None = None,
    ) -> ScrapingBlueprintContent:
        council_models = list(model_set.models or [])
        if not council_models:
            raise BlueprintGenerationError("Selected model set has no council models")

        analyses: list[dict[str, str]] = []
        for index, (role, template) in enumerate(PLANNING_ROLES):
            model_id = council_models[index % len(council_models)]
            model = get_model(model_id)
            prompt = self._prompts.render(
                template,
                role=role,
                mission_title=mission.title,
                mission_prompt=mission.original_prompt,
                previous_blueprint=previous_blueprint,
                change_instructions=change_instructions,
            )
            provider = self._providers.get_provider(model.provider)
            response = await provider.complete(
                system=prompt,
                user="Return planning analysis for the Blueprint Judge.",
                model=model.provider_model,
                max_tokens=4096,
            )
            analyses.append({"role": role, "model_id": model_id, "analysis": response.text})

        judge_model_id = model_set.verdict_model or council_models[0]
        judge_model = get_model(judge_model_id)
        judge_prompt = self._prompts.render(
            "scraping/blueprint_judge.j2",
            mission_title=mission.title,
            mission_prompt=mission.original_prompt,
            analyses=analyses,
            previous_blueprint=previous_blueprint,
            change_instructions=change_instructions,
            required_json_structure=self.required_json_structure(),
        )
        provider = self._providers.get_provider(judge_model.provider)
        response = await provider.complete(
            system=judge_prompt,
            user="Return only the final JSON object.",
            model=judge_model.provider_model,
            max_tokens=8192,
        )
        return await self._parse_validate_or_repair(
            provider=provider,
            model=judge_model.provider_model,
            invalid_output=response.text,
        )

    async def _parse_validate_or_repair(
        self,
        *,
        provider: Any,
        model: str,
        invalid_output: str,
    ) -> ScrapingBlueprintContent:
        try:
            return self._parse_and_validate(invalid_output)
        except Exception as exc:
            validation_summary = str(exc)

        repair_system = self._prompts.render(
            "scraping/blueprint_judge.j2",
            mission_title="Repair invalid blueprint JSON",
            mission_prompt="Repair the provided invalid output. Do not add Markdown.",
            analyses=[],
            previous_blueprint=None,
            change_instructions=(
                "Repair this invalid output so it matches the required JSON structure. "
                "Do not silently omit required sections."
            ),
            required_json_structure=self.required_json_structure(),
        )
        repair_user = (
            "Invalid output:\n"
            f"{invalid_output}\n\n"
            "Validation error summary:\n"
            f"{validation_summary}\n\n"
            "Required JSON structure:\n"
            f"{json.dumps(self.required_json_structure(), indent=2)}"
        )
        repair_response = await provider.complete(
            system=repair_system,
            user=repair_user,
            model=model,
            max_tokens=8192,
        )
        try:
            return self._parse_and_validate(repair_response.text)
        except Exception as exc:
            raise BlueprintGenerationError("Blueprint JSON could not be repaired") from exc

    def _parse_and_validate(self, text: str) -> ScrapingBlueprintContent:
        data = json.loads(self._strip_json_fence(text))
        return ScrapingBlueprintContent.model_validate(data)

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```json") and stripped.endswith("```"):
            return stripped.removeprefix("```json").removesuffix("```").strip()
        return stripped

    @staticmethod
    def required_json_structure() -> dict[str, Any]:
        return {
            "mission_summary": {
                "goal": "string",
                "target_entities": ["string"],
                "deliverables": ["string"],
            },
            "scope": {
                "included": ["string"],
                "excluded": ["string"],
                "countries": ["string"],
                "regions": ["string"],
            },
            "languages": ["string"],
            "search_terms": [{"language": "string", "term": "string", "purpose": "string"}],
            "source_strategy": [
                {
                    "source_type": "string",
                    "priority": 1,
                    "trust_tier": "string",
                    "purpose": "string",
                    "required": True,
                }
            ],
            "data_schema": [
                {"field_name": "string", "description": "string", "required": True}
            ],
            "classification_rules": ["string"],
            "verification_rules": ["string"],
            "deduplication_rules": ["string"],
            "compliance_rules": ["string"],
            "task_plan": [{"order": 1, "task": "string", "assigned_role": "string"}],
            "stop_conditions": ["string"],
            "estimated_workload": {
                "expected_queries": None,
                "expected_pages": None,
                "expected_ai_calls": None,
                "estimated_cost_usd": None,
                "notes": ["string"],
            },
            "agent_assignments": [
                {"role": "string", "responsibility": "string", "model_id": "string"}
            ],
        }


_orchestrator: BlueprintOrchestrator | None = None


def get_blueprint_orchestrator() -> BlueprintOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = BlueprintOrchestrator()
    return _orchestrator
