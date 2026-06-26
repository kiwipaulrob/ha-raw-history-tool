# HA Raw History Tool

A Home Assistant custom integration that exposes entity state history to LLM conversation agents **without** HTTP loopback or Bearer tokens.

## Why This Exists

The old approach used:
```
LLM -> script.get_raw_history -> python_script.get_raw_history
    -> rest_command.get_raw_history_api -> GET /api/history/period/
    -> HA Recorder (HTTP loopback with Bearer token)
```

This had two problems:
1. **Expired tokens** — the Bearer token embedded in the YAML would expire, breaking the pipeline
2. **Unnecessary HTTP loopback** — the python_script was making HTTP calls back to its own HA instance

This custom integration eliminates both by calling HA's internal recorder API directly:

```
LLM -> GetRawHistory tool (via Assist API) -> recorder.history.get_significant_states()
    -> HA Recorder (direct in-process call, no HTTP, no tokens)
```

## What It Provides

### 1. LLM Tool: `GetRawHistory`

Registered as a custom `llm.API` (ID: `raw_history`, name: `Raw History`). When selected alongside `Assist` in your conversation agent config, the LLM gains a tool that queries state history for **any** entity type — covers, switches, sensors, lights, locks, climate, etc.

**Parameters:**
- `entity_id` (required) — e.g. `cover.curtain`, `sensor.outside_temperature`
- `start_time` (optional, ISO format, defaults to 24h ago)
- `end_time` (optional, ISO format, defaults to now)

**Returns:** List of state changes with state and local timestamp.

### 2. Service: `ha_raw_history.get_history`

For use in automations and scripts:
```yaml
service: ha_raw_history.get_history
data:
  entity_id: cover.curtain
  start_time: "2026-06-24T12:00:00"
```

## Installation

### 1. Copy the integration to HA

```bash
# From your HA config directory root:
cp -r custom_components/ha_raw_history /config/custom_components/
```

Or via SCP from a dev machine:
```bash
scp -r custom_components/ha_raw_history root@192.168.214.159:/config/custom_components/
```

### 2. Add to configuration.yaml

```yaml
ha_raw_history:
```

### 3. Restart Home Assistant

```bash
ha core restart
```

### 4. Enable the API in your conversation agent

1. Go to **Settings > Devices & Services > Integrations**
2. Find your **OpenRouter** (or OpenAI) integration entry
3. Click **Configure** on the conversation agent subentry
4. Under **APIs to expose to the LLM**, select **"Assist"** + **"Raw History"**
5. **Save**

Repeat for each conversation agent subentry (e.g. GPT-4o-Mini, Qwen).

### 5. Test

Ask your voice assistant:
> "Was the curtain open at 3pm yesterday?"
> "What was the outdoor temperature 2 hours ago?"
> "When was the last time the front door was unlocked?"

## What to Clean Up (Optional)

The old python_script + rest_command files are no longer needed:

- `/config/python_scripts/get_raw_history.py` — no longer needed
- `/config/packages/get_raw_history.yaml` — can be removed or disabled
- `rest_command.get_raw_history_api` — deprecated
- `python_script.get_raw_history` — deprecated
- `script.get_raw_history` — deprecated
- `intent_script.GetRawHistory` — deprecated (the new tool replaces this entirely)

The new integration registers the tool through the proper `llm.API` mechanism, so it appears naturally in the conversation agent's tool list without any YAML config.

## Architecture

```
custom_components/ha_raw_history/
  __init__.py        - Integration setup, llm.API registration, service handler
  const.py           - Constants (DOMAIN, API_ID, etc.)
  history_tool.py    - GetRawHistoryTool class (Tool subclass)
  manifest.json      - HA manifest (domain, version, dependencies)
  services.yaml      - Service definitions
```

### How the tool registration works

1. On HA startup, `ha_raw_history` registers a custom `llm.API` via `llm.async_register_api()`
2. The API appears as "Raw History" in conversation agent options flows
3. When selected alongside "Assist", the `chat_log.async_provide_llm_data()` merges tools from both APIs into a `MergedAPI`
4. The OpenAI conversation agent formats `GetRawHistory` as an OpenAI-compatible function tool
5. When the LLM calls the tool, it executes via HA's event loop directly on the recorder database

### No more expired tokens

The old flow used a hardcoded Bearer token that would expire (long-lived tokens last ~10 years, but the one that was embedded had already expired). The new flow makes zero HTTP requests — it calls `hass.components.recorder.history.get_significant_states()` directly from within the HA process.

## Compatibility

- Home Assistant Core **2026.6+** (uses `llm.API` and `assist_pipeline` APIs)
- Works with any conversation agent that supports `llm_hass_api` selection (OpenAI, OpenRouter, Anthropic, Google, etc.)
- Fully compatible with **covers, switches, sensors, locks, climate, lights, binary_sensors**

## Development

```bash
git clone https://github.com/kiwipaulrob/ha-raw-history-tool.git
cd ha-raw-history-tool
git checkout custom-integration
```

## Version History

- **1.0.0** — Custom integration with GetRawHistory LLM tool and get_history service
- **0.1.0** (deprecated) — Python script + rest_command with Bearer token
