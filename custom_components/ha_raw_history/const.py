"""Constants for the HA Raw History integration."""

DOMAIN = "ha_raw_history"
CONF_LLM_HASS_API = "llm_hass_api"

# Tool names as they appear to the LLM
TOOL_GET_RAW_HISTORY = "GetRawHistory"
TOOL_GET_STATISTICS = "GetStatistics"

# Default time range (24 hours)
DEFAULT_HISTORY_HOURS = 24

# API identifier for the LLM API registration
API_ID = "raw_history"
API_NAME = "Raw History"
