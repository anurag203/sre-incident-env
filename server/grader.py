"""
Episode Grader — scores agent performance deterministically.

Evaluates an agent's incident response across four dimensions:
investigation quality, diagnosis accuracy, remediation correctness,
and efficiency. Returns a score between 0.0 and 1.0 with a detailed breakdown.
"""

from typing import Optional


class Grader:
    """Deterministic episode grader for SRE incident response."""

    def grade_episode(
        self,
        scenario: dict,
        services_investigated: set,
        metrics_checked: set,
        remediation_actions: list,
        diagnosis_submitted: Optional[str],
        step_count: int,
        used_check_dependencies: bool = False,
        used_check_alert_details: bool = False,
    ) -> dict:
        """
        Grade a completed episode.

        Returns a dict with score (0.0-1.0) and detailed breakdown.
        """
        root_cause = scenario["root_cause"]
        correct_remediation = scenario.get("correct_remediation", [])
        max_steps = scenario.get("max_steps", 15)

        inv_score, inv_details = self._investigation_score(
            scenario, services_investigated, metrics_checked,
            used_check_dependencies, used_check_alert_details,
        )
        diag_score, diag_details = self._diagnosis_score(root_cause, diagnosis_submitted)
        rem_score, rem_details = self._remediation_score(remediation_actions, correct_remediation)
        eff_score, eff_details = self._efficiency_score(step_count, max_steps)
        penalty, pen_details = self._penalties(scenario, remediation_actions, correct_remediation)

        total = inv_score + diag_score + rem_score + eff_score - penalty
        total = round(max(0.0, min(1.0, total)), 2)

        return {
            "score": total,
            "investigation": round(inv_score, 2),
            "diagnosis": round(diag_score, 2),
            "remediation": round(rem_score, 2),
            "efficiency": round(eff_score, 2),
            "penalties": round(penalty, 2),
            "details": {
                "investigation": "; ".join(inv_details) if inv_details else "No investigation performed",
                "diagnosis": diag_details,
                "remediation": rem_details,
                "efficiency": eff_details,
                "penalties": "; ".join(pen_details) if pen_details else "No penalties",
            },
        }

    def _investigation_score(
        self,
        scenario: dict,
        services_investigated: set,
        metrics_checked: set,
        used_check_dependencies: bool,
        used_check_alert_details: bool,
    ) -> tuple[float, list[str]]:
        """Score investigation quality (0.0-0.30)."""
        root_cause_svc = scenario["root_cause"]["service"]
        score = 0.0
        details = []

        if root_cause_svc in services_investigated:
            score += 0.10
            details.append(f"Checked root cause service '{root_cause_svc}' logs (+0.10)")

        investigation_metrics = scenario.get("investigation_metrics", [])
        rc_metric_found = False
        for svc, metric in investigation_metrics:
            if svc == root_cause_svc:
                if (svc, metric) in metrics_checked or (svc, "all") in metrics_checked:
                    rc_metric_found = True
                    break
        if rc_metric_found:
            score += 0.05
            details.append(f"Checked root cause service metrics (+0.05)")

        non_rc = services_investigated - {root_cause_svc}
        if len(non_rc) >= 1:
            score += 0.05
            details.append(f"Investigated other services: {sorted(non_rc)} (+0.05)")

        if used_check_dependencies:
            score += 0.05
            details.append("Used check_dependencies (+0.05)")

        if used_check_alert_details:
            score += 0.05
            details.append("Used check_alert_details (+0.05)")

        score = min(score, 0.30)
        return score, details

    def _diagnosis_score(
        self,
        root_cause: dict,
        diagnosis_submitted: Optional[str],
    ) -> tuple[float, str]:
        """Score diagnosis accuracy (0.0-0.40)."""
        if diagnosis_submitted is None:
            return 0.0, "No diagnosis submitted (+0.00)"

        submitted = diagnosis_submitted.lower().strip()
        correct = root_cause["service"].lower()
        keywords = [kw.lower() for kw in root_cause.get("keywords", [])]

        if submitted == correct:
            return 0.40, f"Exact match: '{submitted}' == '{correct}' (+0.40)"

        if correct in submitted:
            return 0.30, f"Contains correct service: '{correct}' in '{submitted}' (+0.30)"

        if any(kw in submitted for kw in keywords):
            matched = [kw for kw in keywords if kw in submitted]
            return 0.20, f"Keyword match: {matched} (+0.20)"

        return 0.0, f"No match: '{submitted}' does not match '{correct}' (+0.00)"

    def _remediation_score(
        self,
        agent_actions: list,
        correct_actions: list,
    ) -> tuple[float, str]:
        """Score remediation correctness (0.0-0.20)."""
        if not correct_actions:
            return 0.0, "No remediation expected for this scenario (+0.00)"

        matched = self._count_matched_remediation(agent_actions, correct_actions)
        total = len(correct_actions)

        if matched == total:
            return 0.20, f"All {total} correct actions applied (+0.20)"
        elif matched > 0:
            return 0.10, f"{matched} of {total} correct actions applied (+0.10)"
        else:
            return 0.0, f"No correct remediation actions (0 of {total}) (+0.00)"

    def _efficiency_score(
        self,
        step_count: int,
        max_steps: int,
    ) -> tuple[float, str]:
        """Compute efficiency bonus (0.0-0.10)."""
        if max_steps <= 0:
            return 0.0, "No steps allowed (+0.00)"

        ratio = step_count / max_steps

        if ratio <= 0.3:
            score = 0.10
        elif ratio <= 0.5:
            score = 0.07
        elif ratio <= 0.7:
            score = 0.04
        elif ratio <= 0.9:
            score = 0.02
        else:
            score = 0.00

        return score, f"Used {step_count}/{max_steps} steps (ratio={ratio:.2f}) (+{score:.2f})"

    def _penalties(
        self,
        scenario: dict,
        agent_actions: list,
        correct_actions: list,
    ) -> tuple[float, list[str]]:
        """Compute penalty deductions (max 0.15)."""
        penalty = 0.0
        details = []

        for action in agent_actions:
            if action.get("action") not in ("restart_service", "rollback_deploy"):
                continue

            if action.get("pre_status") == "healthy" and not action.get("changed_state", False):
                penalty += 0.03
                svc = action.get("args", {}).get("service_name", "")
                details.append(f"Unnecessary {action['action']} on healthy '{svc}' (-0.03)")

        total_correct = len(correct_actions)
        if total_correct > 0:
            excess = len(agent_actions) - 2 * total_correct
            if excess > 0:
                excess_penalty = excess * 0.02
                penalty += excess_penalty
                details.append(f"Excessive remediation: {excess} extra actions (-{excess_penalty:.2f})")

        penalty = min(penalty, 0.15)
        return penalty, details

    def _count_matched_remediation(self, agent_actions: list, correct_actions: list) -> int:
        """Count how many correct remediation actions the agent performed."""
        matched = 0
        used_agent_indices = set()

        for correct in correct_actions:
            for i, agent in enumerate(agent_actions):
                if i in used_agent_indices:
                    continue
                if agent.get("action") == correct.get("action"):
                    if self._matches_args(agent.get("args", {}), correct.get("args", {})):
                        matched += 1
                        used_agent_indices.add(i)
                        break
        return matched

    def action_matches(self, action_name: str, action_args: dict, correct_actions: list) -> bool:
        """Return whether an action matches any expected remediation action."""
        return any(
            action_name == correct.get("action")
            and self._matches_args(action_args, correct.get("args", {}))
            for correct in correct_actions
        )

    def _matches_args(self, agent_args: dict, correct_args: dict) -> bool:
        """Check if agent's action arguments match the expected ones."""
        for key, value in correct_args.items():
            agent_value = agent_args.get(key, "")
            if key == "query":
                return self._query_matches(str(agent_value), str(value))
            elif key == "service_name":
                if str(agent_value).lower().strip() != str(value).lower().strip():
                    return False
            elif key == "replicas":
                if agent_value != value:
                    return False
        return True

    def _query_matches(self, agent_query: str, correct_query: str) -> bool:
        """Fuzzy match SQL queries based on key operations."""
        agent_lower = agent_query.lower()
        correct_lower = correct_query.lower()

        if "kill" in correct_lower:
            return "kill" in agent_lower
        if "create index" in correct_lower:
            return "create index" in agent_lower or "add index" in agent_lower
        if "drop" in correct_lower:
            return "drop" in agent_lower

        return agent_lower.strip() == correct_lower.strip()

    def step_reward(
        self,
        scenario: dict,
        action_type: str,
        action_args: dict,
        pre_services_investigated: set,
        pre_metrics_checked: set,
        tool_result: str,
        remediation_actions: list,
    ) -> float:
        """Compute incremental reward for a single step."""
        if scenario is None:
            return 0.0

        if str(tool_result).startswith("Error:"):
            return 0.0

        root_cause_svc = scenario["root_cause"]["service"]
        correct_actions = scenario.get("correct_remediation", [])

        service_investigation_types = ("check_logs", "check_metrics", "check_dependencies")
        global_investigation_types = ("check_alert_details", "list_services")
        remediation_types = ("restart_service", "rollback_deploy", "scale_service", "run_db_query")

        if action_type == "check_metrics":
            service_name = action_args.get("service_name")
            metric_name = action_args.get("_normalized_metric", action_args.get("metric", "all"))
            already_checked = (
                (service_name, metric_name) in pre_metrics_checked
                or (service_name, "all") in pre_metrics_checked
            )
            if service_name == root_cause_svc:
                return 0.0 if already_checked else 0.05
            return 0.0 if already_checked else 0.02

        if action_type in service_investigation_types:
            service_name = action_args.get("service_name")
            if service_name == root_cause_svc:
                return 0.0 if service_name in pre_services_investigated else 0.05
            elif service_name and service_name in pre_services_investigated:
                return 0.0
            else:
                return 0.02

        if action_type in global_investigation_types:
            return 0.02

        if action_type in remediation_types:
            is_correct = self.action_matches(action_type, action_args, correct_actions)
            return 0.05 if is_correct else -0.02

        if action_type == "resolve_incident":
            return 0.0

        return 0.0
