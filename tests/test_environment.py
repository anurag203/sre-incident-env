import json

from fastapi.testclient import TestClient
from openenv.core.env_server.mcp_types import CallToolAction, ListToolsAction

from server.app import app
from server.incident_environment import IncidentEnvironment


def _step(env: IncidentEnvironment, tool_name: str, **arguments):
    return env.step(CallToolAction(tool_name=tool_name, arguments=arguments))


def _result_dict(observation) -> dict:
    return json.loads(observation.result)


def test_list_tools_does_not_consume_step_budget():
    env = IncidentEnvironment()
    env.reset(task_id="easy")

    obs = env.step(ListToolsAction())

    assert env.state.step_count == 0
    assert type(obs).__name__ == "ListToolsObservation"


def test_easy_correct_restart_is_not_penalized():
    env = IncidentEnvironment()
    env.reset(task_id="easy")

    _step(env, "check_logs", service_name="order-service")
    _step(env, "restart_service", service_name="order-service")
    terminal = _step(
        env,
        "resolve_incident",
        root_cause="order-service",
        summary="OOM crash fixed with restart",
    )

    result = _result_dict(terminal)

    assert terminal.done is True
    assert result["score"] == 0.8
    assert result["breakdown"]["penalties"] == 0.0


def test_medium_db_actions_reward_and_restore_the_stack():
    env = IncidentEnvironment()
    env.reset(task_id="medium")

    first = _step(env, "check_logs", service_name="db-primary")
    second = _step(env, "check_metrics", service_name="db-primary", metric="latency")
    kill = _step(env, "run_db_query", query="KILL QUERY 4281")
    create = _step(
        env,
        "run_db_query",
        query="CREATE INDEX idx_orders_user_created ON orders(user_id, created_at)",
    )
    terminal = _step(
        env,
        "resolve_incident",
        root_cause="db-primary",
        summary="Slow unindexed query on db-primary caused the cascade",
    )

    assert first.reward == 0.05
    assert second.reward == 0.05
    assert kill.reward == 0.05
    assert create.reward == 0.05

    services = env._episode_state["services"]
    assert services["db-primary"]["status"] == "healthy"
    assert services["user-service"]["status"] == "healthy"
    assert services["payment-service"]["status"] == "healthy"
    assert services["api-gateway"]["status"] == "healthy"

    result = _result_dict(terminal)
    assert result["score"] == 0.82
    assert result["breakdown"]["penalties"] == 0.0


def test_hard_perfect_path_scores_without_false_penalties():
    env = IncidentEnvironment()
    env.reset(task_id="hard")

    _step(env, "check_logs", service_name="api-gateway")
    _step(env, "check_logs", service_name="user-service")
    _step(env, "check_logs", service_name="auth-service")
    _step(env, "check_dependencies", service_name="auth-service")
    _step(env, "check_alert_details")
    _step(env, "rollback_deploy", service_name="auth-service")
    _step(env, "restart_service", service_name="user-service")
    _step(env, "restart_service", service_name="order-service")
    _step(env, "restart_service", service_name="payment-service")
    terminal = _step(
        env,
        "resolve_incident",
        root_cause="auth-service",
        summary="Bad auth deploy rolled back and dependent services restarted",
    )

    result = _result_dict(terminal)

    assert result["score"] == 0.92
    assert result["breakdown"]["penalties"] == 0.0
    assert env._episode_state["services"]["api-gateway"]["status"] == "healthy"
    assert env._episode_state["services"]["notification-service"]["status"] == "healthy"


def test_state_exposes_rich_progress_fields():
    env = IncidentEnvironment()
    env.reset(task_id="medium")

    _step(env, "check_logs", service_name="db-primary")
    _step(env, "check_metrics", service_name="db-primary", metric="latency")

    state = env.state

    assert type(state).__name__ == "SREState"
    assert state.task_id == "medium"
    assert state.task_title == "Database Slow Query Causing Latency Cascade"
    assert state.services_investigated == ["db-primary"]
    assert state.metrics_checked == [("db-primary", "latency_p99_ms")]
    assert state.remediation_actions_taken == 0
    assert state.episode_done is False


def test_metric_aliases_work_for_investigation_and_output():
    env = IncidentEnvironment()
    env.reset(task_id="easy")

    observation = _step(env, "check_metrics", service_name="order-service", metric="memory")
    result = _result_dict(observation)

    assert observation.reward == 0.05
    assert "order-service memory: 98.5%" in result["tool_result"]
    assert env.state.metrics_checked == [("order-service", "memory_percent")]


def test_cache_failure_restart_heals_cascade():
    env = IncidentEnvironment()
    env.reset(task_id="cache_failure")

    _step(env, "check_alert_details")
    _step(env, "check_logs", service_name="db-primary")
    _step(env, "check_logs", service_name="cache-redis")
    _step(env, "check_metrics", service_name="cache-redis", metric="memory")
    _step(env, "restart_service", service_name="cache-redis")
    terminal = _step(
        env,
        "resolve_incident",
        root_cause="cache-redis",
        summary="cache-redis OOM fixed by restart; cache layer restored",
    )

    result = _result_dict(terminal)

    assert terminal.done is True
    assert result["diagnosis_correct"] is True
    assert result["breakdown"]["penalties"] == 0.0
    services = env._episode_state["services"]
    assert services["cache-redis"]["status"] == "healthy"
    assert services["db-primary"]["status"] == "healthy"


def test_memory_leak_rollback_and_restart_heals_cascade():
    env = IncidentEnvironment()
    env.reset(task_id="memory_leak")

    _step(env, "check_alert_details")
    _step(env, "check_logs", service_name="api-gateway")
    _step(env, "check_logs", service_name="user-service")
    _step(env, "check_metrics", service_name="user-service", metric="memory")
    _step(env, "check_dependencies", service_name="user-service")
    _step(env, "rollback_deploy", service_name="user-service")
    _step(env, "restart_service", service_name="order-service")
    _step(env, "restart_service", service_name="payment-service")
    terminal = _step(
        env,
        "resolve_incident",
        root_cause="user-service",
        summary="user-service v3.1.0 memory leak rolled back",
    )

    result = _result_dict(terminal)

    assert terminal.done is True
    assert result["diagnosis_correct"] is True
    assert result["breakdown"]["penalties"] == 0.0
    services = env._episode_state["services"]
    assert services["user-service"]["status"] == "healthy"
    assert services["order-service"]["status"] == "healthy"
    assert services["payment-service"]["status"] == "healthy"
    assert services["api-gateway"]["status"] == "healthy"
    assert services["notification-service"]["status"] == "healthy"


def test_metadata_endpoint_exposes_submission_ready_details():
    client = TestClient(app)

    response = client.get("/metadata")
    metadata = response.json()

    assert response.status_code == 200
    assert metadata["name"] == "SRE Incident Response Environment"
    assert "incident response" in metadata["description"].lower()
    assert metadata["author"] == "Team Bhole Chature"
    assert metadata["documentation_url"] == "https://huggingface.co/spaces/anurag203/sre-incident-env"
    assert metadata["readme_content"] is not None
