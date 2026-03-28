"""
SRE Incident Response Environment Client.

Provides the client for connecting to an SRE Incident Response Environment server.
Extends MCPToolClient for tool-calling style interactions.

Example:
    >>> with SREIncidentEnv(base_url="http://localhost:8000") as env:
    ...     env.reset()
    ...     tools = env.list_tools()
    ...     result = env.call_tool("check_logs", service_name="order-service")
    ...     print(result)
"""

from openenv.core.mcp_client import MCPToolClient


class SREIncidentEnv(MCPToolClient):
    """
    Client for the SRE Incident Response Environment.

    Inherits all functionality from MCPToolClient:
    - list_tools(): Discover available investigation and remediation tools
    - call_tool(name, **kwargs): Call a tool by name
    - reset(**kwargs): Reset the environment (pass task_id="easy"|"medium"|"hard")
    - step(action): Execute an action (for advanced use)
    """

    pass
