"""
get_raw_history.py — Raw Entity State History Query for Home Assistant

PURPOSE:
  Returns raw state changes (open/closed, on/off, home/away, etc.) for any
  entity from the HA recorder database. Unlike recorder.get_statistics which
  only returns numeric aggregations for sensors with state_class, this tool
  returns every state transition for any entity type — covers, switches,
  locks, lights, sensors, etc.

ARCHITECTURE:
  The python_script sandbox cannot import modules or make HTTP requests
  directly. Instead:
    1. User calls this script via python_script.get_raw_history
    2. This script calls rest_command.get_raw_history_api (which has the
       HASS_TOKEN and can reach the local HA API)
    3. The rest_command hits GET /api/history/period/<start_time>
       ?filter_entity_id=<entity_id>&end_time=<end_time>
    4. Response flows back: rest_command → python_script → output

INPUT (via data dict):
  entity_id    (str, required)  — Full entity ID e.g. cover.curtain
  start_time   (str, optional)  — ISO 8601 start. Default: 24h ago
  end_time     (str, optional)  — ISO 8601 end.   Default: now
  debug        (bool, optional) — If true, dump extra tracing to logger

OUTPUT (via output dict):
  success      (bool)    — Whether the query succeeded
  entity_id    (str)     — The entity that was queried
  state_changes (list)   — List of dicts: {timestamp, state, attributes?}
  count        (int)     — Number of state changes returned
  start_time   (str)     — Actual start used
  end_time     (str)     — Actual end used
  error        (str|None)— Error message if failed
  debug_info   (dict)    — Only if debug=true: raw response, timing, etc.

TRACING:
  All significant operations log at info level with [RAW_HISTORY] prefix.
  Set debug=true for full intermediate data dumps at warning level.
"""

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
DEFAULT_HOURS = 24       # Default lookback period when no start_time given
ISO_FMT = "%Y-%m-%dT%H:%M:%S"

# ---------------------------------------------------------------------------
# TRACING HELPERS
# ---------------------------------------------------------------------------
PREFIX = "[RAW_HISTORY]"


def log(level, msg, *args):
    """Centralised logging with component prefix."""
    getattr(logger, level)(f"{PREFIX} {msg}", *args)


is_debug = False


def debug(msg, *args):
    """Conditional debug logging."""
    if is_debug:
        log("warning", f"DEBUG: {msg}", *args)


# ---------------------------------------------------------------------------
# INPUT VALIDATION
# ---------------------------------------------------------------------------
def validate_input(data):
    """Validate and normalise input parameters. Returns (params, error)."""
    errors = []

    # entity_id — required
    entity_id = data.get("entity_id", "").strip()
    if not entity_id:
        errors.append("entity_id is required (e.g. cover.curtain)")
    elif "." not in entity_id:
        errors.append(f"entity_id must be fully qualified (e.g. 'cover.curtain'), got '{entity_id}'")

    # start_time — optional, validate format if provided
    start_time_raw = data.get("start_time", "").strip()

    # end_time — optional, validate format if provided
    end_time_raw = data.get("end_time", "").strip()

    if errors:
        return None, "; ".join(errors)

    return {
        "entity_id": entity_id,
        "start_time_raw": start_time_raw,
        "end_time_raw": end_time_raw,
    }, None


# ---------------------------------------------------------------------------
# TIME HELPERS
# ---------------------------------------------------------------------------
def now_iso():
    """Return current UTC time as ISO string."""
    return datetime.utcnow().strftime(ISO_FMT)


def hours_ago_iso(hours=DEFAULT_HOURS):
    """Return ISO string for N hours ago."""
    return (datetime.utcnow() - timedelta(hours=hours)).strftime(ISO_FMT)


def resolve_time(time_str, default_func):
    """Use the provided time string, or fall back to the default."""
    if time_str and time_str.strip():
        return time_str.strip()
    return default_func()


# ---------------------------------------------------------------------------
# CALL THE REST_COMMAND
# ---------------------------------------------------------------------------
def call_history_api(entity_id, start_iso, end_iso):
    """
    Call rest_command.get_raw_history_api and return the response.
    We call hass.services.call with blocking=True and return_response=True.
    """
    service_data = {
        "entity_id": entity_id,
        "start_time": start_iso,
        "end_time": end_iso,
    }

    debug("Calling rest_command.get_raw_history_api with: %s", service_data)

    try:
        response = hass.services.call(
            "rest_command",
            "get_raw_history_api",
            service_data,
            blocking=True,
            return_response=True,
        )
        debug("Raw rest_command response: %s", response)
        log("info", "rest_command returned for %s (%s → %s)", entity_id, start_iso, end_iso)
        return response, None
    except Exception as e:
        err_msg = f"rest_command failed: {e}"
        log("error", err_msg)
        return None, err_msg


# ---------------------------------------------------------------------------
# PROCESS HISTORY RESPONSE
# ---------------------------------------------------------------------------
def process_history(raw_response, entity_id):
    """
    Convert the raw API response into a clean list of state changes.
    The /api/history/period/ endpoint returns [[{state1}, {state2}, ...], ...]
    — a list of lists, one per entity.
    """
    if raw_response is None:
        return [], "No response from history API"

    # The response may come as dict with 'content' key or be the list directly
    content = raw_response.get("content", raw_response)
    debug("Content type: %s, value: %s", type(content).__name__, str(content)[:200])

    # If it's a string, try to parse it
    if isinstance(content, str):
        # In some versions, the content arrives as an already-stringified list
        # Try to evaluate it safely
        try:
            import ast
            content = ast.literal_eval(content)
        except Exception:
            return [], f"Could not parse response: {content[:100]}"

    # Should be a list of lists
    if not isinstance(content, list):
        return [], f"Unexpected response format: {type(content).__name__}"

    if not content:
        return [], None  # Empty but not an error

    # Take the first entity's history (should be the one we queried)
    entity_history = content[0] if content else []

    if not isinstance(entity_history, list):
        return [], f"Expected list of states, got {type(entity_history).__name__}"

    state_changes = []
    for entry in entity_history:
        if not isinstance(entry, dict):
            continue
        change = {
            "state": entry.get("state", "unknown"),
            "timestamp": entry.get("last_changed", entry.get("last_updated", "unknown")),
        }
        # Only include attributes if we have something interesting
        attrs = entry.get("attributes", {})
        if attrs:
            # Strip internal HA attributes to keep response lean
            clean_attrs = {k: v for k, v in attrs.items()
                          if not k.startswith("_") and k not in ("restored", "supported_features")}
            if clean_attrs:
                change["attributes"] = clean_attrs
        state_changes.append(change)

    return state_changes, None


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------
log("info", "Script invoked with data: %s", data)

# 1. Check debug flag
is_debug = data.get("debug", False)
if isinstance(is_debug, str):
    is_debug = is_debug.lower() in ("true", "1", "yes")

if is_debug:
    log("warning", "DEBUG MODE ENABLED — all inputs and intermediate data will be logged")

# 2. Validate inputs
params, err = validate_input(data)
if err:
    log("error", "Validation failed: %s", err)
    output["success"] = False
    output["error"] = err
    output["entity_id"] = data.get("entity_id", "")
    return  # Early exit — output dict already populated

entity_id = params["entity_id"]

# 3. Resolve times
start_iso = resolve_time(params["start_time_raw"], lambda: hours_ago_iso(DEFAULT_HOURS))
end_iso = resolve_time(params["end_time_raw"], now_iso)

log("info", "Querying %s from %s to %s", entity_id, start_iso, end_iso)

# 4. Check entity exists
current_state = hass.states.get(entity_id)
if current_state is None:
    err_msg = f"Entity '{entity_id}' not found in Home Assistant"
    log("error", err_msg)
    output["success"] = False
    output["error"] = err_msg
    output["entity_id"] = entity_id
    return

log("info", "Current state of %s: %s", entity_id, current_state.state)

# 5. Call the history API
raw_response, api_err = call_history_api(entity_id, start_iso, end_iso)
if api_err:
    log("error", "API call failed: %s", api_err)
    output["success"] = False
    output["error"] = api_err
    output["entity_id"] = entity_id
    return

# 6. Process the response
state_changes, process_err = process_history(raw_response, entity_id)
if process_err:
    log("error", "Processing failed: %s", process_err)
    output["success"] = False
    output["error"] = process_err
    output["entity_id"] = entity_id
    if is_debug:
        output["debug_info"] = {"raw_response": str(raw_response)[:500]}
    return

# 7. Build output
output["success"] = True
output["entity_id"] = entity_id
output["state_changes"] = state_changes
output["count"] = len(state_changes)
output["start_time"] = start_iso
output["end_time"] = end_iso
output["error"] = None

log("info", "Success: %d state changes returned for %s", len(state_changes), entity_id)

# 8. Debug info
if is_debug:
    output["debug_info"] = {
        "current_state": current_state.state,
        "raw_response_truncated": str(raw_response)[:300],
        "input_data": dict(data),
    }

log("info", "Script complete. Output keys: %s", list(output.keys()))
