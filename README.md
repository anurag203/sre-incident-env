---
title: SRE Incident Response Environment
emoji: "\U0001F6A8"
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 8000
tags:
  - openenv
---

# SRE Incident Response Environment

An OpenEnv-compliant agentic execution environment that simulates real-world DevOps on-call incidents. AI agents must investigate infrastructure alerts, diagnose root causes, and execute remediation actions across a simulated microservices architecture.

## Motivation

Site Reliability Engineering (SRE) on-call incident response is one of the most critical, high-stakes tasks in modern software operations. Engineers must rapidly triage alerts, correlate signals across multiple services, identify root causes amid noise and red herrings, and apply precise fixes under time pressure.

This environment captures that workflow as a structured RL task, enabling the training and evaluation of AI agents for autonomous incident response.

## Action Space (MCP Tools)

| Tool | Type | Description |
|------|------|-------------|
| `check_logs(service_name)` | Investigation | Retrieve recent log entries for a service |
| `check_metrics(service_name, metric)` | Investigation | Query CPU, memory, latency, error rate, or connections. Friendly aliases and canonical metric keys are both supported |
| `check_dependencies(service_name)` | Investigation | View upstream/downstream service dependencies |
| `check_alert_details()` | Investigation | Get full incident alert context |
| `list_services()` | Investigation | List all services with current health status |
| `restart_service(service_name)` | Remediation | Restart a crashed or degraded service |
| `rollback_deploy(service_name)` | Remediation | Revert to the previous deployment version |
| `scale_service(service_name, replicas)` | Remediation | Scale service replica count |
| `run_db_query(query)` | Remediation | Execute a database administrative command |
| `resolve_incident(root_cause, summary)` | Terminal | Submit diagnosis and end the episode |

## Observation Space

Each observation is a `CallToolObservation` with:
- **result** (str): JSON-encoded tool output with step metadata
- **tool_name** (str): Name of the tool that was called
- **done** (bool): Whether the episode has ended
- **reward** (float): Reward signal for this step

The environment state is tracked via a typed `SREState` that includes:
- current `task_id` and `task_title`
- `services_investigated`
- `metrics_checked`
- `remediation_actions_taken`
- `diagnosis_submitted`
- `episode_done`
- `final_score`

## Tasks

### Task 1: Easy - Service OOM Crash
- **Services**: 3 (order-service, db-primary, api-gateway)
- **Root cause**: order-service crashed due to OutOfMemoryError
- **Fix**: Restart the crashed service
- **Max steps**: 10

### Task 2: Medium - Database Slow Query Cascade
- **Services**: 5 (api-gateway, user-service, payment-service, db-primary, cache-redis)
- **Root cause**: Unindexed slow query on db-primary causing cascading latency
- **Fix**: Kill the blocking query and create an index
- **Red herring**: cache-redis shows eviction warnings (unrelated)
- **Max steps**: 15

### Task 3: Hard - Bad Deploy Cascading Failure
- **Services**: 7 (auth-service, user-service, order-service, payment-service, notification-service, db-primary, api-gateway)
- **Root cause**: Broken deploy to auth-service causing 401 errors across all downstream services
- **Fix**: Rollback auth-service, then restart 3 dependent services
- **Red herrings**: Downstream errors, db-primary connection pool warnings
- **Max steps**: 20

### Task 4: Medium - Cache Layer Collapse
- **Services**: 5 (cache-redis, db-primary, user-service, order-service, api-gateway)
- **Root cause**: cache-redis OOM crash forces all services to hit db-primary directly, overwhelming it
- **Fix**: Restart cache-redis to restore the cache layer
- **Red herring**: db-primary shows 95% CPU and near-full connection pool (symptom, not cause)
- **Max steps**: 12

### Task 5: Hard - Memory Leak from Bad Deploy
- **Services**: 6 (user-service, order-service, payment-service, db-primary, notification-service, api-gateway)
- **Root cause**: Deploy v3.1.0 to user-service introduced an unbounded HashMap that leaked memory until OOM killed the process
- **Fix**: Rollback user-service, restart order-service and payment-service
- **Red herrings**: db-primary shows connection churn but is healthy; api-gateway and notification-service are degraded but auto-recover once upstream is fixed
- **Max steps**: 18

## Reward Function

| Component | Weight | Description |
|-----------|--------|-------------|
| Investigation | 0.0-0.3 | Did the agent check relevant services and metrics? |
| Diagnosis | 0.0-0.4 | Did the agent correctly identify the root cause? |
| Remediation | 0.0-0.2 | Did the agent apply the correct fix? |
| Efficiency | 0.0-0.1 | Bonus for solving with fewer steps |
| Penalties | 0.0-0.15 | Deduction for reckless actions on healthy services |

## Setup

### Local Development

```bash
uv sync
uv run uvicorn server.app:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t sre-incident-env .
docker run -p 8000:8000 sre-incident-env
```

### Validate

```bash
openenv validate .
openenv validate --url http://localhost:8000
pytest
./scripts/pre_submit.sh
```

`./scripts/pre_submit.sh` runs the full local readiness loop: tests, package validation, server validation, deterministic inference smoke test, and Docker build. Set `SKIP_DOCKER=1` if you want a faster check while iterating.

### Deploy to HF Spaces

Live deployment: [https://huggingface.co/spaces/anurag203/sre-incident-env](https://huggingface.co/spaces/anurag203/sre-incident-env)

```bash
huggingface-cli login
openenv push . --repo-id YOUR_USERNAME/sre-incident-env
```

### Running Inference

```bash
export API_BASE_URL="https://router.huggingface.co/v1"
export MODEL_NAME="your-model-name"
export HF_TOKEN="your-hf-token"
# OPENAI_API_KEY is also accepted
export ENV_BASE_URL="http://localhost:8000"
export BASELINE_MODE="hybrid"   # hybrid | llm | policy
# export OPENENV_INSECURE_SKIP_VERIFY=1  # optional local SSL workaround
python inference.py
```

## Baseline Scores

`inference.py` ships as a hybrid baseline: it uses the OpenAI-compatible client when the provider is available and falls back to a deterministic recovery policy if the model output is malformed or the provider errors. That keeps the baseline reproducible while still satisfying the required client integration.

### Measured Hybrid Baseline Scores

Measured on `2026-03-28` against a local server using `deepseek-ai/DeepSeek-R1:fastest` through the Hugging Face Router.

| Task | Score | Diagnosis | Investigation | Remediation | Efficiency |
|------|-------|-----------|---------------|-------------|------------|
| Easy | 0.89 | 0.40 | 0.25 | 0.20 | 0.04 |
| Medium | 0.92 | 0.40 | 0.25 | 0.20 | 0.07 |
| Hard | 0.92 | 0.40 | 0.25 | 0.20 | 0.07 |
| Cache Failure | 0.89 | 0.40 | 0.25 | 0.20 | 0.04 |
| Memory Leak | 0.97 | 0.40 | 0.30 | 0.20 | 0.07 |
| **Average** | **0.92** | | | | |

*If you want a pure model-only run for comparison, set `BASELINE_MODE=llm`. If you want a fully deterministic reproducibility check, set `BASELINE_MODE=policy`.*

## Submission Notes

- The HF Space is configured for Docker Spaces and tagged with `openenv`.
- The local pre-submit script uses `BASELINE_MODE=policy` so you can always verify the baseline end-to-end, even if provider credits are exhausted.
- For leaderboard-quality reporting, rerun `inference.py` with your target provider credentials and keep the measured output in your final submission notes.

## Validation Results

- `openenv validate .` -- All deployment modes supported (docker, openenv_serve, uv_run, python_module)
- `openenv validate --url http://localhost:8000` -- 6/6 criteria passed (health, metadata, schema, MCP, mode consistency)
- `pytest` -- environment, scoring, and scenario consistency checks

## License

MIT
