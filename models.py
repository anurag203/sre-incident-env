"""
Data models for the SRE Incident Response Environment.

This environment uses MCP (Model Context Protocol) for tool-based interactions.
The agent discovers tools via ListToolsAction and invokes them via CallToolAction.
"""

from typing import Optional

from pydantic import Field

from openenv.core.env_server.mcp_types import (
    CallToolAction,
    CallToolObservation,
    ListToolsAction,
    ListToolsObservation,
)
from openenv.core.env_server.types import Action, Observation, State

SREAction = CallToolAction
SREObservation = CallToolObservation


class SREState(State):
    """Extended state for SRE Incident Response episodes."""

    task_id: str = Field(default="", description="Current task identifier (easy/medium/hard)")
    task_title: str = Field(default="", description="Human-readable title for the current task")
    services_investigated: list[str] = Field(
        default_factory=list,
        description="Services the agent has investigated so far",
    )
    metrics_checked: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Service/metric pairs inspected during the episode",
    )
    remediation_actions_taken: int = Field(
        default=0,
        description="Number of remediation actions taken",
    )
    diagnosis_submitted: Optional[str] = Field(
        default=None,
        description="Most recent diagnosis submitted by the agent",
    )
    episode_done: bool = Field(default=False, description="Whether the episode has ended")
    final_score: Optional[float] = Field(
        default=None,
        description="Final graded score once the episode completes",
    )


__all__ = [
    "SREAction",
    "SREObservation",
    "SREState",
    "CallToolAction",
    "CallToolObservation",
    "ListToolsAction",
    "ListToolsObservation",
    "Action",
    "Observation",
    "State",
]
