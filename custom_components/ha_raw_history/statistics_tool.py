"""GetStatistics tool for Home Assistant LLM integration.

Wraps HA's internal recorder.get_statistics API for numeric aggregate
queries (min, max, mean). Use this for questions like:
- "What's the coldest the bedroom has ever been?"
- "What was the highest temperature yesterday?"
- "What's the average power usage this week?"

Only works for entities with state_class=measurement or state_class=total_increasing
(sensors, power meters, temperature sensors, etc.). For state-based entities
(covers, switches, locks) use GetRawHistory instead.
"""

from __future__ import annotations

from datetime import datetime as dt, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.llm import Tool, ToolInput, LLMContext
from homeassistant.util import dt as dt_util

from .const import TOOL_GET_STATISTICS

import logging

LOGGER = logging.getLogger(__name__)

PERIOD_CHOICES = ["5minute", "hour", "day", "week", "month", "year"]

STATISTIC_TYPES = ["min", "max", "mean", "sum", "state"]


class GetStatisticsTool(Tool):
    """Tool that retrieves numeric aggregate statistics for sensor entities.

    Uses HA's internal recorder statistics API directly. Returns pre-computed
    min, max, mean values for entities with state_class=measurement.
    Perfect for temperature, humidity, power, and other numeric sensors.
    """

    name: str = TOOL_GET_STATISTICS
    description: str = (
        "Get numeric aggregate statistics (min, max, mean, sum) for a sensor "
        "entity over a time period. Use this for questions like 'what is the "
        "coldest the bedroom has ever been', 'what was the highest temperature "
        "yesterday', or 'what is the average power usage this week'. "
        "Works for temperature, humidity, power, and other numeric sensors. "
        "Does NOT work for state-based entities like covers or switches - "
        "use GetRawHistory for those."
    )
    parameters: vol.Schema = vol.Schema(
        {
            vol.Required(
                "entity_id",
                description="Entity ID to query (e.g. 'sensor.bedroom_bluetooth_temperature_temperature')"
            ): str,
            vol.Optional(
                "statistic",
                description="Type of statistic to return: 'min' (minimum value), "
                            "'max' (maximum value), 'mean' (average value), "
                            "'all' (all of the above). Default: 'all'.",
                default="all",
            ): str,
            vol.Optional(
                "period",
                description="Time period for aggregation: 'hour' (hourly), "
                            "'day' (daily), 'week' (weekly), 'month' (monthly), "
                            "'year' (yearly). Default: 'day'.",
                default="day",
            ): str,
            vol.Optional(
                "start_time",
                description="Start of time range (ISO format, e.g. '2026-01-01T00:00:00'). "
                            "Defaults to 30 days ago.",
                default="",
            ): str,
            vol.Optional(
                "end_time",
                description="End of time range (ISO format, e.g. '2026-06-26T00:00:00'). "
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
        """Execute the tool - fetch statistics from the recorder."""
        entity_id: str = tool_input.tool_args["entity_id"]
        stat_type: str = tool_input.tool_args.get("statistic", "all").lower()
        period: str = tool_input.tool_args.get("period", "day").lower()
        start_time_str: str = tool_input.tool_args.get("start_time", "")
        end_time_str: str = tool_input.tool_args.get("end_time", "")

        LOGGER.debug(
            "GetStatistics called: entity_id=%s, statistic=%s, period=%s, "
            "start=%s, end=%s",
            entity_id, stat_type, period, start_time_str, end_time_str
        )

        # Validate period
        if period not in PERIOD_CHOICES:
            return {
                "success": False,
                "error": f"Invalid period '{period}'. Must be one of: "
                         f"{', '.join(PERIOD_CHOICES)}.",
            }

        # Validate entity_id format
        if not isinstance(entity_id, str) or "." not in entity_id:
            return {
                "success": False,
                "error": f"Invalid entity_id format: '{entity_id}'.",
            }

        now = dt_util.utcnow()

        # Parse or default start_time
        if start_time_str:
            start_time = dt_util.parse_datetime(start_time_str)
            if start_time is None:
                return {
                    "success": False,
                    "error": f"Invalid start_time format: '{start_time_str}'.",
                }
            start_time = dt_util.as_utc(start_time)
        else:
            start_time = now - timedelta(days=30)

        # Parse or default end_time
        if end_time_str:
            end_time = dt_util.parse_datetime(end_time_str)
            if end_time is None:
                return {
                    "success": False,
                    "error": f"Invalid end_time format: '{end_time_str}'.",
                }
            end_time = dt_util.as_utc(end_time)
        else:
            end_time = now

        if start_time >= end_time:
            return {"success": False, "error": "start_time must be before end_time."}

        # Determine which statistic types to fetch
        if stat_type == "all":
            types = {"min", "max", "mean", "sum"}
        else:
            types = {stat_type}

        invalid_types = types - set(STATISTIC_TYPES) - {"sum"}
        if invalid_types:
            return {
                "success": False,
                "error": f"Invalid statistic type(s): {invalid_types}. "
                         f"Must be one of: min, max, mean, sum, state, all.",
            }

        try:
            instance = get_instance(hass)
            result = await instance.async_add_executor_job(
                statistics_during_period,
                hass,
                start_time,
                end_time,
                [entity_id],
                period,
                {},  # units (empty = default / no conversion)
                types,
            )
        except Exception as err:
            return {
                "success": False,
                "error": f"Failed to fetch statistics: {err}",
            }

        rows = result.get(entity_id, [])

        if not rows:
            return {
                "success": True,
                "entity_id": entity_id,
                "count": 0,
                "statistics": [],
                "message": f"No statistics found for {entity_id}. "
                           f"This entity may not have state_class=measurement "
                           f"or may not have been recorded long enough.",
            }

        # Format statistics
        stats_formatted = []
        for r in rows:
            entry = {"period_start": r.get("start", ""),
                     "period_end": r.get("end", "")}
            if "min" in r and r["min"] is not None:
                entry["min"] = r["min"]
            if "max" in r and r["max"] is not None:
                entry["max"] = r["max"]
            if "mean" in r and r["mean"] is not None:
                entry["mean"] = r["mean"]
            if "sum" in r and r["sum"] is not None:
                entry["sum"] = r["sum"]
            stats_formatted.append(entry)

        # Compute overall aggregates across all periods
        overall = {}
        if stat_type == "all" or stat_type == "min":
            vals = [r["min"] for r in rows if r.get("min") is not None]
            if vals:
                overall["min"] = min(vals)
        if stat_type == "all" or stat_type == "max":
            vals = [r["max"] for r in rows if r.get("max") is not None]
            if vals:
                overall["max"] = max(vals)
        if stat_type == "all" or stat_type == "mean":
            vals = [r["mean"] for r in rows if r.get("mean") is not None]
            if vals:
                overall["mean"] = sum(vals) / len(vals)
        if stat_type == "all" or stat_type == "sum":
            vals = [r["sum"] for r in rows if r.get("sum") is not None]
            if vals:
                overall["sum"] = sum(vals)

        return {
            "success": True,
            "entity_id": entity_id,
            "period": period,
            "count": len(stats_formatted),
            "statistics": stats_formatted,
            "overall": overall,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
