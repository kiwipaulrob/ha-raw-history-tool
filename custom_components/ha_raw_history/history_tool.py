"""GetRawHistory tool for Home Assistant LLM integration.

Calls HA's internal recorder history API directly, avoiding any HTTP
loopback or token-based authentication. Works for ALL entity types:
covers, switches, sensors, locks, presence trackers, climate, etc.
"""

from __future__ import annotations

from datetime import datetime as dt, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.llm import Tool, ToolInput, LLMContext
from homeassistant.util import dt as dt_util

from .const import TOOL_GET_RAW_HISTORY

import logging

LOGGER = logging.getLogger(__name__)


class GetRawHistoryTool(Tool):
    """Tool that retrieves raw state history for any entity.

    Uses HA's internal recorder API directly - no HTTP calls, no tokens.
    Returns the complete state change log for the requested entity.
    """

    name: str = TOOL_GET_RAW_HISTORY
    description: str = (
        "Get the raw state history for an entity over a time period. "
        "Returns every state change (state, timestamp) for the requested entity. "
        "Use this to answer questions like 'was the curtain open at 3pm yesterday?' "
        "or 'when did the temperature drop below 20 degrees?'. "
        "Works for ALL entity types: covers, switches, sensors, lights, locks, climate."
    )
    parameters: vol.Schema = vol.Schema(
        {
            vol.Required("entity_id", description="Entity ID to query (e.g. 'cover.curtain')"): str,
            vol.Optional(
                "start_time",
                description="Start of time range (ISO format, e.g. '2026-06-24T12:00:00'). "
                            "Defaults to 24 hours ago.",
                default="",
            ): str,
            vol.Optional(
                "end_time",
                description="End of time range (ISO format, e.g. '2026-06-25T12:00:00'). "
                            "Defaults to now.",
                default="",
            ): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> dict[str, Any]:
        """Execute the tool - fetch entity history from the recorder."""
        entity_id: str = tool_input.tool_args["entity_id"]
        start_time_str: str = tool_input.tool_args.get("start_time", "")
        end_time_str: str = tool_input.tool_args.get("end_time", "")

        LOGGER.debug(
            "GetRawHistory called: entity_id=%s, start_time=%s, end_time=%s",
            entity_id, start_time_str, end_time_str
        )

        now = dt_util.utcnow()

        # Parse or default start_time
        if start_time_str:
            start_time = dt_util.parse_datetime(start_time_str)
            if start_time is None:
                return {
                    "success": False,
                    "error": f"Invalid start_time format: '{start_time_str}'. "
                             f"Use ISO format like '2026-06-24T12:00:00'.",
                }
            start_time = dt_util.as_utc(start_time)
        else:
            start_time = now - timedelta(hours=24)

        # Parse or default end_time
        if end_time_str:
            end_time = dt_util.parse_datetime(end_time_str)
            if end_time is None:
                return {
                    "success": False,
                    "error": f"Invalid end_time format: '{end_time_str}'. "
                             f"Use ISO format like '2026-06-25T12:00:00'.",
                }
            end_time = dt_util.as_utc(end_time)
        else:
            end_time = now

        # Validate time range
        if start_time >= end_time:
            return {
                "success": False,
                "error": "start_time must be before end_time.",
            }

        # Validate entity_id format
        if not isinstance(entity_id, str) or "." not in entity_id:
            return {
                "success": False,
                "error": f"Invalid entity_id format: '{entity_id}'. "
                         f"Must be in format 'domain.name' (e.g. 'cover.curtain').",
            }

        try:
            # Fetch history via recorder executor (avoids blocking the event loop)
            instance = get_instance(hass)
            states = await instance.async_add_executor_job(
                get_significant_states,
                hass,
                start_time,
                end_time,
                [entity_id],
                None,  # filters
                True,  # include_start_time_state
                True,  # significant_changes_only
                True,  # minimal_response
                False, # no_attributes
            )
        except Exception as err:
            return {
                "success": False,
                "error": f"Failed to fetch history: {err}",
            }

        # Format the results
        entity_states = states.get(entity_id, [])

        if not entity_states:
            return {
                "success": True,
                "entity_id": entity_id,
                "count": 0,
                "state_changes": [],
                "message": f"No state changes found for {entity_id} "
                           f"between {start_time.isoformat()} and {end_time.isoformat()}.",
            }

        # Format state changes for the LLM
        # Handle both State objects (no_attributes=False) and
        # dicts (minimal_response=True)
        state_changes = []
        for s in entity_states:
            if isinstance(s, dict):
                state = s.get("state", "unknown")
                last_changed = s.get("last_changed")
                if last_changed:
                    ts = dt_util.parse_datetime(last_changed)
                    if ts:
                        ts = dt_util.as_local(ts)
                    else:
                        ts = last_changed
                else:
                    ts = "unknown"
                if isinstance(ts, dt):
                    ts = ts.isoformat()
            else:
                state = s.state
                ts = dt_util.as_local(
                    s.last_changed if s.last_changed else s.last_updated
                ).isoformat()
            state_changes.append({"state": state, "timestamp": ts})

        return {
            "success": True,
            "entity_id": entity_id,
            "count": len(state_changes),
            "state_changes": state_changes,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
