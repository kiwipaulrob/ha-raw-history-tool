# Get Raw State History — Home Assistant Python Script

A Python script and supporting configuration for Home Assistant that returns **raw state changes** for any entity type — covers, switches, lights, sensors, locks, presence trackers, etc.

Unlike `recorder.get_statistics` (which only returns numeric aggregations for sensors with `state_class`), this tool queries the HA recorder's **raw state history** — the same data available at `GET /api/history/period/`. This means you can ask questions like:

- "Was the curtain open at 3pm yesterday?"
- "When did the garage door last open?"
- "What was the outside temperature every hour for the past 2 days?"
- "Has the washing machine been running today?"

## Architecture

```
LLM / Conversation Agent
        │
        ▼
  script.get_raw_history      ← LLM-friendly script entity
        │
        ▼
  python_script.get_raw_history  ← Logic layer: validation, defaults, formatting
        │
        ▼
  rest_command.get_raw_history_api  ← HTTP layer: calls HA's /api/history/period/
        │
        ▼
  HA Recorder Database         ← Raw state changes for ALL entity types
```

## Files

| File | Purpose |
|------|---------|
| `get_raw_history.py` | The Python script — runs in HA's python_script sandbox |
| `get_raw_history.yaml` | HA package — defines rest_command, script, and intent_script |
| `README.md` | This file |
| `CHANGELOG.md` | Version history |

## Installation

### 1. Enable python_script integration

Add to your `configuration.yaml`:

```yaml
python_script:
```

### 2. Create the python_scripts directory

```bash
mkdir -p /config/python_scripts
```

### 3. Copy the Python script

Copy `get_raw_history.py` to `/config/python_scripts/get_raw_history.py`.

Verify it's detected:

```bash
ha core restart   # or use the python_script.reload service
```

### 4. Add the HASS_TOKEN to secrets.yaml

The `rest_command` needs a long-lived access token to call the HA API internally. Add this to `/config/secrets.yaml`:

```yaml
hass_token: "eyJhbGciOiJIUzI1NiIsInR5cCI..."
```

To generate a token: Settings → Users → Your Profile → Long-Lived Access Tokens → Create Token.

> **Important**: The quotes around the token are required because the token contains special characters (`.`, `-`, `_`).

### 5. Copy the package file

Copy `get_raw_history.yaml` to `/config/packages/get_raw_history.yaml`.

### 6. Restart Home Assistant

```bash
ha core restart
```

## Usage

### Via Conversation Agent (LLM)

The LLM can call `script.get_raw_history` with:

```yaml
entity_id: "cover.curtain"          # Required
start_time: "2026-06-25T12:00:00"  # Optional, defaults to 24h ago
end_time: "2026-06-26T12:00:00"    # Optional, defaults to now
debug: false                        # Optional, set true for verbose logging
```

### Via Developer Tools → Actions

```yaml
action: script.get_raw_history
data:
  entity_id: cover.curtain
  start_time: "2026-06-25T00:00:00"
  end_time: "2026-06-26T00:00:00"
```

### Via HA REST API

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "cover.curtain"}' \
  http://your-ha:8123/api/services/script/get_raw_history
```

### Via the raw REST endpoint (for testing)

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://your-ha:8123/api/history/period/2026-06-25T00:00:00Z?filter_entity_id=cover.curtain&end_time=2026-06-26T00:00:00Z&minimal_response=true&significant_changes_only=true"
```

## Response Format

Success:

```json
{
  "success": true,
  "entity_id": "cover.curtain",
  "state_changes": [
    {
      "state": "open",
      "timestamp": "2026-06-25T18:45:46.739237+00:00"
    },
    {
      "state": "closed",
      "timestamp": "2026-06-25T04:44:44.381003+00:00"
    }
  ],
  "count": 2,
  "start_time": "2026-06-25T00:00:00",
  "end_time": "2026-06-26T00:00:00",
  "error": null
}
```

Error:

```json
{
  "success": false,
  "entity_id": "cover.nonexistent",
  "error": "Entity 'cover.nonexistent' not found in Home Assistant",
  "count": 0,
  "state_changes": []
}
```

## Debugging

Set `debug: true` in the data to enable verbose logging. Look for `[RAW_HISTORY]` prefix in the HA logs:

```bash
ha core logs | grep RAW_HISTORY
```

This will show:
- All input parameters received
- The resolved start/end times
- The raw API response (truncated)
- Current state of the entity
- Each validation step

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `rest_command failed` | HASS_TOKEN missing or expired | Check secrets.yaml has valid `hass_token` |
| `Entity not found` | Entity ID is wrong | Use `hass.states.get(entity_id)` to verify the exact ID |
| `Unexpected response format` | rest_command returned unexpected data | Enable debug=true and check logs |
| `Could not parse response` | Response content is malformed | Check the raw API endpoint in browser/curl |
| Empty state_changes | Entity exists but no changes in period | Widen the time window |
| Script times out | Large query window for busy entity | Narrow the time range |

## Comparison: get_raw_history vs history_crud (recorder.get_statistics)

| Feature | get_raw_history (this) | history_crud (recorder.get_statistics) |
|---------|----------------------|----------------------------------------|
| Entity types | **All** — covers, switches, sensors, etc. | Numeric sensors only (state_class required) |
| Data returned | Raw state changes (open/closed, on/off) | Numeric aggregations (mean, min, max, sum) |
| Use case | "Was the curtain open at 3pm?" | "What was the average temperature yesterday?" |
| API used | `/api/history/period/` | `recorder.get_statistics` service |
| History scope | Any time range | Only entities with long-term statistics |

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

## License

MIT — feel free to use, modify, and share.
