"""
FastAPI application for the SRE Incident Response Environment.

Usage:
    uvicorn server.app:app --reload --host 0.0.0.0 --port 8000
"""

try:
    from openenv.core.env_server.http_server import create_app
    from openenv.core.env_server.mcp_types import CallToolAction, CallToolObservation

    from .incident_environment import IncidentEnvironment
except ImportError:
    from openenv.core.env_server.http_server import create_app
    from openenv.core.env_server.mcp_types import CallToolAction, CallToolObservation

    from server.incident_environment import IncidentEnvironment

app = create_app(
    IncidentEnvironment,
    CallToolAction,
    CallToolObservation,
    env_name="sre_incident_env",
)


@app.get("/")
def root():
    return {"env": "sre_incident_env", "status": "running"}


def main():
    """Entry point for direct execution via uv run or python -m."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
