# Submission Checklist

Submit the contents of this folder as the project root:

- `/Users/anuagar2/Desktop/practice/hackathon_proj/sre_incident_env`

Do not submit the parent folder `hackathon_proj`, because the automated checks expect these files at the repository root:

- `inference.py`
- `openenv.yaml`
- `Dockerfile`
- `README.md`
- `pyproject.toml`
- `server/`
- `scenarios/`

## Final Local Check

Run this from the submission root:

```bash
cd /Users/anuagar2/Desktop/practice/hackathon_proj/sre_incident_env
./scripts/pre_submit.sh
```

If you want a faster repeat check while iterating:

```bash
SKIP_DOCKER=1 ./scripts/pre_submit.sh
```

To create a clean upload folder from this nested workspace:

```bash
./scripts/package_submission.sh
```

By default that creates:

- `/Users/anuagar2/Desktop/practice/hackathon_proj/sre_incident_env_submission`

## What Must Be True Before Submit

- `pytest -q` passes
- `openenv validate .` passes
- `./scripts/pre_submit.sh` passes
- Hugging Face Space is updated with the latest local files
- The Space responds on `/health` and `/reset`
- No secrets are committed or left in `.env`

## Inference Notes

- `inference.py` supports `BASELINE_MODE=hybrid`, `BASELINE_MODE=llm`, and `BASELINE_MODE=policy`
- For guaranteed reproducibility during validation, `scripts/pre_submit.sh` uses `BASELINE_MODE=policy`
- For final reporting, you can optionally rerun with `BASELINE_MODE=llm` once provider credits are available

## Push Reminder

If you are pushing this as a separate repo or upload bundle, make sure the top-level tree looks like:

```text
Dockerfile
README.md
inference.py
openenv.yaml
pyproject.toml
server/
scenarios/
tests/
scripts/
```
