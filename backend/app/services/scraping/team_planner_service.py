"""AI team planner for approved Scraping Council blueprints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationError
from app.db.models import ModelSet, ScrapingBlueprint, ScrapingMission
from app.llm.catalog import get_model
from app.llm.providers import LLMProvider, get_provider_registry
from app.schemas.api import ScrapingTeamPlanOutput

MIN_SCRAPING_TEAM_AGENTS = 2
MAX_SCRAPING_TEAM_AGENTS = 12


class TeamPlannerService:
    async def plan_team(
        self,
        mission: ScrapingMission,
        blueprint: ScrapingBlueprint,
        model_set: ModelSet,
    ) -> tuple[ScrapingTeamPlanOutput, str]:
        planner_model_id = self.planner_model_id(model_set)
        model = get_model(planner_model_id)
        provider = get_provider_registry().get_provider(model.provider)
        allowed_model_ids = list(dict.fromkeys(model_set.models))
        response = await provider.complete(
            system=self._system_prompt(),
            user=self._user_prompt(
                mission=mission,
                blueprint=blueprint,
                model_set=model_set,
                planner_model_id=planner_model_id,
                allowed_model_ids=allowed_model_ids,
            ),
            model=model.provider_model,
            max_tokens=5000,
        )
        return (
            await self.parse_validate_or_repair(
                provider=provider,
                provider_model=model.provider_model,
                raw_text=response.text,
                allowed_model_ids=allowed_model_ids,
            ),
            planner_model_id,
        )

    def planner_model_id(self, model_set: ModelSet) -> str:
        return model_set.verdict_model or model_set.models[0]

    async def parse_validate_or_repair(
        self,
        *,
        provider: LLMProvider,
        provider_model: str,
        raw_text: str,
        allowed_model_ids: list[str],
    ) -> ScrapingTeamPlanOutput:
        try:
            return self.validate_raw_plan(raw_text, allowed_model_ids=allowed_model_ids)
        except Exception as exc:
            validation_problem = self._safe_validation_problem(exc)

        repair_response = await provider.complete(
            system=self._repair_system_prompt(),
            user=(
                "The previous AI scraping team plan was invalid.\n\n"
                f"Validation problem:\n{validation_problem}\n\n"
                "Return only valid JSON that matches the required schema. Do not include markdown."
            ),
            model=provider_model,
            max_tokens=5000,
        )
        return self.validate_raw_plan(repair_response.text, allowed_model_ids=allowed_model_ids)

    def validate_raw_plan(
        self, raw_text: str, *, allowed_model_ids: Iterable[str]
    ) -> ScrapingTeamPlanOutput:
        try:
            data = LLMProvider.parse_json_response(raw_text)
        except Exception as exc:
            raise ValidationError("Planner output was not valid JSON") from exc
        return self.validate_plan_data(data, allowed_model_ids=allowed_model_ids)

    def validate_plan_data(
        self, data: dict[str, Any], *, allowed_model_ids: Iterable[str]
    ) -> ScrapingTeamPlanOutput:
        try:
            plan = ScrapingTeamPlanOutput.model_validate(data)
        except PydanticValidationError as exc:
            raise ValidationError(self._schema_validation_message(exc)) from exc

        allowed = set(allowed_model_ids)
        if not allowed:
            raise ValidationError("Model set has no available agent models")
        if plan.recommended_agent_count < MIN_SCRAPING_TEAM_AGENTS:
            raise ValidationError("Planner selected too few agents")
        if plan.recommended_agent_count > MAX_SCRAPING_TEAM_AGENTS:
            raise ValidationError("Planner selected too many agents")

        sequences = [agent.sequence for agent in plan.agents]
        if len(sequences) != len(set(sequences)):
            raise ValidationError("Planner output contains duplicate agent sequences")
        sequence_set = set(sequences)

        for agent in plan.agents:
            if agent.model_id not in allowed:
                raise ValidationError("Planner assigned a model outside the selected model set")
            if agent.sequence in agent.depends_on:
                raise ValidationError("Planner output contains a self-dependency")
            missing = [
                dependency for dependency in agent.depends_on if dependency not in sequence_set
            ]
            if missing:
                raise ValidationError("Planner output contains an unknown dependency")

        if self._has_dependency_cycle({agent.sequence: agent.depends_on for agent in plan.agents}):
            raise ValidationError("Planner output contains a dependency cycle")

        return plan

    def _has_dependency_cycle(self, graph: dict[int, list[int]]) -> bool:
        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(node: int) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dependency in graph.get(node, []):
                if visit(dependency):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph)

    def _system_prompt(self) -> str:
        return (
            "You are the AI execution orchestrator for Scraping Council. "
            "You only plan an AI scraping team. No websites have been visited, no records have "
            "been found, and no scraping has been completed. Return strict JSON only."
        )

    def _user_prompt(
        self,
        *,
        mission: ScrapingMission,
        blueprint: ScrapingBlueprint,
        model_set: ModelSet,
        planner_model_id: str,
        allowed_model_ids: list[str],
    ) -> str:
        return (
            "Plan an autonomous AI scraping team for the approved blueprint.\n\n"
            f"Mission title: {mission.title}\n"
            f"Mission prompt: {mission.original_prompt}\n"
            f"Blueprint version: {blueprint.version}\n"
            f"Approved structured blueprint JSON: {blueprint.blueprint_json}\n"
            f"Model set: {model_set.name} ({model_set.slug})\n"
            f"Planner model: {planner_model_id}\n"
            f"Available agent model IDs: {allowed_model_ids}\n"
            f"Minimum agents: {MIN_SCRAPING_TEAM_AGENTS}\n"
            f"Maximum agents: {MAX_SCRAPING_TEAM_AGENTS}\n\n"
            "Choose the team size dynamically inside the allowed range. Do not always choose five. "
            "Consider countries, languages, geographic scope, source categories, expected "
            "workload, verification, deduplication, compliance complexity, regional "
            "specialization, language specialization, extraction needs, and independent "
            "review.\n\n"
            "Every planned member must be an AI scraping agent or AI oversight agent. Include at "
            "least one quality-control, verification, or judge role. Include deduplication "
            "responsibility somewhere in the team. Avoid assigning several agents the exact same "
            "scope. Respect the approved blueprint. Only use available model IDs. Do not "
            "produce fake URLs, fake statistics, fake records, or claims that scraping already "
            "happened.\n\n"
            "Return JSON shaped exactly like:\n"
            "{\n"
            '  "recommended_agent_count": 3,\n'
            '  "rationale": "Why this number and mix of agents is appropriate.",\n'
            '  "agents": [\n'
            "    {\n"
            '      "sequence": 1,\n'
            '      "name": "Arabic Source Discovery Agent",\n'
            '      "role": "source_discovery",\n'
            '      "purpose": "Find Arabic-language sources relevant to the mission.",\n'
            '      "instructions": "Plan source discovery without claiming browsing occurred.",\n'
            '      "assigned_scope": {"languages": ["ar"], "regions": ["Lebanon"]},\n'
            '      "model_id": "one-of-the-available-model-ids",\n'
            '      "depends_on": []\n'
            "    }\n"
            "  ]\n"
            "}"
        )

    def _repair_system_prompt(self) -> str:
        return (
            "Repair malformed AI scraping team plan JSON. Return valid JSON only. "
            "Do not include markdown, commentary, secrets, or provider metadata."
        )

    def _safe_validation_problem(self, exc: Exception) -> str:
        message = str(exc)
        if len(message) > 1200:
            return message[:1200] + "..."
        return message

    def _schema_validation_message(self, exc: PydanticValidationError) -> str:
        errors = exc.errors()
        if not errors:
            return "Planner output schema validation failed"
        messages: list[str] = []
        for error in errors[:3]:
            location = ".".join(str(part) for part in error.get("loc", ()))
            detail = str(error.get("msg", "Invalid value"))
            if location:
                messages.append(f"{location}: {detail}")
            else:
                messages.append(detail)
        return "Planner output schema validation failed: " + "; ".join(messages)


team_planner_service = TeamPlannerService()
