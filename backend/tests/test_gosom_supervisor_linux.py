"""Offline tests for scripts/gosom_supervisor.py — the Linux-native,
container-based replacement for the Windows gosom_supervisor.ps1 script.

Everything here runs against a fully mocked HTTP transport (httpx.
MockTransport). No Docker socket, no real container, no network call ever
leaves the process — this verifies the SUPERVISOR'S OWN decision logic
(when to restart, when to skip, how it reports outcomes), not Docker itself.
"""

from __future__ import annotations

import json

import httpx
import pytest

from scripts.gosom_supervisor import (
    RestartAlreadyInProgress,
    SupervisorConfig,
    find_gosom_container_id,
    restart_container,
    run_once,
)


def _config(**overrides) -> SupervisorConfig:
    env = {
        "MAPS_GOSOM_SUPERVISOR_TOKEN": "secret-token",
        "GOSOM_SUPERVISOR_API_BASE_URL": "http://api:8000",
        "GOSOM_SUPERVISOR_DOCKER_PROXY_URL": "http://docker-socket-proxy:2375",
        "GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL": "com.docker.compose.project=multimind-gosom-census",
        "GOSOM_SUPERVISOR_CONTAINER_NAME": "multimind-gosom-census-gosom-1",
        "GOSOM_SUPERVISOR_GOSOM_HEALTH_URL": "http://gosom:8080/api/v1/jobs",
        "GOSOM_SUPERVISOR_HTTP_RESPONSIVE_POLL_SECONDS": "0",
        "GOSOM_SUPERVISOR_HTTP_RESPONSIVE_TIMEOUT_SECONDS": "0.05",
        **overrides,
    }
    return SupervisorConfig(env)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_not_frozen_takes_no_action():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["x-supervisor-token"] == "secret-token"
        return httpx.Response(200, json={"frozen": False, "reason": "no_outstanding_work", "state": {}})

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome == {"action": "skipped", "reason": "not_frozen"}
    assert calls == ["GET /api/v1/maps/internal/gosom-supervisor/status"]


@pytest.mark.asyncio
async def test_restart_exhausted_takes_no_action():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "frozen": True,
                "reason": "freeze",
                "state": {"restart_exhausted": True, "restart_exhausted_reason": "too many failures"},
            },
        )

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome == {"action": "skipped", "reason": "restart_exhausted"}


@pytest.mark.asyncio
async def test_hourly_cap_blocks_restart_even_when_frozen():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "frozen": True,
                "reason": "freeze",
                "state": {},
                "restarts_in_last_hour": 3,
                "max_restarts_per_hour": 3,
            },
        )

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome == {"action": "skipped", "reason": "hourly_cap"}


@pytest.mark.asyncio
async def test_concurrent_restart_in_progress_is_handled_not_raised():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200,
                json={"frozen": True, "reason": "freeze", "state": {}, "restarts_in_last_hour": 0, "max_restarts_per_hour": 3},
            )
        if request.url.path.endswith("/restart/begin"):
            return httpx.Response(409, json={"detail": "already in progress"})
        raise AssertionError(f"unexpected call: {request.url.path}")

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome == {"action": "skipped", "reason": "restart_already_in_progress"}
    # Never reaches the Docker proxy at all once begin/restart says no.
    assert "GET /containers/json" not in calls


@pytest.mark.asyncio
async def test_full_restart_cycle_finds_container_restarts_and_reports_responsive():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        path = request.url.path
        if path.endswith("/status"):
            return httpx.Response(
                200,
                json={
                    "frozen": True,
                    "reason": "consecutive_timeouts=3",
                    "state": {},
                    "restarts_in_last_hour": 0,
                    "max_restarts_per_hour": 3,
                },
            )
        if path.endswith("/restart/begin"):
            assert json.loads(request.content)["reason"]
            return httpx.Response(200, json={"started": True})
        if path == "/containers/json":
            filters = json.loads(request.url.params["filters"])
            assert filters == {
                "label": [
                    "com.docker.compose.service=gosom",
                    "com.docker.compose.project=multimind-gosom-census",
                ]
            }
            # Discovery resolves a DIFFERENT id than the fixed restart
            # target below — proving the restart call ignores whatever
            # this lookup returns and always targets the pinned name.
            return httpx.Response(200, json=[{"Id": "container-abc123"}])
        if path == "/containers/multimind-gosom-census-gosom-1/restart":
            assert request.method == "POST"
            return httpx.Response(204)
        if path.endswith("/api/v1/jobs"):
            # Gosom's own health endpoint, polled after the restart.
            return httpx.Response(200, json=[])
        if path.endswith("/restart/complete"):
            body = json.loads(request.content)
            assert body == {"http_responsive": True}
            return httpx.Response(200, json={"state": {"restart_exhausted": False}})
        raise AssertionError(f"unexpected call: {path}")

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome == {"action": "restart_attempted", "http_responsive": True, "restart_exhausted": False}
    restart_calls = [
        c for c in calls if "/containers/multimind-gosom-census-gosom-1/restart" in c[1]
    ]
    assert len(restart_calls) == 1
    assert not any("container-abc123" in c[1] and "restart" in c[1] for c in calls)


@pytest.mark.asyncio
async def test_restart_without_gosom_becoming_responsive_reports_false():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/status"):
            return httpx.Response(
                200,
                json={"frozen": True, "reason": "freeze", "state": {}, "restarts_in_last_hour": 0, "max_restarts_per_hour": 3},
            )
        if path.endswith("/restart/begin"):
            return httpx.Response(200, json={"started": True})
        if path == "/containers/json":
            return httpx.Response(200, json=[{"Id": "container-xyz"}])
        if path == "/containers/multimind-gosom-census-gosom-1/restart":
            return httpx.Response(204)
        if path.endswith("/api/v1/jobs"):
            # Gosom never comes back up.
            raise httpx.ConnectError("connection refused")
        if path.endswith("/restart/complete"):
            body = json.loads(request.content)
            assert body == {"http_responsive": False}
            return httpx.Response(200, json={"state": {"restart_exhausted": False}})
        raise AssertionError(f"unexpected call: {path}")

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome == {"action": "restart_attempted", "http_responsive": False, "restart_exhausted": False}


@pytest.mark.asyncio
async def test_container_not_found_reports_failure_without_crashing():
    """If the label match ever finds nothing (e.g. the service was renamed),
    this must report a failed restart attempt (so restart_exhausted
    eventually kicks in and alerts an operator) rather than silently doing
    nothing or crashing the supervisor loop."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/status"):
            return httpx.Response(
                200,
                json={"frozen": True, "reason": "freeze", "state": {}, "restarts_in_last_hour": 0, "max_restarts_per_hour": 3},
            )
        if path.endswith("/restart/begin"):
            return httpx.Response(200, json={"started": True})
        if path == "/containers/json":
            return httpx.Response(200, json=[])
        if path.endswith("/restart/complete"):
            body = json.loads(request.content)
            assert body == {"http_responsive": False}
            return httpx.Response(200, json={"state": {}})
        raise AssertionError(f"unexpected call: {path}")

    async with _client(handler) as client:
        outcome = await run_once(client, _config())

    assert outcome["action"] == "restart_attempted"
    assert outcome["http_responsive"] is False


@pytest.mark.asyncio
async def test_find_gosom_container_id_returns_none_when_no_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        result = await find_gosom_container_id(client, _config())

    assert result is None


@pytest.mark.asyncio
async def test_find_gosom_container_id_returns_none_when_ambiguous():
    """Root-cause regression, confirmed against a real multi-project host:
    docker-socket-proxy sees every container on the host, so a service
    label alone can match more than one Compose project's own "gosom"
    service. Two matches must never be resolved by picking the first one —
    that is exactly how the wrong container would get restarted."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"Id": "container-project-a"}, {"Id": "container-project-b"}]
        )

    async with _client(handler) as client:
        result = await find_gosom_container_id(client, _config())

    assert result is None


def test_config_validation_requires_token_proxy_url_project_label_and_container_name():
    with pytest.raises(RuntimeError, match="MAPS_GOSOM_SUPERVISOR_TOKEN"):
        SupervisorConfig({}).validate()
    with pytest.raises(RuntimeError, match="GOSOM_SUPERVISOR_DOCKER_PROXY_URL"):
        SupervisorConfig({"MAPS_GOSOM_SUPERVISOR_TOKEN": "x"}).validate()
    with pytest.raises(RuntimeError, match="GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL"):
        SupervisorConfig(
            {"MAPS_GOSOM_SUPERVISOR_TOKEN": "x", "GOSOM_SUPERVISOR_DOCKER_PROXY_URL": "http://proxy"}
        ).validate()
    with pytest.raises(RuntimeError, match="GOSOM_SUPERVISOR_CONTAINER_NAME"):
        SupervisorConfig(
            {
                "MAPS_GOSOM_SUPERVISOR_TOKEN": "x",
                "GOSOM_SUPERVISOR_DOCKER_PROXY_URL": "http://proxy",
                "GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL": "com.docker.compose.project=x",
            }
        ).validate()
    SupervisorConfig(
        {
            "MAPS_GOSOM_SUPERVISOR_TOKEN": "x",
            "GOSOM_SUPERVISOR_DOCKER_PROXY_URL": "http://proxy",
            "GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL": "com.docker.compose.project=x",
            "GOSOM_SUPERVISOR_CONTAINER_NAME": "x-gosom-1",
        }
    ).validate()


@pytest.mark.asyncio
async def test_restart_container_always_targets_the_configured_fixed_name():
    """Direct unit coverage: restart_container() takes no id parameter at
    all anymore — it can only ever post to config.container_name."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(204)

    async with _client(handler) as client:
        await restart_container(client, _config(GOSOM_SUPERVISOR_CONTAINER_NAME="pinned-name"))

    assert len(calls) == 1
    assert "/containers/pinned-name/restart" in calls[0]


def test_restart_already_in_progress_is_a_distinct_exception_type():
    assert issubclass(RestartAlreadyInProgress, Exception)


# --- Prefix mode: platforms (Coolify) that rename containers on every
# deploy. The prefix embeds the deployment's unique id. ---

PREFIX = "gosom-fqhz20ulcg5huwiewn044kks-"


def _prefix_config(**overrides) -> SupervisorConfig:
    return _config(
        **{
            "GOSOM_SUPERVISOR_CONTAINER_NAME": "",
            "GOSOM_SUPERVISOR_COMPOSE_PROJECT_LABEL": "",
            "GOSOM_SUPERVISOR_CONTAINER_NAME_PREFIX": PREFIX,
            **overrides,
        }
    )


def test_prefix_mode_validates_without_fixed_name_or_project_label():
    _prefix_config().validate()


def test_prefix_mode_rejects_a_vague_prefix_and_both_modes_at_once():
    with pytest.raises(RuntimeError, match="PREFIX"):
        _prefix_config(GOSOM_SUPERVISOR_CONTAINER_NAME_PREFIX="gosom").validate()
    with pytest.raises(RuntimeError, match="not both"):
        _config(GOSOM_SUPERVISOR_CONTAINER_NAME_PREFIX=PREFIX).validate()


@pytest.mark.asyncio
async def test_prefix_mode_restarts_only_this_deployments_gosom():
    """Another project's gosom on the same host shares the service label but
    not the prefix; it must be ignored, and the restart must target this
    deployment's current (timestamped) container name."""
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/containers/json":
            return httpx.Response(
                200,
                json=[
                    {"Id": "ours", "Names": [f"/{PREFIX}20261030T191944"]},
                    {"Id": "theirs", "Names": ["/multimind-gosom-census-gosom-1"]},
                ],
            )
        posts.append(request.url.path)
        return httpx.Response(204)

    from scripts.gosom_supervisor import find_restart_target

    config = _prefix_config()
    async with _client(handler) as client:
        target = await find_restart_target(client, config)
        await restart_container(client, config, target)

    assert target == f"{PREFIX}20261030T191944"
    assert posts == [f"/containers/{PREFIX}20261030T191944/restart"]


@pytest.mark.asyncio
async def test_prefix_mode_refuses_two_of_our_containers():
    """During a deploy the old and new gosom can briefly coexist; never pick
    one of them."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"Id": "old", "Names": [f"/{PREFIX}20261030T190927"]},
                {"Id": "new", "Names": [f"/{PREFIX}20261030T191944"]},
            ],
        )

    from scripts.gosom_supervisor import find_restart_target

    async with _client(handler) as client:
        assert await find_restart_target(client, _prefix_config()) is None
