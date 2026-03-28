import json

from openenv.core.env_server.mcp_types import CallToolAction

import inference
from server.incident_environment import IncidentEnvironment


def _run_policy_plan(task_id: str) -> tuple[dict, IncidentEnvironment]:
    env = IncidentEnvironment()
    env.reset(task_id=task_id)

    terminal = None
    for tool_name, args in inference.POLICY_PLANS[task_id]:
        terminal = env.step(CallToolAction(tool_name=tool_name, arguments=args))

    assert terminal is not None
    return json.loads(terminal.result), env


def test_parse_tool_call_handles_multiple_common_llm_formats():
    cases = [
        (
            'TOOL: check_metrics\nARGS: {"service_name": "db-primary", "metric": "latency"}',
            ("check_metrics", {"service_name": "db-primary", "metric": "latency"}),
        ),
        (
            '{"tool": "restart_service", "args": {"service_name": "order-service"}}',
            ("restart_service", {"service_name": "order-service"}),
        ),
        (
            "<think>Investigate first</think>\n```json\n"
            '{"tool_name": "run_db_query", "arguments": {"query": "KILL QUERY 4281"}}\n```',
            ("run_db_query", {"query": "KILL QUERY 4281"}),
        ),
        (
            'check_metrics("auth-service", "error_rate")',
            ("check_metrics", {"service_name": "auth-service", "metric": "error_rate"}),
        ),
        (
            "TOOL: rollback_deploy\nARGS: {'service_name': 'auth-service'}",
            ("rollback_deploy", {"service_name": "auth-service"}),
        ),
    ]

    for response_text, expected in cases:
        assert inference.parse_tool_call(response_text) == expected


def test_policy_mode_does_not_require_live_credentials(monkeypatch):
    monkeypatch.setattr(inference, "BASELINE_MODE", "policy")
    monkeypatch.setattr(inference, "API_KEY", None)
    monkeypatch.setattr(inference, "MODEL_NAME", None)

    assert inference.create_client() is None


def test_policy_plans_hit_expected_scores():
    expected_scores = {
        "easy": 0.89,
        "medium": 0.92,
        "hard": 0.92,
    }

    for task_id, expected_score in expected_scores.items():
        result, env = _run_policy_plan(task_id)
        assert result["score"] == expected_score
        assert result["diagnosis_correct"] is True
        assert env.state.final_score == expected_score
