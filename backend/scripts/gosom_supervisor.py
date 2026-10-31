"""Linux-native, container-based replacement for scripts/gosom_supervisor.ps1.

Runs as its OWN long-lived container (``restart: always``) instead of a
Windows Scheduled Task, so automatic recovery needs no host-side cron, no
Task Scheduler, and nobody watching it. It never touches the Docker socket
directly: the API/worker containers must never get Docker socket access (an
unrestricted docker.sock mount is a container-escape risk), and neither does
this one — it only ever talks to:

1. MultiMind's own ``/internal/gosom-supervisor/*`` control endpoints (same
   contract as the PowerShell supervisor: ``/status``, ``/restart/begin``,
   ``/restart/complete``), authenticated with the shared
   ``MAPS_GOSOM_SUPERVISOR_TOKEN``.
2. A small nginx allowlist gateway (``docker/gosom-docker-gateway``) in
   front of a ``docker-socket-proxy`` sidecar. The gateway is the actual
   enforcement point — docker-socket-proxy's own granularity is only
   "resource family + HTTP verb", which can't express "restart only"
   (enabling POST for containers also covers stop/kill/start/pause/
   create), so the gateway forwards exactly GET /containers/json and POST
   /containers/{id}/restart and rejects everything else, including from
   this process if it were ever compromised. The gosom container is
   looked up by its Compose service label rather than a hardcoded name,
   since the container's actual name depends on the Compose project name.
   Both the gateway and docker-socket-proxy live on an isolated Compose
   network that no other service in the stack can reach.

Deliberately stateless: unlike the PowerShell version's local JSON state
file, the cooldown/hourly-restart cap is read entirely from the server's own
``/status`` response (``restarts_in_last_hour``, ``max_restarts_per_hour``),
which is itself backed by the DB-persisted ``GosomHealthState`` row — so this
container can be killed and recreated at any time (or the whole host
rebooted) with zero local state to lose, and the cap still holds correctly
across all of it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gosom_supervisor")


class SupervisorConfig:
    def __init__(self, env: dict[str, str]) -> None:
        self.api_base_url = env.get("GOSOM_SUPERVISOR_API_BASE_URL", "http://api:8000").rstrip("/")
        self.supervisor_token = env.get("MAPS_GOSOM_SUPERVISOR_TOKEN", "")
        self.docker_proxy_url = env.get("GOSOM_SUPERVISOR_DOCKER_PROXY_URL", "").rstrip("/")
        self.compose_service_label = env.get(
            "GOSOM_SUPERVISOR_COMPOSE_SERVICE_LABEL", "com.docker.compose.service=gosom"
        )
        # docker-socket-proxy sees the WHOLE host's Docker daemon, not just
        # this Compose project — confirmed live: filtering by the service
        # label alone matched a different, unrelated project's "gosom"
        # service as well as this one. The project label is required (not
        # optional) specifically so a host running more than one Compose
        # project with a service named "gosom" can never have this
        # supervisor discover or restart the wrong one.
        self.compose_project_label = env.get("GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL", "")
        # The ONLY value this process ever asks gosom-docker-gateway to
        # restart — a fixed, pre-known Compose container name, never
        # whatever find_gosom_container_id() resolved from the Docker API.
        # The gateway independently hardcodes this exact same name and
        # rejects any other path outright (see nginx.conf), so even a fully
        # compromised supervisor cannot influence WHICH container gets
        # restarted — that identity is fixed on both ends at deploy time,
        # not trusted from a runtime lookup. find_gosom_container_id() is
        # still used, but only as a pre-flight "does this container
        # currently exist, uniquely" health check.
        self.container_name = env.get("GOSOM_SUPERVISOR_CONTAINER_NAME", "")
        # Alternative to a fixed name, for platforms that rename containers
        # on every deploy (Coolify: gosom-<resource uuid>-<timestamp>). The
        # gateway allows only names with this same prefix, and the prefix
        # embeds the deployment's unique id, so it also disambiguates this
        # stack's gosom from any other project's on the host.
        self.container_name_prefix = env.get("GOSOM_SUPERVISOR_CONTAINER_NAME_PREFIX", "")
        self.gosom_health_url = env.get(
            "GOSOM_SUPERVISOR_GOSOM_HEALTH_URL", "http://gosom:8080/api/v1/jobs"
        )
        self.poll_interval_seconds = float(env.get("GOSOM_SUPERVISOR_POLL_INTERVAL_SECONDS", "60"))
        self.http_responsive_timeout_seconds = float(
            env.get("GOSOM_SUPERVISOR_HTTP_RESPONSIVE_TIMEOUT_SECONDS", "120")
        )
        self.http_responsive_poll_seconds = float(
            env.get("GOSOM_SUPERVISOR_HTTP_RESPONSIVE_POLL_SECONDS", "3")
        )
        self.request_timeout_seconds = float(env.get("GOSOM_SUPERVISOR_REQUEST_TIMEOUT_SECONDS", "15"))

    def validate(self) -> None:
        if not self.supervisor_token:
            raise RuntimeError("MAPS_GOSOM_SUPERVISOR_TOKEN is required")
        if not self.docker_proxy_url:
            raise RuntimeError("GOSOM_SUPERVISOR_DOCKER_PROXY_URL is required")
        if self.container_name and self.container_name_prefix:
            raise RuntimeError(
                "Set GOSOM_SUPERVISOR_CONTAINER_NAME or GOSOM_SUPERVISOR_CONTAINER_NAME_PREFIX, "
                "not both"
            )
        if self.container_name_prefix:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{7,}", self.container_name_prefix):
                raise RuntimeError(
                    "GOSOM_SUPERVISOR_CONTAINER_NAME_PREFIX must be a specific container-name "
                    "prefix (at least 8 characters of [a-z0-9_.-])"
                )
            return
        if not self.compose_project_label:
            raise RuntimeError(
                "GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL is required — without it, container "
                "discovery is ambiguous on any host running more than one Compose project "
                "with a service named 'gosom'"
            )
        if not self.container_name:
            raise RuntimeError(
                "GOSOM_SUPERVISOR_CONTAINER_NAME is required — it is the only container "
                "identity gosom-docker-gateway will ever restart; must match the gateway's "
                "own hardcoded allowlist exactly (see docker/gosom-docker-gateway/nginx.conf)"
            )


class RestartAlreadyInProgress(Exception):
    """The server reports a 409 on /restart/begin — another caller (or a
    concurrent tick of this same supervisor, which should never happen with
    a single-replica container, but is handled safely anyway) already owns
    the in-flight restart."""


async def get_status(client: httpx.AsyncClient, config: SupervisorConfig) -> dict:
    response = await client.get(
        f"{config.api_base_url}/api/v1/maps/internal/gosom-supervisor/status",
        headers={"X-Supervisor-Token": config.supervisor_token},
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


async def begin_restart(client: httpx.AsyncClient, config: SupervisorConfig, *, reason: str) -> None:
    response = await client.post(
        f"{config.api_base_url}/api/v1/maps/internal/gosom-supervisor/restart/begin",
        headers={"X-Supervisor-Token": config.supervisor_token},
        json={"reason": reason},
        timeout=config.request_timeout_seconds,
    )
    if response.status_code == 409:
        raise RestartAlreadyInProgress()
    response.raise_for_status()


async def complete_restart(
    client: httpx.AsyncClient, config: SupervisorConfig, *, http_responsive: bool
) -> dict:
    response = await client.post(
        f"{config.api_base_url}/api/v1/maps/internal/gosom-supervisor/restart/complete",
        headers={"X-Supervisor-Token": config.supervisor_token},
        json={"http_responsive": http_responsive},
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


async def find_gosom_container_id(client: httpx.AsyncClient, config: SupervisorConfig) -> str | None:
    """Looks the container up by its Compose service AND project labels
    together — never the service label alone. docker-socket-proxy (and the
    Docker Engine behind it) sees every container on the host, not just
    this Compose project's, so the service label by itself is ambiguous on
    any host running more than one project with a service named "gosom"
    (confirmed against a real multi-project host, not theoretical). A
    hardcoded container NAME is avoided too, since that depends on the
    Compose project name/separator, which differs between stacks and
    versions.

    Returns None — never an arbitrary pick — on zero OR on more than one
    match; the caller treats both as a failed restart attempt. A silent
    "first match wins" is exactly how this would restart the wrong
    container the one time two hosts/projects briefly collide.
    """
    containers = await _matching_containers(client, config)
    if not isinstance(containers, list) or len(containers) != 1:
        logger.error(
            "gosom_supervisor_container_lookup_ambiguous matches=%s service_label=%s project_label=%s",
            len(containers) if isinstance(containers, list) else "invalid_response",
            config.compose_service_label,
            config.compose_project_label,
        )
        return None
    return containers[0].get("Id")


def _container_names(container: dict) -> list[str]:
    return [str(name).lstrip("/") for name in container.get("Names") or []]


async def _matching_containers(client: httpx.AsyncClient, config: SupervisorConfig) -> list | object:
    """Containers with this stack's gosom labels (and, in prefix mode, a
    name carrying this deployment's prefix)."""
    labels = [config.compose_service_label]
    if config.compose_project_label:
        labels.append(config.compose_project_label)
    response = await client.get(
        f"{config.docker_proxy_url}/containers/json",
        params={"filters": json.dumps({"label": labels})},
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    containers = response.json()
    if config.container_name_prefix and isinstance(containers, list):
        containers = [
            container
            for container in containers
            if any(name.startswith(config.container_name_prefix) for name in _container_names(container))
        ]
    return containers


async def find_restart_target(client: httpx.AsyncClient, config: SupervisorConfig) -> str | None:
    """The container name to restart, or None when it is missing or
    ambiguous. A fixed name is only ever confirmed, never replaced; in
    prefix mode the single matching container's own prefixed name is used
    (the gateway rejects any name without that prefix)."""
    if config.container_name:
        if await find_gosom_container_id(client, config) is None:
            return None
        return config.container_name
    containers = await _matching_containers(client, config)
    if not isinstance(containers, list) or len(containers) != 1:
        logger.error(
            "gosom_supervisor_container_lookup_ambiguous matches=%s prefix=%s",
            len(containers) if isinstance(containers, list) else "invalid_response",
            config.container_name_prefix,
        )
        return None
    names = [
        name
        for name in _container_names(containers[0])
        if name.startswith(config.container_name_prefix)
    ]
    return names[0] if names else None


async def restart_container(
    client: httpx.AsyncClient, config: SupervisorConfig, container_name: str | None = None
) -> None:
    """Restarts ``container_name`` (from find_restart_target) or the fixed
    ``config.container_name``. Either way the gateway independently allows
    only the configured name or prefix, so a compromised supervisor still
    cannot restart any other container.
    """
    target = container_name or config.container_name
    response = await client.post(
        f"{config.docker_proxy_url}/containers/{target}/restart",
        params={"t": "10"},
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()


async def wait_gosom_http_responsive(client: httpx.AsyncClient, config: SupervisorConfig) -> bool:
    """Confirms ONLY that Gosom's own REST API answers again — NOT that its
    dispatcher is actually scraping. That distinction matters: it is
    reported to the server as ``http_responsive``, never as a general
    "success". Genuine scraping-progress verification happens server-side,
    independently, from a real discovery job completing
    (gosom_health_monitor.record_job_success)."""
    deadline = asyncio.get_running_loop().time() + config.http_responsive_timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            response = await client.get(
                config.gosom_health_url, timeout=config.request_timeout_seconds
            )
            if 200 <= response.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        await asyncio.sleep(config.http_responsive_poll_seconds)
    return False


async def run_once(client: httpx.AsyncClient, config: SupervisorConfig) -> dict:
    """One supervisor decision cycle. Returns a dict describing what
    happened, for logging and for deterministic offline testing — never
    raises on an expected/handled condition (not frozen, exhausted, capped,
    already-in-progress); only on a genuinely unexpected HTTP/transport
    error from one of the calls it does decide to make.
    """
    status = await get_status(client, config)
    state = status.get("state") or {}

    if state.get("restart_exhausted"):
        logger.error(
            "gosom_supervisor_stopped restart_exhausted reason=%s",
            state.get("restart_exhausted_reason"),
        )
        return {"action": "skipped", "reason": "restart_exhausted"}

    if not status.get("frozen"):
        logger.info("gosom_supervisor_not_frozen reason=%s", status.get("reason"))
        return {"action": "skipped", "reason": "not_frozen"}

    restarts_in_last_hour = int(status.get("restarts_in_last_hour") or 0)
    max_restarts_per_hour = int(status.get("max_restarts_per_hour") or 3)
    if restarts_in_last_hour >= max_restarts_per_hour:
        logger.error(
            "gosom_supervisor_blocked_hourly_cap restarts_in_last_hour=%s max=%s",
            restarts_in_last_hour,
            max_restarts_per_hour,
        )
        return {"action": "skipped", "reason": "hourly_cap"}

    reason = f"freeze detected: {status.get('reason')}"
    logger.warning("gosom_supervisor_freeze_confirmed reason=%s — restarting gosom", reason)

    try:
        await begin_restart(client, config, reason=reason)
    except RestartAlreadyInProgress:
        logger.info("gosom_supervisor_restart_already_in_progress")
        return {"action": "skipped", "reason": "restart_already_in_progress"}

    try:
        # Pre-flight health check only — exists so "container renamed/
        # missing/ambiguous" is reported distinctly, never to decide WHICH
        # container gets restarted. See restart_container()'s docstring.
        target = await find_restart_target(client, config)
        if target is None:
            logger.error("gosom_supervisor_container_not_found label=%s", config.compose_service_label)
            await complete_restart(client, config, http_responsive=False)
            return {"action": "restart_attempted", "http_responsive": False, "reason": "container_not_found"}

        await restart_container(client, config, target)
        responsive = await wait_gosom_http_responsive(client, config)
        result = await complete_restart(client, config, http_responsive=responsive)
        if responsive:
            logger.warning("gosom_supervisor_restart_responsive container_name=%s", target)
        else:
            logger.error("gosom_supervisor_restart_not_responsive container_name=%s", target)
        return {
            "action": "restart_attempted",
            "http_responsive": responsive,
            "restart_exhausted": bool((result.get("state") or {}).get("restart_exhausted")),
        }
    except Exception:
        logger.exception("gosom_supervisor_restart_error")
        try:
            await complete_restart(client, config, http_responsive=False)
        except Exception:
            logger.exception("gosom_supervisor_restart_complete_also_failed")
        raise


async def main() -> None:
    config = SupervisorConfig(os.environ)
    config.validate()
    logger.info(
        "gosom_supervisor_starting poll_interval_seconds=%s api_base_url=%s",
        config.poll_interval_seconds,
        config.api_base_url,
    )
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await run_once(client, config)
            except Exception:
                logger.exception("gosom_supervisor_cycle_failed")
            await asyncio.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
