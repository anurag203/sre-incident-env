"""
SRE Incident Response Environment Implementation.

A pure MCP environment where an AI agent triages infrastructure incidents
by investigating logs/metrics, diagnosing root causes, and executing
remediation actions across simulated microservices.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from openenv.core.env_server.mcp_environment import MCPEnvironment
from openenv.core.env_server.mcp_types import (
    CallToolAction,
    CallToolObservation,
    ListToolsAction,
    ToolError,
    ToolErrorType,
)
from openenv.core.env_server.types import Action, EnvironmentMetadata, Observation, State

from fastmcp import FastMCP

from .scenario_engine import ScenarioEngine
from .grader import Grader

try:
    from ..models import SREState
except ImportError:
    from models import SREState


class IncidentEnvironment(MCPEnvironment):
    """
    SRE Incident Response Environment.

    Simulates on-call incident response with investigation and remediation tools.
    Each episode loads a scenario (easy/medium/hard) and the agent must diagnose
    and fix the issue within a step budget.
    """

    def __init__(self):
        self._scenario_engine = ScenarioEngine()
        self._grader = Grader()

        self._episode_state: Optional[dict] = None
        self._task_id: Optional[str] = None
        self._services_investigated: set = set()
        self._metrics_checked: set = set()
        self._remediation_actions: list = []
        self._diagnosis_submitted: Optional[str] = None
        self._diagnosis_summary: Optional[str] = None
        self._episode_done: bool = False
        self._used_check_dependencies: bool = False
        self._used_check_alert_details: bool = False
        self._used_list_services: bool = False
        self._final_score: Optional[dict] = None

        self._tool_registry: Dict[str, Callable] = {}

        mcp = FastMCP("sre_incident_env")
        self._register_tools(mcp)

        super().__init__(mcp)
        self._state = SREState(episode_id=str(uuid4()), step_count=0)

    def _register_tools(self, mcp: FastMCP):
        """Register all MCP tools as closures over self."""
        env = self

        @mcp.tool
        def check_logs(service_name: str) -> str:
            """Retrieve recent log entries for a service. Use this to investigate what happened."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            error = env._scenario_engine.validate_service(env._episode_state, service_name)
            if error:
                return f"Error: {error}"
            env._services_investigated.add(service_name)
            env._refresh_state()
            return env._scenario_engine.get_logs(env._episode_state, service_name)

        @mcp.tool
        def check_metrics(service_name: str, metric: str = "all") -> str:
            """Retrieve current metrics for a service. Options: cpu, memory, latency, error_rate, connections, all."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            error = env._scenario_engine.validate_service(env._episode_state, service_name)
            if error:
                return f"Error: {error}"
            metric_key = env._scenario_engine.normalize_metric_name(metric)
            result = env._scenario_engine.get_metrics(env._episode_state, service_name, metric)
            if result.startswith("Error:"):
                return result
            env._services_investigated.add(service_name)
            env._metrics_checked.add((service_name, metric_key))
            env._refresh_state()
            return result

        @mcp.tool
        def check_dependencies(service_name: str) -> str:
            """Show upstream and downstream dependencies for a service."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            error = env._scenario_engine.validate_service(env._episode_state, service_name)
            if error:
                return f"Error: {error}"
            env._services_investigated.add(service_name)
            env._used_check_dependencies = True
            env._refresh_state()
            return env._scenario_engine.get_dependencies(env._episode_state, service_name)

        @mcp.tool
        def check_alert_details() -> str:
            """Get the full details of the current incident alert."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            env._used_check_alert_details = True
            env._refresh_state()
            return env._scenario_engine.get_alert_details(env._episode_state)

        @mcp.tool
        def list_services() -> str:
            """List all services in the infrastructure with their current status."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            env._used_list_services = True
            env._refresh_state()
            return env._scenario_engine.list_services(env._episode_state)

        @mcp.tool
        def restart_service(service_name: str) -> str:
            """Restart a service. Use after identifying a service that needs recovery."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            error = env._scenario_engine.validate_service(env._episode_state, service_name)
            if error:
                return f"Error: {error}"

            service = env._episode_state["services"][service_name]
            pre_status = service.get("status")
            pre_version = service.get("deploy", {}).get("current_version")
            result = env._scenario_engine.apply_restart(env._episode_state, service_name)
            post_service = env._episode_state["services"][service_name]
            env._remediation_actions.append(
                env._build_remediation_action(
                    action_name="restart_service",
                    action_args={"service_name": service_name},
                    pre_status=pre_status,
                    post_status=post_service.get("status"),
                    changed_state=(
                        pre_status != post_service.get("status")
                        or pre_version != post_service.get("deploy", {}).get("current_version")
                    ),
                )
            )
            env._refresh_state()
            return result

        @mcp.tool
        def rollback_deploy(service_name: str) -> str:
            """Rollback the most recent deployment for a service, reverting to the previous version."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            error = env._scenario_engine.validate_service(env._episode_state, service_name)
            if error:
                return f"Error: {error}"

            service = env._episode_state["services"][service_name]
            pre_status = service.get("status")
            pre_version = service.get("deploy", {}).get("current_version")
            result = env._scenario_engine.apply_rollback(env._episode_state, service_name)
            post_service = env._episode_state["services"][service_name]
            env._remediation_actions.append(
                env._build_remediation_action(
                    action_name="rollback_deploy",
                    action_args={"service_name": service_name},
                    pre_status=pre_status,
                    post_status=post_service.get("status"),
                    changed_state=(
                        pre_status != post_service.get("status")
                        or pre_version != post_service.get("deploy", {}).get("current_version")
                    ),
                )
            )
            env._refresh_state()
            return result

        @mcp.tool
        def scale_service(service_name: str, replicas: int) -> str:
            """Scale a service to the specified number of replicas (1-10)."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            error = env._scenario_engine.validate_service(env._episode_state, service_name)
            if error:
                return f"Error: {error}"

            service = env._episode_state["services"][service_name]
            pre_status = service.get("status")
            pre_replicas = service.get("replicas")
            result = env._scenario_engine.apply_scale(env._episode_state, service_name, replicas)
            post_service = env._episode_state["services"][service_name]
            env._remediation_actions.append(
                env._build_remediation_action(
                    action_name="scale_service",
                    action_args={"service_name": service_name, "replicas": replicas},
                    pre_status=pre_status,
                    post_status=post_service.get("status"),
                    changed_state=pre_replicas != post_service.get("replicas"),
                )
            )
            env._refresh_state()
            return result

        @mcp.tool
        def run_db_query(query: str) -> str:
            """Execute a database administrative command (KILL QUERY, CREATE INDEX, SHOW PROCESSLIST, etc.)."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            before_services = json.dumps(env._episode_state.get("services", {}), sort_keys=True)
            result = env._scenario_engine.apply_db_query(env._episode_state, query)
            after_services = json.dumps(env._episode_state.get("services", {}), sort_keys=True)
            env._remediation_actions.append(
                env._build_remediation_action(
                    action_name="run_db_query",
                    action_args={"query": query},
                    changed_state=before_services != after_services,
                )
            )
            env._refresh_state()
            return result

        @mcp.tool
        def resolve_incident(root_cause: str, summary: str) -> str:
            """Submit your diagnosis and close the incident. This ends the episode. root_cause should be the service name that caused the incident."""
            if env._episode_state is None:
                return "Error: No active episode. Call reset() first."
            if env._episode_done:
                return "Error: Episode already ended."

            env._diagnosis_submitted = root_cause
            env._diagnosis_summary = summary
            env._episode_done = True

            score_result = env._grader.grade_episode(
                scenario=env._episode_state,
                services_investigated=env._services_investigated,
                metrics_checked=env._metrics_checked,
                remediation_actions=env._remediation_actions,
                diagnosis_submitted=env._diagnosis_submitted,
                step_count=env._state.step_count,
                used_check_dependencies=env._used_check_dependencies,
                used_check_alert_details=env._used_check_alert_details,
            )
            env._final_score = score_result
            env._refresh_state()

            return json.dumps({
                "status": "resolved",
                "your_diagnosis": root_cause,
                "expected_root_cause": env._episode_state["root_cause"]["service"],
                "score": score_result["score"],
                "breakdown": {
                    "investigation": score_result["investigation"],
                    "diagnosis": score_result["diagnosis"],
                    "remediation": score_result["remediation"],
                    "efficiency": score_result["efficiency"],
                    "penalties": score_result["penalties"],
                },
            })

        self._tool_registry = {
            "check_logs": check_logs,
            "check_metrics": check_metrics,
            "check_dependencies": check_dependencies,
            "check_alert_details": check_alert_details,
            "list_services": list_services,
            "restart_service": restart_service,
            "rollback_deploy": rollback_deploy,
            "scale_service": scale_service,
            "run_db_query": run_db_query,
            "resolve_incident": resolve_incident,
        }

    def _call_tool_directly(self, tool_name: str, arguments: dict) -> CallToolObservation:
        """Call a registered tool function directly, bypassing MCP transport."""
        func = self._tool_registry.get(tool_name)
        if func is None:
            return CallToolObservation(
                tool_name=tool_name,
                result=None,
                error=ToolError(
                    error_type=ToolErrorType.TOOL_NOT_FOUND,
                    message=f"Tool '{tool_name}' not found",
                ),
            )
        try:
            result = func(**arguments)
            return CallToolObservation(
                tool_name=tool_name,
                result=str(result),
            )
        except TypeError as e:
            return CallToolObservation(
                tool_name=tool_name,
                result=None,
                error=ToolError(
                    error_type=ToolErrorType.INVALID_ARGS,
                    message=str(e),
                ),
            )
        except Exception as e:
            return CallToolObservation(
                tool_name=tool_name,
                result=None,
                error=ToolError(
                    error_type=ToolErrorType.EXECUTION_ERROR,
                    message=str(e),
                ),
            )

    def reset(
        self,
        seed: Optional[int] = None,
        episode_id: Optional[str] = None,
        **kwargs: Any,
    ) -> CallToolObservation:
        """Reset the environment for a new incident episode."""
        task_id = kwargs.get("task_id", "easy")
        self._task_id = task_id

        self._episode_state = self._scenario_engine.load(task_id)

        self._services_investigated = set()
        self._metrics_checked = set()
        self._remediation_actions = []
        self._diagnosis_submitted = None
        self._diagnosis_summary = None
        self._episode_done = False
        self._used_check_dependencies = False
        self._used_check_alert_details = False
        self._used_list_services = False
        self._final_score = None

        self._episode_state.setdefault("_runtime", {"db_query_killed": False, "db_index_created": False})

        self._state = SREState(
            episode_id=episode_id or str(uuid4()),
            step_count=0,
        )
        self._refresh_state()

        alert = self._episode_state.get("alert", {})
        service_names = self._scenario_engine.get_service_names(self._episode_state)
        max_steps = self._episode_state.get("max_steps", 15)

        initial_info = json.dumps({
            "type": "alert",
            "alert": alert,
            "services": service_names,
            "service_count": len(service_names),
            "max_steps": max_steps,
            "task_id": task_id,
            "task_title": self._episode_state.get("title", ""),
            "instructions": (
                "You are an on-call SRE engineer. An incident alert has fired. "
                "Use the available tools to investigate the issue, identify the root cause, "
                "apply the fix, and call resolve_incident() when done."
            ),
        })

        return CallToolObservation(
            result=initial_info,
            tool_name="reset",
            done=False,
            reward=0.0,
        )

    def _step_impl(
        self,
        action: Action,
        timeout_s: Optional[float] = None,
        **kwargs: Any,
    ) -> Observation:
        """Handle non-MCP actions."""
        return CallToolObservation(
            result=json.dumps({
                "error": f"Unknown action type: {type(action).__name__}. "
                "Use ListToolsAction or CallToolAction for MCP interactions."
            }),
            tool_name="unknown",
            done=False,
            reward=0.0,
        )

    def step(
        self,
        action: Action,
        timeout_s: Optional[float] = None,
        **kwargs: Any,
    ) -> Observation:
        """Execute a step in the environment."""
        if self._episode_done:
            return self._build_terminal_observation()

        if isinstance(action, ListToolsAction):
            return self._handle_list_tools()

        self._state.step_count += 1
        pre_services_investigated = set(self._services_investigated)
        pre_metrics_checked = set(self._metrics_checked)

        if isinstance(action, CallToolAction):
            obs = self._call_tool_directly(action.tool_name, action.arguments)
        else:
            obs = self._step_impl(action, timeout_s=timeout_s, **kwargs)

        max_steps = (self._episode_state or {}).get("max_steps", 15)
        if self._episode_done:
            return self._build_terminal_observation()

        if self._state.step_count >= max_steps and not self._episode_done:
            self._episode_done = True
            self._final_score = self._grader.grade_episode(
                scenario=self._episode_state,
                services_investigated=self._services_investigated,
                metrics_checked=self._metrics_checked,
                remediation_actions=self._remediation_actions,
                diagnosis_submitted=self._diagnosis_submitted,
                step_count=self._state.step_count,
                used_check_dependencies=self._used_check_dependencies,
                used_check_alert_details=self._used_check_alert_details,
            )
            return self._build_terminal_observation()

        tool_name_str = ""
        action_args = {}
        if isinstance(action, CallToolAction):
            tool_name_str = action.tool_name
            action_args = action.arguments
            if tool_name_str == "check_metrics":
                action_args = {
                    **action_args,
                    "_normalized_metric": self._scenario_engine.normalize_metric_name(
                        action.arguments.get("metric", "all")
                    ),
                }

        tool_result = getattr(obs, "result", "") or ""
        obs_error = getattr(obs, "error", None)
        if obs_error is not None and not tool_result:
            tool_result = f"Error: {obs_error.message}"

        step_reward = self._grader.step_reward(
            scenario=self._episode_state,
            action_type=tool_name_str,
            action_args=action_args,
            pre_services_investigated=pre_services_investigated,
            pre_metrics_checked=pre_metrics_checked,
            tool_result=tool_result,
            remediation_actions=self._remediation_actions,
        )

        enriched = json.dumps({
            "tool_result": tool_result,
            "step": self._state.step_count,
            "steps_remaining": max_steps - self._state.step_count,
            "services_investigated": list(self._services_investigated),
        })
        self._refresh_state()

        return CallToolObservation(
            result=enriched,
            tool_name=tool_name_str,
            done=False,
            reward=step_reward,
        )

    async def step_async(
        self,
        action: Action,
        timeout_s: Optional[float] = None,
        **kwargs: Any,
    ) -> Observation:
        """Async step delegates to sync step (tools are all synchronous)."""
        return self.step(action, timeout_s=timeout_s, **kwargs)

    def _build_terminal_observation(self) -> CallToolObservation:
        """Build the terminal observation with final score."""
        score_result = self._final_score or {"score": 0.0}
        obs_type = "resolution" if self._diagnosis_submitted else "timeout"
        self._refresh_state()

        terminal_info = json.dumps({
            "type": obs_type,
            "message": (
                "Incident resolved successfully."
                if self._diagnosis_submitted
                else "Step budget exhausted. Incident auto-closed."
            ),
            "score": score_result.get("score", 0.0),
            "breakdown": {
                "investigation": score_result.get("investigation", 0.0),
                "diagnosis": score_result.get("diagnosis", 0.0),
                "remediation": score_result.get("remediation", 0.0),
                "efficiency": score_result.get("efficiency", 0.0),
                "penalties": score_result.get("penalties", 0.0),
            },
            "diagnosis_correct": (
                self._diagnosis_submitted is not None
                and self._episode_state is not None
                and self._diagnosis_submitted.lower().strip()
                == self._episode_state["root_cause"]["service"].lower()
            ),
            "root_cause_expected": (
                self._episode_state["root_cause"]["service"]
                if self._episode_state
                else None
            ),
            "root_cause_submitted": self._diagnosis_submitted,
            "steps_used": self._state.step_count,
            "max_steps": (self._episode_state or {}).get("max_steps", 0),
        })

        return CallToolObservation(
            result=terminal_info,
            tool_name="resolve_incident" if self._diagnosis_submitted else "timeout",
            done=True,
            reward=score_result.get("score", 0.0),
        )

    def _handle_list_tools(self):
        """Delegate to parent's list_tools handler."""
        return super()._handle_list_tools()

    def _get_last_action_type(self) -> str:
        """Infer the last action type from tracking state."""
        if self._remediation_actions:
            return self._remediation_actions[-1].get("action", "")
        if self._services_investigated:
            return "check_logs"
        return ""

    def _get_last_service_name(self) -> Optional[str]:
        """Infer the last service acted upon."""
        if self._remediation_actions:
            return self._remediation_actions[-1].get("args", {}).get("service_name")
        if self._services_investigated:
            return list(self._services_investigated)[-1]
        return None

    @property
    def state(self) -> SREState:
        """Get the current environment state."""
        return self._state

    def _build_remediation_action(
        self,
        *,
        action_name: str,
        action_args: dict,
        pre_status: Optional[str] = None,
        post_status: Optional[str] = None,
        changed_state: bool = False,
    ) -> dict:
        """Create a rich action record for grading and debugging."""
        correct_actions = (self._episode_state or {}).get("correct_remediation", [])
        return {
            "action": action_name,
            "args": action_args,
            "pre_status": pre_status,
            "post_status": post_status,
            "changed_state": changed_state,
            "is_correct_action": self._grader.action_matches(
                action_name,
                action_args,
                correct_actions,
            ),
        }

    def _refresh_state(self) -> None:
        """Keep the typed state in sync with the internal episode trackers."""
        if not isinstance(self._state, SREState):
            return

        self._state.task_id = self._task_id or ""
        self._state.task_title = (self._episode_state or {}).get("title", "")
        self._state.services_investigated = sorted(self._services_investigated)
        self._state.metrics_checked = sorted(self._metrics_checked)
        self._state.remediation_actions_taken = len(self._remediation_actions)
        self._state.diagnosis_submitted = self._diagnosis_submitted
        self._state.episode_done = self._episode_done
        self._state.final_score = None if self._final_score is None else self._final_score.get("score")

    def get_metadata(self) -> EnvironmentMetadata:
        """Return rich metadata for docs, validation, and the hosted web UI."""
        readme_path = Path(__file__).resolve().parents[1] / "README.md"
        readme_content = None
        if readme_path.exists():
            readme_content = readme_path.read_text(encoding="utf-8")

        return EnvironmentMetadata(
            name="SRE Incident Response Environment",
            description=(
                "A real-world OpenEnv environment for SRE on-call incident response. "
                "Agents investigate alerts across a production microservice stack, "
                "apply targeted remediation, and are graded on diagnosis quality, "
                "recovery correctness, and operational efficiency."
            ),
            readme_content=readme_content,
            version="0.1.0",
            author="Team Bhole Chature",
            documentation_url="https://huggingface.co/spaces/anurag203/sre-incident-env",
        )
