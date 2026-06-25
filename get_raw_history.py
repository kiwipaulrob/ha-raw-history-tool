# get_raw_history.py - Raw entity state history for Home Assistant
# Runs in the python_script sandbox (very limited builtins)
# Available: str, int, bool, len, range, min, max, sum, any, all, sorted
# Available: time, logger, hass, data, output
# NOT available: type, isinstance, hasattr, dict, list, tuple, set
# NOT available: import, eval, exec, open, getattr, setattr

PREFIX = "[RAW_HISTORY]"


def log(lvl, msg):
    if lvl == "error":
        logger.error(PREFIX + " " + msg)
    elif lvl == "warning":
        logger.warning(PREFIX + " " + msg)
    else:
        logger.info(PREFIX + " " + msg)


def run():
    log("info", "Starting: entity_id=" + str(data.get("entity_id", "")))

    # Get entity_id (required)
    eid = str(data.get("entity_id", "")).strip()
    if not eid:
        output["error"] = "entity_id is required"
        log("error", "No entity_id provided")
        return
    if "." not in eid:
        output["error"] = "entity_id must be like 'cover.curtain'"
        log("error", "Invalid entity_id: " + eid)
        return

    # Resolve times with defaults
    start_raw = str(data.get("start_time", "")).strip()
    end_raw = str(data.get("end_time", "")).strip()

    if start_raw:
        start_iso = start_raw
    else:
        start_iso = time.strftime("%Y-%m-%dT%H:%M:%S",
                                  time.gmtime(time.time() - 86400))

    if end_raw:
        end_iso = end_raw
    else:
        end_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    log("info", "Query: " + eid + " from " + start_iso + " to " + end_iso)

    # Check entity exists
    current = hass.states.get(eid)
    if current is None:
        output["error"] = "Entity not found: " + eid
        log("error", "Entity not found: " + eid)
        return

    log("info", "Current state: " + str(current.state))

    # Call rest_command
    try:
        response = hass.services.call(
            "rest_command", "get_raw_history_api",
            {"entity_id": eid, "start_time": start_iso, "end_time": end_iso},
            blocking=True, return_response=True)
        log("info", "rest_command completed")
    except Exception as ex:
        err = "rest_command failed: " + str(ex)
        output["error"] = err
        log("error", err)
        return

    # Return whatever we got
    output["success"] = True
    output["entity_id"] = eid
    output["response"] = str(response)
    output["start_time"] = start_iso
    output["end_time"] = end_iso
    output["error"] = ""

    log("info", "Complete for " + eid)


run()
