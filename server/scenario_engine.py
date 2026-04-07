"""
Scenario Engine — loads and manages incident scenario data.

Loads scenario JSON files from the scenarios/ directory and provides
methods to query and mutate the simulated infrastructure state during
an episode.
"""

import copy
import json
from pathlib import Path
from typing import Optional


SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
METRIC_ALIASES = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "latency": "latency_p99_ms",
    "error_rate": "error_rate_percent",
    "connections": "active_connections",
}


class ScenarioEngine:
    """Loads scenario JSON files and provides query/mutation methods."""

    def __init__(self):
        self._scenario_cache: dict = {}

    def load(self, task_id: str) -> dict:
        """Load and return a deep copy of the scenario for the given task_id."""
        if task_id not in self._scenario_cache:
            path = SCENARIOS_DIR / f"{task_id}.json"
            if not path.exists():
                available = [f.stem for f in SCENARIOS_DIR.glob("*.json")]
                raise ValueError(
                    f"Scenario '{task_id}' not found. Available: {available}"
                )
            with open(path, "r") as f:
                self._scenario_cache[task_id] = json.load(f)

        return copy.deepcopy(self._scenario_cache[task_id])

    def get_available_tasks(self) -> list[str]:
        """Return list of available task IDs."""
        return [f.stem for f in SCENARIOS_DIR.glob("*.json")]

    def normalize_metric_name(self, metric: str) -> str:
        """Map user-friendly metric aliases to canonical scenario keys."""
        return METRIC_ALIASES.get(metric, metric)

    def get_service_names(self, episode_state: dict) -> list[str]:
        """Get list of all service names in the scenario."""
        return list(episode_state["services"].keys())

    def validate_service(self, episode_state: dict, service_name: str) -> Optional[str]:
        """Validate that a service exists. Returns error message or None."""
        services = self.get_service_names(episode_state)
        if service_name not in services:
            return f"Service '{service_name}' not found. Available services: {services}"
        return None

    def get_logs(self, episode_state: dict, service_name: str) -> str:
        """Get formatted log lines for a service."""
        error = self.validate_service(episode_state, service_name)
        if error:
            return error

        service = episode_state["services"][service_name]
        logs = service.get("logs", [])
        if not logs:
            return f"No log entries available for {service_name}."

        header = f"=== Logs for {service_name} (status: {service['status']}) ==="
        return header + "\n" + "\n".join(logs)

    def get_metrics(self, episode_state: dict, service_name: str, metric: str = "all") -> str:
        """Get formatted metrics for a service."""
        error = self.validate_service(episode_state, service_name)
        if error:
            return error

        service = episode_state["services"][service_name]
        metrics = service.get("metrics", {})
        metric_key = self.normalize_metric_name(metric)

        if metric != "all" and metric_key not in metrics:
            available = list(metrics.keys())
            return f"Error: Metric '{metric}' not found for {service_name}. Available: {available}"

        if metric == "all":
            lines = [f"=== Metrics for {service_name} (status: {service['status']}) ==="]
            lines.append(f"  CPU:                {metrics.get('cpu_percent', 'N/A')}%")
            lines.append(f"  Memory:             {metrics.get('memory_percent', 'N/A')}%")
            lines.append(f"  Latency (p99):      {metrics.get('latency_p99_ms', 'N/A')}ms")
            lines.append(f"  Error rate:         {metrics.get('error_rate_percent', 'N/A')}%")
            lines.append(f"  Active connections: {metrics.get('active_connections', 'N/A')}")
            return "\n".join(lines)
        else:
            value = metrics[metric_key]
            unit = _metric_unit(metric_key)
            label = metric if metric in METRIC_ALIASES else metric_key
            return f"{service_name} {label}: {value}{unit}"

    def get_dependencies(self, episode_state: dict, service_name: str) -> str:
        """Get formatted dependency info for a service."""
        error = self.validate_service(episode_state, service_name)
        if error:
            return error

        service = episode_state["services"][service_name]
        deps = service.get("dependencies", {})
        upstream = deps.get("upstream", [])
        downstream = deps.get("downstream", [])

        lines = [f"=== Dependencies for {service_name} ==="]
        lines.append(f"  Upstream (depends on {service_name}): {', '.join(upstream) if upstream else 'none'}")
        lines.append(f"  Downstream ({service_name} depends on): {', '.join(downstream) if downstream else 'none'}")
        return "\n".join(lines)

    def get_alert_details(self, episode_state: dict) -> str:
        """Get formatted alert details."""
        alert = episode_state.get("alert", {})
        lines = [
            "=== Incident Alert ===",
            f"  Severity:     {alert.get('severity', 'UNKNOWN')}",
            f"  Title:        {alert.get('title', 'N/A')}",
            f"  Message:      {alert.get('message', 'N/A')}",
            f"  Triggered at: {alert.get('triggered_at', 'N/A')}",
        ]
        return "\n".join(lines)

    def list_services(self, episode_state: dict) -> str:
        """List all services with their current status."""
        lines = ["=== Infrastructure Services ==="]
        for name, data in episode_state["services"].items():
            status = data.get("status", "unknown")
            marker = {"healthy": "[OK]", "degraded": "[WARN]", "crashed": "[DOWN]"}.get(
                status, "[???]"
            )
            deploy = data.get("deploy", {})
            version = deploy.get("current_version", "unknown")
            lines.append(f"  {marker} {name:<30s} status={status:<10s} version={version}")
        return "\n".join(lines)

    def apply_restart(self, episode_state: dict, service_name: str) -> str:
        """Apply restart and return result message."""
        error = self.validate_service(episode_state, service_name)
        if error:
            return error

        service = episode_state["services"][service_name]
        old_status = service["status"]

        root_cause_svc = episode_state["root_cause"]["service"]
        correct_actions = episode_state.get("correct_remediation", [])
        is_correct = any(
            a["action"] == "restart_service" and a["args"]["service_name"] == service_name
            for a in correct_actions
        )

        if old_status in ("crashed", "degraded"):
            if is_correct or self._is_dependency_fix_valid(episode_state, service_name):
                self._apply_recovery_profile(episode_state, service_name)
                self._append_log(service, "Service restarted and passed health checks.")
                self._maybe_finalize_cascade_recovery(episode_state)
                return f"Service '{service_name}' restarted successfully. Status: healthy (was {old_status})."
            else:
                service["status"] = "degraded"
                self._append_log(
                    service,
                    "Restart completed, but the service is still impacted by an unresolved upstream issue.",
                )
                return (
                    f"Service '{service_name}' restarted but remains degraded. "
                    f"The underlying issue may not be resolved."
                )
        else:
            return f"Service '{service_name}' was already healthy. Restart completed (no-op)."

    def apply_rollback(self, episode_state: dict, service_name: str) -> str:
        """Apply rollback and return result message."""
        error = self.validate_service(episode_state, service_name)
        if error:
            return error

        service = episode_state["services"][service_name]
        deploy = service.get("deploy", {})
        current = deploy.get("current_version", "unknown")
        previous = deploy.get("previous_version", "unknown")

        if current == previous:
            return f"No previous version available for '{service_name}'. Already at {current}."

        deploy["current_version"] = previous
        deploy["previous_version"] = current

        correct_actions = episode_state.get("correct_remediation", [])
        is_correct = any(
            a["action"] == "rollback_deploy" and a["args"]["service_name"] == service_name
            for a in correct_actions
        )

        if is_correct:
            self._apply_recovery_profile(episode_state, service_name)
            self._append_log(
                service,
                f"Rollback to {previous} completed successfully. Token validation has recovered.",
            )
            self._maybe_finalize_cascade_recovery(episode_state)
            return (
                f"Rolled back '{service_name}' from {current} to {previous}. "
                f"Service recovering. Status: healthy."
            )
        else:
            return (
                f"Rolled back '{service_name}' from {current} to {previous}. "
                f"Service status unchanged ({service['status']})."
            )

    def apply_scale(self, episode_state: dict, service_name: str, replicas: int) -> str:
        """Apply scaling and return result message."""
        error = self.validate_service(episode_state, service_name)
        if error:
            return error

        if replicas < 1 or replicas > 10:
            return f"Invalid replica count: {replicas}. Must be between 1 and 10."

        service = episode_state["services"][service_name]
        service["replicas"] = replicas
        self._append_log(service, f"Replica count adjusted to {replicas}.")
        return f"Scaled '{service_name}' to {replicas} replicas. Status: {service['status']}."

    def apply_db_query(self, episode_state: dict, query: str) -> str:
        """Execute a simulated DB query and return result."""
        if not query or not query.strip():
            return "Error: Empty query provided."

        query_lower = query.lower().strip()
        scenario_id = episode_state.get("id")
        runtime = self._runtime(episode_state)

        if "kill" in query_lower and ("query" in query_lower or "process" in query_lower):
            if scenario_id != "medium":
                return "Query terminated successfully. No incident-relevant change detected."
            runtime["db_query_killed"] = True
            db = episode_state["services"].get("db-primary")
            if db:
                db["status"] = "degraded"
                metrics = db.setdefault("metrics", {})
                metrics["cpu_percent"] = 64.0
                metrics["latency_p99_ms"] = 1600
                metrics["active_connections"] = 68
                self._append_log(db, "Administrative kill cleared the blocking slow query.")

            if runtime.get("db_index_created"):
                self._finalize_medium_recovery(episode_state)
                return (
                    "Query terminated successfully. Affected rows: 1. "
                    "The new index is already in place and the database has stabilized."
                )

            return (
                "Query terminated successfully. Affected rows: 1. "
                "Active slow queries cleared, but the missing index still needs to be created."
            )

        if "create index" in query_lower or "add index" in query_lower:
            if scenario_id != "medium":
                return "Index created successfully. No incident-relevant change detected."
            runtime["db_index_created"] = True
            db = episode_state["services"].get("db-primary")
            if db:
                self._append_log(
                    db,
                    "Index creation completed for orders(user_id, created_at). Future lookups will avoid sequential scans.",
                )

            if runtime.get("db_query_killed"):
                self._finalize_medium_recovery(episode_state)
                return (
                    "Index created successfully. Query optimizer will now use the new index, "
                    "and the database has recovered."
                )

            return (
                "Index created successfully. Future queries will improve, "
                "but the currently blocked slow query is still running."
            )

        if "show processlist" in query_lower or "show full processlist" in query_lower:
            return (
                "ID | User    | Host      | DB     | Command | Time | State           | Info\n"
                "4281| app_user| 10.0.1.5  | maindb | Query   | 45   | Sending data    | SELECT u.*, s.*, o.* FROM users u JOIN...\n"
                "4282| app_user| 10.0.1.6  | maindb | Query   | 38   | Sending data    | SELECT u.*, s.*, o.* FROM users u JOIN...\n"
                "4283| monitor | localhost | maindb | Sleep   | 120  | Waiting         | NULL"
            )

        if "explain" in query_lower:
            return (
                "QUERY PLAN:\n"
                "  -> Seq Scan on orders (cost=0.00..45231.00 rows=2400000)\n"
                "     Filter: (user_id = $1)\n"
                "  NOTE: No index on orders(user_id, created_at). Consider creating one."
            )

        return f"Query executed. Result: OK. Rows affected: 0."

    def _is_dependency_fix_valid(self, episode_state: dict, service_name: str) -> bool:
        """Check if restarting this service is valid after root cause is fixed."""
        root_svc = episode_state["root_cause"]["service"]
        root_status = episode_state["services"].get(root_svc, {}).get("status", "")
        if root_status == "healthy":
            service = episode_state["services"][service_name]
            deps = service.get("dependencies", {})
            downstream = deps.get("downstream", [])
            if root_svc in downstream:
                return True
        return False

    def _runtime(self, episode_state: dict) -> dict:
        """Return mutable runtime-only state for the active episode."""
        return episode_state.setdefault(
            "_runtime",
            {
                "db_query_killed": False,
                "db_index_created": False,
            },
        )

    def _append_log(self, service: dict, message: str) -> None:
        """Append a synthetic log line so agents can observe recovery."""
        logs = service.setdefault("logs", [])
        logs.append(f"RECOVERY [INFO] {message}")

    def _mark_service_healthy(
        self,
        service: dict,
        *,
        cpu_percent: Optional[float] = None,
        memory_percent: Optional[float] = None,
        latency_p99_ms: Optional[int] = None,
        error_rate_percent: Optional[float] = 0.0,
        active_connections: Optional[int] = None,
    ) -> None:
        """Move a service into a healthy state with optional metric updates."""
        service["status"] = "healthy"
        metrics = service.setdefault("metrics", {})
        if cpu_percent is not None:
            metrics["cpu_percent"] = cpu_percent
        if memory_percent is not None:
            metrics["memory_percent"] = memory_percent
        if latency_p99_ms is not None:
            metrics["latency_p99_ms"] = latency_p99_ms
        if error_rate_percent is not None:
            metrics["error_rate_percent"] = error_rate_percent
        if active_connections is not None:
            metrics["active_connections"] = active_connections

    def _apply_recovery_profile(self, episode_state: dict, service_name: str) -> None:
        """Apply realistic healthy-state metrics after a successful remediation."""
        services = episode_state["services"]
        service = services[service_name]
        profiles = {
            "order-service": {
                "cpu_percent": 34.0,
                "memory_percent": 51.0,
                "latency_p99_ms": 42,
                "error_rate_percent": 0.0,
                "active_connections": 31,
            },
            "auth-service": {
                "cpu_percent": 36.0,
                "memory_percent": 49.0,
                "latency_p99_ms": 14,
                "error_rate_percent": 0.0,
                "active_connections": 210,
            },
            "user-service": {
                "cpu_percent": 31.0,
                "memory_percent": 47.0,
                "latency_p99_ms": 72,
                "error_rate_percent": 0.6,
                "active_connections": 140,
            },
            "order-service:hard": {
                "cpu_percent": 28.0,
                "memory_percent": 44.0,
                "latency_p99_ms": 68,
                "error_rate_percent": 0.4,
                "active_connections": 120,
            },
            "payment-service": {
                "cpu_percent": 24.0,
                "memory_percent": 39.0,
                "latency_p99_ms": 61,
                "error_rate_percent": 0.3,
                "active_connections": 88,
            },
            "notification-service": {
                "cpu_percent": 18.0,
                "memory_percent": 33.0,
                "latency_p99_ms": 40,
                "error_rate_percent": 0.2,
                "active_connections": 70,
            },
            "api-gateway": {
                "cpu_percent": 38.0,
                "memory_percent": 41.0,
                "latency_p99_ms": 24,
                "error_rate_percent": 1.2,
                "active_connections": 960,
            },
            "db-primary": {
                "cpu_percent": 34.0,
                "memory_percent": 61.0,
                "latency_p99_ms": 14,
                "error_rate_percent": 0.0,
                "active_connections": 48,
            },
            "cache-redis": {
                "cpu_percent": 12.0,
                "memory_percent": 45.0,
                "latency_p99_ms": 2,
                "error_rate_percent": 0.0,
                "active_connections": 200,
            },
            "user-service:memory_leak": {
                "cpu_percent": 31.0,
                "memory_percent": 47.0,
                "latency_p99_ms": 72,
                "error_rate_percent": 0.6,
                "active_connections": 140,
            },
        }

        profile_key = service_name
        if episode_state.get("id") == "hard" and service_name == "order-service":
            profile_key = "order-service:hard"
        elif episode_state.get("id") == "memory_leak" and service_name == "user-service":
            profile_key = "user-service:memory_leak"

        profile = profiles.get(profile_key, {})
        self._mark_service_healthy(service, **profile)

        if episode_state.get("id") == "easy" and service_name == "order-service":
            gateway = services.get("api-gateway")
            if gateway:
                gateway_metrics = gateway.setdefault("metrics", {})
                gateway_metrics["error_rate_percent"] = 1.0
                gateway_metrics["latency_p99_ms"] = 9
                self._append_log(gateway, "Upstream order-service recovered. Circuit breaker closed.")

    def _finalize_medium_recovery(self, episode_state: dict) -> None:
        """Fully recover the database cascade once both DB actions are complete."""
        services = episode_state["services"]
        self._apply_recovery_profile(episode_state, "db-primary")
        self._append_log(
            services["db-primary"],
            "Slow-query incident resolved. Connection pool returned to normal limits.",
        )

        for service_name in ("user-service", "payment-service", "api-gateway"):
            if service_name in services:
                self._apply_recovery_profile(episode_state, service_name)
                self._append_log(
                    services[service_name],
                    "Upstream database latency normalized and request handling recovered.",
                )

    def _maybe_finalize_cascade_recovery(self, episode_state: dict) -> None:
        """Auto-heal dependent services once a scenario is fully remediated."""
        scenario_id = episode_state.get("id")

        if scenario_id == "hard":
            services = episode_state["services"]
            required = ("auth-service", "user-service", "order-service", "payment-service")
            if not all(services.get(name, {}).get("status") == "healthy" for name in required):
                return
            for service_name in ("api-gateway", "notification-service"):
                service = services.get(service_name)
                if service and service.get("status") != "healthy":
                    self._apply_recovery_profile(episode_state, service_name)
                    self._append_log(
                        service,
                        "Dependent authentication path recovered and backlog drain is in progress.",
                    )

        elif scenario_id == "cache_failure":
            services = episode_state["services"]
            if services.get("cache-redis", {}).get("status") != "healthy":
                return
            for service_name in ("db-primary", "user-service", "order-service", "api-gateway"):
                service = services.get(service_name)
                if service and service.get("status") != "healthy":
                    self._apply_recovery_profile(episode_state, service_name)
                    self._append_log(
                        service,
                        "Cache layer restored. Database load returning to normal.",
                    )

        elif scenario_id == "memory_leak":
            services = episode_state["services"]
            required = ("user-service", "order-service", "payment-service")
            if not all(services.get(name, {}).get("status") == "healthy" for name in required):
                return
            for service_name in ("api-gateway", "notification-service"):
                service = services.get(service_name)
                if service and service.get("status") != "healthy":
                    self._apply_recovery_profile(episode_state, service_name)
                    self._append_log(
                        service,
                        "Upstream user-service recovered after rollback. Request handling restored.",
                    )


def _metric_unit(metric: str) -> str:
    """Return the unit suffix for a metric name."""
    units = {
        "cpu_percent": "%",
        "memory_percent": "%",
        "latency_p99_ms": "ms",
        "error_rate_percent": "%",
        "active_connections": "",
    }
    return units.get(metric, "")
