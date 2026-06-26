"""HA Raw History - Home Assistant Custom Integration.

REGISTERS:
  - A custom llm.API with ID "raw_history" providing the GetRawHistory tool
  - A ha_raw_history.get_history service for automation use

ARCHITECTURE:
  Unlike the old python_script + rest_command approach which required
  a Bearer token and HTTP loopback, this integration calls HA's internal
  recorder history API directly through the event loop executor. No
  tokens, no HTTP, no sandbox restrictions.

USAGE:
  1. Copy custom_components/ha_raw_history/ to your HA config directory
  2. Add 'ha_raw_history:' to configuration.yaml
  3. Restart Home Assistant
  4. Go to Settings > Devices & Services > [Your OpenAI/OpenRouter agent]
     > Configure > APIs to expose
  5. Select "Assist" + "Raw History" and save
  6. Test: "Was the curtain open at 3pm yesterday?"
"""

from __future__ import annotations

from datetime import datetime as dt, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import API_ID, API_NAME, DOMAIN, TOOL_GET_RAW_HISTORY, TOOL_GET_STATISTICS
from .history_tool import GetRawHistoryTool
from .statistics_tool import GetStatisticsTool

SERVICE_GET_HISTORY = "get_history"

SERVICE_GET_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.string,
        vol.Optional("start_time", default=""): cv.string,
        vol.Optional("end_time", default=""): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the HA Raw History integration.

    Registers:
    1. A custom llm.API providing the GetRawHistory tool for LLM agents
    2. A ha_raw_history.get_history service for automations
    """
    # 1. Register the LLM API so conversation agents can expose GetRawHistory
    api = RawHistoryAPI(hass=hass)
    llm.async_register_api(hass, api)

    # 2. Register a service for direct use from automations
    async def handle_get_history(call: ServiceCall) -> dict[str, Any]:
        """Handle get_history service call."""
        entity_id: str = call.data["entity_id"]
        start_time_str: str = call.data.get("start_time", "")
        end_time_str: str = call.data.get("end_time", "")

        now = dt_util.utcnow()

        # Parse or default start_time
        if start_time_str:
            start_time = dt_util.parse_datetime(start_time_str)
            if start_time is None:
                raise HomeAssistantError(
                    f"Invalid start_time format: '{start_time_str}'"
                )
            start_time = dt_util.as_utc(start_time)
        else:
            start_time = now - timedelta(hours=24)

        # Parse or default end_time
        if end_time_str:
            end_time = dt_util.parse_datetime(end_time_str)
            if end_time is None:
                raise HomeAssistantError(
                    f"Invalid end_time format: '{end_time_str}'"
                )
            end_time = dt_util.as_utc(end_time)
        else:
            end_time = now

        # Validate time range
        if start_time >= end_time:
            raise HomeAssistantError("start_time must be before end_time")

        # Fetch history via recorder executor
        instance = get_instance(hass)
        states = await instance.async_add_executor_job(
            get_significant_states,
            hass,
            start_time,
            end_time,
            [entity_id],
            None,   # filters
            True,   # include_start_time_state
            True,   # significant_changes_only
            True,   # minimal_response
            False,  # no_attributes
        )

        entity_states = states.get(entity_id, [])

        # Handle both State objects and dicts (minimal_response=True returns dicts)
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

        result = {
            "entity_id": entity_id,
            "count": len(state_changes),
            "state_changes": state_changes,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_HISTORY,
        handle_get_history,
        schema=SERVICE_GET_HISTORY_SCHEMA,
    )

    return True


class RawHistoryAPI(llm.API):
    """LLM API that exposes the GetRawHistory tool.

    Provides a GetRawHistory tool which queries HA's internal recorder
    history directly - no HTTP loopback, no Bearer tokens, no sandbox
    restrictions. Works for ALL entity types.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=API_ID, name=API_NAME)

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return an API instance with tools."""
        api_prompt = (
            "You have access to two history tools:\n"
            "1. GetRawHistory - retrieves raw state changes for any entity "
            "(covers, switches, locks, etc.). Returns every state change "
            "with timestamps. Use for questions like 'was the curtain open "
            "at 3pm yesterday?' or 'when did the front door unlock?'.\n"
            "2. GetStatistics - retrieves numeric aggregate statistics "
            "(min, max, mean) for sensor entities with state_class. "
            "Use for questions like 'what is the coldest the bedroom has "
            "ever been?' or 'what was the highest temperature today?'.\n\n"
            "IMPORTANT: For both tools, use the entity_id parameter with "
            "the full entity ID format (e.g. 'cover.curtain', "
            "'sensor.bedroom_bluetooth_temperature_temperature'). "
            "Do NOT use friendly names."
        )

        return llm.APIInstance(
            api=self,
            api_prompt=api_prompt,
            llm_context=llm_context,
            tools=[GetRawHistoryTool(), GetStatisticsTool()],
        )
