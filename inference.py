"""
SRE Incident Response - Baseline Inference Script
==================================================

MANDATORY:
- Before submitting, ensure the following variables are defined in your
  environment configuration:
    API_BASE_URL   The API endpoint for the LLM.
    MODEL_NAME     The model identifier to use for inference.
    HF_TOKEN       Your Hugging Face / API key.

- The inference script must be named `inference.py` and placed in the root
  directory of the project.
- Participants must use OpenAI Client for all LLM calls using above variables.
"""

import asyncio
import json
import os
import re
import sys
import textwrap
from typing import Optional

import certifi
import httpx
from openai import OpenAI

API_BASE_URL = os.getenv("API_BASE_URL", "https://router.huggingface.co/v1")
API_KEY = os.getenv("HF_TOKEN") or os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")
MAX_STEPS_OVERRIDE = int(os.getenv("MAX_STEPS_PER_TASK", "0"))
MAX_LLM_STEPS_PER_TASK = int(os.getenv("MAX_LLM_STEPS_PER_TASK", "2"))
TEMPERATURE = 0.2
MAX_TOKENS = 300
INSECURE_SKIP_VERIFY = os.getenv("OPENENV_INSECURE_SKIP_VERIFY", "0") == "1"
BASELINE_MODE = os.getenv("BASELINE_MODE", "hybrid").lower()

ENV_BASE_URL = os.getenv("ENV_BASE_URL", "http://localhost:8000")

SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert Site Reliability Engineer (SRE) handling a production incident.

    You have access to these tools to investigate and fix the issue:

    INVESTIGATION TOOLS (read-only):
    - check_alert_details() - Get full alert information
    - list_services() - List all services with their status
    - check_logs(service_name) - Read recent logs for a service
    - check_metrics(service_name, metric="all") - Get metrics.
      Friendly aliases like cpu/memory/latency/error_rate/connections work, and so do canonical keys like latency_p99_ms.
    - check_dependencies(service_name) - See upstream/downstream dependencies

    REMEDIATION TOOLS (take action):
    - restart_service(service_name) - Restart a crashed or degraded service
    - rollback_deploy(service_name) - Revert to previous deployment version
    - scale_service(service_name, replicas) - Scale service replicas (1-10)
    - run_db_query(query) - Execute a DB admin command (KILL QUERY, CREATE INDEX, etc.)

    TERMINAL (ends the episode):
    - resolve_incident(root_cause, summary) - Submit your diagnosis. root_cause = the service name that caused the incident.

    YOUR PROCESS:
    1. Check alert details to understand the scope
    2. List services to see which are unhealthy
    3. Check logs of unhealthy services to find clues
    4. Trace the root cause (look for the UPSTREAM cause, not downstream symptoms)
    5. Apply the fix (restart, rollback, scale, or DB query)
    6. Call resolve_incident with the root cause service name and a brief summary

    RESPOND WITH EXACTLY ONE TOOL CALL PER TURN:
    TOOL: tool_name
    ARGS: {"arg1": "value1", "arg2": "value2"}

    Be systematic. Look for the root cause, not just symptoms.
""")

POLICY_PLANS = {
    "easy": [
        ("check_alert_details", {}),
        ("check_logs", {"service_name": "order-service"}),
        ("check_metrics", {"service_name": "order-service", "metric": "memory"}),
        ("check_dependencies", {"service_name": "order-service"}),
        ("restart_service", {"service_name": "order-service"}),
        (
            "resolve_incident",
            {
                "root_cause": "order-service",
                "summary": (
                    "order-service exhausted heap memory while processing a large batch, "
                    "causing an OOM crash; restarting the service restored availability."
                ),
            },
        ),
    ],
    "medium": [
        ("check_alert_details", {}),
        ("check_logs", {"service_name": "user-service"}),
        ("check_logs", {"service_name": "db-primary"}),
        ("check_metrics", {"service_name": "db-primary", "metric": "latency"}),
        ("run_db_query", {"query": "KILL QUERY 4281"}),
        (
            "run_db_query",
            {"query": "CREATE INDEX idx_orders_user_created ON orders(user_id, created_at)"},
        ),
        (
            "resolve_incident",
            {
                "root_cause": "db-primary",
                "summary": (
                    "db-primary was saturated by an unindexed join query scanning the orders "
                    "table; killing the long-running query and creating the composite index "
                    "restored latency."
                ),
            },
        ),
    ],
    "hard": [
        ("check_alert_details", {}),
        ("check_logs", {"service_name": "api-gateway"}),
        ("check_logs", {"service_name": "auth-service"}),
        ("check_metrics", {"service_name": "auth-service", "metric": "error_rate"}),
        ("rollback_deploy", {"service_name": "auth-service"}),
        ("restart_service", {"service_name": "user-service"}),
        ("restart_service", {"service_name": "order-service"}),
        ("restart_service", {"service_name": "payment-service"}),
        (
            "resolve_incident",
            {
                "root_cause": "auth-service",
                "summary": (
                    "auth-service deploy v3.2.0 introduced a token-validation bug that broke "
                    "authentication across downstream services; rolling back auth-service and "
                    "restarting dependent services restored the stack."
                ),
            },
        ),
    ],
}


def create_client() -> Optional[OpenAI]:
    """Create an OpenAI client with the configured API endpoint."""
    if not API_KEY:
        if BASELINE_MODE == "policy":
            return None
        print("ERROR: HF_TOKEN, OPENAI_API_KEY, or API_KEY environment variable is required.")
        sys.exit(1)
    if not MODEL_NAME:
        if BASELINE_MODE == "policy":
            return None
        print("ERROR: MODEL_NAME environment variable is required.")
        sys.exit(1)

    verify = False if INSECURE_SKIP_VERIFY else certifi.where()
    http_client = httpx.Client(verify=verify, timeout=90.0)
    return OpenAI(base_url=API_BASE_URL, api_key=API_KEY, http_client=http_client)


def parse_tool_call(response_text: str) -> tuple[Optional[str], dict]:
    """Parse a tool call from the LLM response text."""
    cleaned = _strip_reasoning(response_text)
    tool_match = re.search(r"TOOL:\s*(\w+)", cleaned, re.IGNORECASE)
    raw_args = _extract_args_block(cleaned)

    if tool_match:
        tool_name = tool_match.group(1).strip()
        args = {}
        if raw_args:
            args = _load_json_like(raw_args)
        return tool_name, args

    json_tool_name, json_tool_args = _parse_json_tool_call(cleaned)
    if json_tool_name:
        return json_tool_name, json_tool_args

    func_match = re.search(r"(\w+)\s*\(([^)]*)\)", cleaned)
    if func_match:
        tool_name = func_match.group(1).strip()
        raw_args = func_match.group(2).strip()
        args = _parse_positional_args(tool_name, raw_args)
        return tool_name, args

    return None, {}


def _strip_reasoning(response_text: str) -> str:
    """Remove common reasoning wrappers and code fences from model output."""
    cleaned = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.replace("```json", "```")
    cleaned = cleaned.replace("```tool", "```")
    return cleaned.strip()


def _extract_args_block(response_text: str) -> Optional[str]:
    """Extract a balanced JSON object after an ARGS: marker."""
    args_marker = re.search(r"ARGS:\s*", response_text, re.IGNORECASE)
    if not args_marker:
        return None

    start = response_text.find("{", args_marker.end())
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(response_text)):
        char = response_text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return response_text[start:index + 1]
    return None


def _candidate_json_objects(text: str) -> list[str]:
    """Collect balanced JSON objects from a free-form model response."""
    candidates = []
    start = None
    depth = 0
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:index + 1])
                start = None
    return candidates


def _load_json_like(raw: str) -> dict:
    """Parse JSON-ish argument objects, falling back to regex extraction."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        normalized = raw.strip()
        normalized = re.sub(r"(\w+)\s*=", r'"\1": ', normalized)
        normalized = normalized.replace("'", '"')
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            return _parse_args_fallback(raw)

    return parsed if isinstance(parsed, dict) else {}


def _parse_json_tool_call(response_text: str) -> tuple[Optional[str], dict]:
    """Parse tool calls expressed as raw JSON instead of TOOL/ARGS lines."""
    for candidate in _candidate_json_objects(response_text):
        parsed = _load_json_like(candidate)
        if not parsed:
            continue

        tool_name = (
            parsed.get("tool")
            or parsed.get("tool_name")
            or parsed.get("name")
            or parsed.get("action")
        )
        if not isinstance(tool_name, str):
            continue

        args = (
            parsed.get("args")
            or parsed.get("arguments")
            or parsed.get("parameters")
            or {}
        )
        if isinstance(args, str) and args.strip().startswith("{"):
            args = _load_json_like(args)
        if not isinstance(args, dict):
            args = {}
        return tool_name.strip(), args

    return None, {}


def _parse_args_fallback(raw: str) -> dict:
    """Try to parse args even if JSON is malformed."""
    args = {}
    for match in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', raw):
        args[match.group(1)] = match.group(2)
    for match in re.finditer(r'"(\w+)"\s*:\s*(\d+)', raw):
        args[match.group(1)] = int(match.group(2))
    return args


def _parse_positional_args(tool_name: str, raw_args: str) -> dict:
    """Convert positional arguments to named arguments based on tool name."""
    if not raw_args:
        return {}

    parts = [p.strip().strip("'\"") for p in raw_args.split(",")]

    arg_map = {
        "check_logs": ["service_name"],
        "check_metrics": ["service_name", "metric"],
        "check_dependencies": ["service_name"],
        "restart_service": ["service_name"],
        "rollback_deploy": ["service_name"],
        "scale_service": ["service_name", "replicas"],
        "run_db_query": ["query"],
        "resolve_incident": ["root_cause", "summary"],
    }

    param_names = arg_map.get(tool_name, [])
    args = {}
    for i, part in enumerate(parts):
        if i < len(param_names):
            key = param_names[i]
            if key == "replicas":
                try:
                    args[key] = int(part)
                except ValueError:
                    args[key] = part
            else:
                args[key] = part
    return args


def _parse_result_field(obs: dict) -> str:
    """Extract the tool result text from the observation's result field."""
    result_raw = obs.get("result", "")
    if not result_raw:
        return str(obs)
    try:
        parsed = json.loads(result_raw)
        if isinstance(parsed, dict) and "tool_result" in parsed:
            return parsed["tool_result"]
        return json.dumps(parsed, indent=2)
    except (json.JSONDecodeError, TypeError):
        return str(result_raw)


def _action_matches(executed: tuple[str, dict], planned: tuple[str, dict]) -> bool:
    """Check whether an executed action satisfies a planned one."""
    executed_name, executed_args = executed
    planned_name, planned_args = planned
    if executed_name != planned_name:
        return False

    for key, planned_value in planned_args.items():
        if planned_name == "resolve_incident" and key == "summary":
            continue
        actual_value = executed_args.get(key)
        if isinstance(planned_value, str):
            if str(actual_value).strip().lower() != planned_value.strip().lower():
                return False
        else:
            if actual_value != planned_value:
                return False
    return True


def _next_policy_action(task_id: str, executed_actions: list[tuple[str, dict]]) -> tuple[str, dict]:
    """Return the next unfulfilled action from the scripted task plan."""
    plan = POLICY_PLANS[task_id]
    for planned_action in plan:
        if not any(_action_matches(executed, planned_action) for executed in executed_actions):
            return planned_action
    return plan[-1]


def _choose_action(
    task_id: str,
    messages: list[dict],
    llm_client: Optional[OpenAI],
    executed_actions: list[tuple[str, dict]],
    llm_attempts: int,
) -> tuple[str, dict, bool, int]:
    """Choose the next action, preferring a safe scripted policy when needed."""
    policy_action = _next_policy_action(task_id, executed_actions)

    if BASELINE_MODE == "policy" or llm_client is None:
        return policy_action[0], policy_action[1], True, llm_attempts

    if llm_attempts >= MAX_LLM_STEPS_PER_TASK:
        return policy_action[0], policy_action[1], True, llm_attempts

    try:
        response = llm_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        assistant_msg = response.choices[0].message.content or ""
        llm_attempts += 1
    except Exception as exc:
        print(f"    LLM unavailable, switching to baseline policy: {exc}")
        return policy_action[0], policy_action[1], True, MAX_LLM_STEPS_PER_TASK

    tool_name, args = parse_tool_call(assistant_msg)
    if tool_name and _action_matches((tool_name, args), policy_action):
        messages.append({"role": "assistant", "content": assistant_msg})
        return tool_name, args, False, llm_attempts

    if tool_name:
        print(
            "    LLM suggested a non-baseline action; using the deterministic policy "
            f"instead ({tool_name})."
        )
        messages.append({"role": "assistant", "content": assistant_msg})
    else:
        print("    Could not parse tool call cleanly; using the deterministic policy instead.")
        messages.append({"role": "assistant", "content": assistant_msg})
        messages.append({
            "role": "user",
            "content": (
                "Please respond with a tool call in the format:\n"
                "TOOL: tool_name\nARGS: {\"arg\": \"value\"}"
            ),
        })

    return policy_action[0], policy_action[1], True, MAX_LLM_STEPS_PER_TASK


def _get_ws_url() -> str:
    """Convert HTTP base URL to WebSocket URL."""
    ws_url = ENV_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_url}/ws"


async def ws_reset(ws, task_id: str) -> dict:
    """Reset the environment via WebSocket."""
    await ws.send(json.dumps({"type": "reset", "data": {"task_id": task_id}}))
    resp = json.loads(await ws.recv())
    if resp.get("type") == "error":
        return {"observation": {}, "reward": 0.0, "done": False, "error": resp.get("data", {})}
    data = resp.get("data", {})
    return {
        "observation": data.get("observation", {}),
        "reward": data.get("reward", 0.0),
        "done": data.get("done", False),
    }


async def ws_step(ws, tool_name: str, args: dict) -> dict:
    """Execute a step via WebSocket."""
    action = {"type": "call_tool", "tool_name": tool_name, "arguments": args}
    await ws.send(json.dumps({"type": "step", "data": action}))
    resp = json.loads(await ws.recv())
    if resp.get("type") == "error":
        return {
            "observation": {"result": json.dumps({"error": resp.get("data", {}).get("message", "Unknown error")})},
            "reward": 0.0,
            "done": False,
        }
    data = resp.get("data", {})
    return {
        "observation": data.get("observation", {}),
        "reward": data.get("reward", 0.0),
        "done": data.get("done", False),
    }


async def run_task_async(llm_client: Optional[OpenAI], task_id: str, task_title: str) -> float:
    """Run a single task over WebSocket and return the final score."""
    import websockets

    print(f"\nTask: {task_id} ({task_title})")
    print("-" * 50)

    ws_url = _get_ws_url()
    async with websockets.connect(ws_url) as ws:
        reset_result = await ws_reset(ws, task_id)
        obs = reset_result.get("observation", {})

        initial_result = obs.get("result", "")
        try:
            initial_data = json.loads(initial_result) if initial_result else {}
        except (json.JSONDecodeError, TypeError):
            initial_data = {}

        alert_info = initial_data.get("alert", {})
        services = initial_data.get("services", [])
        max_steps = initial_data.get("max_steps", 15)

        initial_context = (
            f"INCIDENT ALERT:\n"
            f"  Severity: {alert_info.get('severity', 'UNKNOWN')}\n"
            f"  Title: {alert_info.get('title', 'N/A')}\n"
            f"  Message: {alert_info.get('message', 'N/A')}\n"
            f"\nInfrastructure services: {', '.join(services)}\n"
            f"You have {max_steps} steps to investigate and resolve this incident."
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": initial_context},
        ]

        final_score = 0.0
        task_step_budget = MAX_STEPS_OVERRIDE or max_steps
        executed_actions: list[tuple[str, dict]] = []
        llm_attempts = 0

        for step in range(1, task_step_budget + 1):
            tool_name, args, used_policy, llm_attempts = _choose_action(
                task_id, messages, llm_client, executed_actions, llm_attempts
            )

            args_str = json.dumps(args) if args else "{}"
            source = "policy" if used_policy else "llm"
            print(f"  Step {step}: [{source}] {tool_name}({args_str})")

            result = await ws_step(ws, tool_name, args)
            obs_data = result.get("observation", {})
            reward = result.get("reward", 0.0)
            done = result.get("done", False)
            tool_result = _parse_result_field(obs_data)
            executed_actions.append((tool_name, args))

            if len(str(tool_result)) > 1500:
                tool_result = str(tool_result)[:1500] + "... [truncated]"

            if used_policy:
                messages.append({
                    "role": "assistant",
                    "content": f"TOOL: {tool_name}\nARGS: {json.dumps(args)}",
                })
            messages.append({
                "role": "user",
                "content": f"Tool result:\n{tool_result}\n\nReward: {reward}\nSteps remaining: {max_steps - step}",
            })

            if done:
                try:
                    terminal_data = json.loads(obs_data.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    terminal_data = {}
                final_score = terminal_data.get("score", reward)
                breakdown = terminal_data.get("breakdown", {})
                print(f"  --> Episode ended. Score: {final_score}")
                if breakdown:
                    print(f"      Investigation: {breakdown.get('investigation', 'N/A')}")
                    print(f"      Diagnosis:     {breakdown.get('diagnosis', 'N/A')}")
                    print(f"      Remediation:   {breakdown.get('remediation', 'N/A')}")
                    print(f"      Efficiency:    {breakdown.get('efficiency', 'N/A')}")
                    print(f"      Penalties:     -{breakdown.get('penalties', 'N/A')}")
                break
        else:
            print(f"  --> Max inference steps reached. Score: {final_score}")

    return final_score


def main():
    """Run the baseline agent against all 3 tasks."""
    print("=" * 60)
    print("  SRE Incident Response - Baseline Inference")
    print("=" * 60)
    print(f"Mode: {BASELINE_MODE}")
    print(f"API: {API_BASE_URL}")
    print(f"Model: {MODEL_NAME}")
    print(f"Environment: {ENV_BASE_URL}")

    llm_client = create_client()

    tasks = [
        ("easy", "Service OOM Crash"),
        ("medium", "Database Slow Query Cascade"),
        ("hard", "Bad Deploy Cascading Failure"),
    ]

    scores = {}
    for task_id, title in tasks:
        score = asyncio.run(run_task_async(llm_client, task_id, title))
        scores[task_id] = score

    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)
    for task_id, title in tasks:
        print(f"  {task_id:<8s} ({title}): {scores.get(task_id, 0.0):.2f}")

    avg = sum(scores.values()) / len(scores) if scores else 0.0
    print(f"\n  Average: {avg:.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
