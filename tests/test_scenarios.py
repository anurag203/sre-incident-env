import json
from pathlib import Path


SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def test_all_scenarios_have_consistent_structure():
    for scenario_path in sorted(SCENARIOS_DIR.glob("*.json")):
        scenario = json.loads(scenario_path.read_text())

        assert scenario["id"]
        assert scenario["title"]
        assert scenario["difficulty"] in {"easy", "medium", "hard"}
        assert scenario["max_steps"] > 0

        services = scenario["services"]
        root_cause_service = scenario["root_cause"]["service"]

        assert root_cause_service in services
        assert scenario["investigation_targets"]
        assert scenario["investigation_metrics"]

        for service_name, service in services.items():
            assert service["status"] in {"healthy", "degraded", "crashed"}
            assert "logs" in service and service["logs"]
            assert "metrics" in service
            assert "dependencies" in service

            for upstream in service["dependencies"].get("upstream", []):
                assert upstream in services
            for downstream in service["dependencies"].get("downstream", []):
                assert downstream in services

        for action in scenario["correct_remediation"]:
            assert action["action"]
            if "service_name" in action["args"]:
                assert action["args"]["service_name"] in services
