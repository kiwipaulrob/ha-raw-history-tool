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
        "entity or ALL entities of a device_class over a time period. "
        "Use this for questions like 'what is the coldest room in the house', "
        "'what was the highest temperature yesterday', "
        "'what is the average power usage this week', "
        "'which room is the coldest', or 'what was the coldest the bedroom has ever been'. "
        "Provide either an entity_id (specific sensor) or a device_class "
        "(compare all sensors of that type, e.g. 'temperature', 'humidity', 'power'). "
        "Works for temperature, humidity, power, and other numeric sensors. "
        "Does NOT work for state-based entities like covers or switches - "
        "use GetRawHistory for those."
    )
    parameters: vol.Schema = vol.Schema(
        {
            vol.Optional(
                "entity_id",
                description="Entity ID to query (e.g. 'sensor.bedroom_bluetooth_temperature_temperature'). "
                            "Alternative to device_class - provide one or the other.",
                default="",
            ): str,
            vol.Optional(
                "device_class",
                description="Device class to query all matching entities (e.g. 'temperature', 'humidity', 'power'). "
                            "Finds all entities with this device_class and compares their statistics. "
                            "Alternative to entity_id - provide one or the other.",
                default="",
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
        entity_id: str = tool_input.tool_args.get("entity_id", "").strip()
        device_class: str = tool_input.tool_args.get("device_class", "").strip().lower()
        stat_type: str = tool_input.tool_args.get("statistic", "all").lower()
        period: str = tool_input.tool_args.get("period", "day").lower()
        start_time_str: str = tool_input.tool_args.get("start_time", "")
        end_time_str: str = tool_input.tool_args.get("end_time", "")

        LOGGER.debug(
            "GetStatistics called: entity_id=%s, device_class=%s, "
            "statistic=%s, period=%s, start=%s, end=%s",
            entity_id, device_class, stat_type, period,
            start_time_str, end_time_str
        )

        # Must provide exactly one of entity_id or device_class
        if not entity_id and not device_class:
            return {
                "success": False,
                "error": "Provide either 'entity_id' for a specific sensor, "
                         "or 'device_class' (e.g. 'temperature') to compare "
                         "all sensors of that type.",
            }
        if entity_id and device_class:
            return {
                "success": False,
                "error": "Provide either 'entity_id' or 'device_class', not both.",
            }

        # Validate period
        if period not in PERIOD_CHOICES:
            return {
                "success": False,
                "error": f"Invalid period '{period}'. Must be one of: "
                         f"{', '.join(PERIOD_CHOICES)}.",
            }

        # Validate entity_id format if provided
        if entity_id and ("." not in entity_id):
            return {
                "success": False,
                "error": f"Invalid entity_id format: '{entity_id}'.",
            }

        now = dt_util.utcnow()

        # Parse or default start_time
        if start_time_str:
            start_time = dt_util.parse_datetime(start_time_str)
            if start_time is None:
                return {"success": False,
                        "error": f"Invalid start_time format: '{start_time_str}'."}
            start_time = dt_util.as_utc(start_time)
        else:
            start_time = now - timedelta(days=30)

        # Parse or default end_time
        if end_time_str:
            end_time = dt_util.parse_datetime(end_time_str)
            if end_time is None:
                return {"success": False,
                        "error": f"Invalid end_time format: '{end_time_str}'."}
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

        # Discover entity_ids if device_class was provided
        entity_ids = []
        if device_class:
            for state in hass.states.async_all():
                attrs = state.attributes
                if attrs.get("device_class") == device_class and attrs.get("state_class") == "measurement":
                    entity_ids.append(state.entity_id)
            if not entity_ids:
                return {
                    "success": False,
                    "error": f"No entities found with device_class='{device_class}' "
                             f"and state_class='measurement'.",
                }
        else:
            entity_ids = [entity_id]

        try:
            instance = get_instance(hass)
            result = await instance.async_add_executor_job(
                statistics_during_period,
                hass,
                start_time,
                end_time,
                entity_ids,
                period,
                {},
                types,
            )
        except Exception as err:
            return {
                "success": False,
                "error": f"Failed to fetch statistics: {err}",
            }

        # Build response
        multi = len(entity_ids) > 1
        sensor_results = []

        for eid in entity_ids:
            rows = result.get(eid, [])
            if not rows:
                continue

            # Per-sensor overall aggregates
            overall = {}
            if "min" in types or stat_type == "all":
                vals = [r["min"] for r in rows if r.get("min") is not None]
                if vals:
                    overall["min"] = min(vals)
            if "max" in types or stat_type == "all":
                vals = [r["max"] for r in rows if r.get("max") is not None]
                if vals:
                    overall["max"] = max(vals)
            if "mean" in types or stat_type == "all":
                vals = [r["mean"] for r in rows if r.get("mean") is not None]
                if vals:
                    overall["mean"] = sum(vals) / len(vals)
            if "sum" in types or stat_type == "all":
                vals = [r["sum"] for r in rows if r.get("sum") is not None]
                if vals:
                    overall["sum"] = sum(vals)

            friendly = hass.states.get(eid)
            friendly_name = friendly.attributes.get("friendly_name", eid) if friendly else eid
            unit = friendly.attributes.get("unit_of_measurement", "") if friendly else ""

            if multi:
                sensor_results.append({
                    "entity_id": eid,
                    "name": friendly_name,
                    "unit": unit,
                    "overall": overall,
                    "period_count": len(rows),
                })
            else:
                # Single entity - return detailed format with per-period data
                stats_formatted = []
                for r in rows:
                    entry = {"period_start": r.get("start", ""),
                             "period_end": r.get("end", "")}
                    for t in types:
                        if t in r and r[t] is not None:
                            entry[t] = r[t]
                    stats_formatted.append(entry)

                return {
                    "success": True,
                    "entity_id": entity_id,
                    "name": friendly_name,
                    "unit": unit,
                    "period": period,
                    "count": len(stats_formatted),
                    "statistics": stats_formatted,
                    "overall": overall,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                }

        if not sensor_results:
            return {
                "success": True,
                "device_class": device_class,
                "count": 0,
                "sensors": [],
                "message": f"No statistics found for any {device_class} sensors.",
            }

        # Sort by min value ascending (coldest first for temperature)
        if "min" in types or stat_type == "all":
            sensor_results.sort(
                key=lambda x: x["overall"].get("min", 999) if "min" in x["overall"] else 999
            )

        return {
            "success": True,
            "device_class": device_class,
            "period": period,
            "comparison": True,
            "count": len(sensor_results),
            "sensors": sensor_results,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
