# mcp_tools.py
import json
import os
import pandas as pd
import requests
import zlib
import io
import zipfile
from datetime import datetime, timedelta
from requests.auth import HTTPBasicAuth
from audit_engine import AuditEngine
from utils import (
    _extract_event_time,
    get_platform,
    get_dataset_bounds
)

def _event_time_ms(event: dict):
    """Returns the event timestamp as integer milliseconds, or 0."""
    dt = _extract_event_time(event)
    if dt:
        return int(dt.timestamp() * 1000)
    return 0

METADATA_FILE = "audit_metadata.json"

def get_platform(event):
    """Fallback priority for extracting platform dimension."""
    props   = event.get("event_properties", {})
    u_props = event.get("user_properties", {})
    return (props.get("platform") or u_props.get("platform") or "unknown")

def load_audit_metadata():
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r") as f:
            try:
                return json.load(f)
            except: return {}
    return {}

def save_audit_metadata(project_id, last_date):
    if not isinstance(last_date, (datetime, pd.Timestamp)):
        try: last_date = datetime.strptime(str(last_date), "%Y-%m-%d")
        except: return

    data = load_audit_metadata()
    data[str(project_id)] = {
        "last_audit_date": last_date.strftime("%Y-%m-%d"),
        "updated_at":      datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(METADATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def execute_get_amplitude_events(tool_params, config):
    """
    Fetches raw events from Amplitude Export API.

    FIX — Request timeout: added timeout=(10, 120) to prevent the requests
    session from hanging indefinitely on large exports (1M+ events/day).
    - 10s  = connection timeout (fail fast if Amplitude is unreachable)
    - 120s = read timeout     (allow up to 2 min for the response body)
    If the read times out, the caller receives a structured error dict so
    the UI can surface a clear "request timed out" message rather than freezing.
    """
    api_key    = config.get("api_key")    or os.getenv("AMPLITUDE_API_KEY")
    secret_key = config.get("secret_key") or os.getenv("AMPLITUDE_SECRET_KEY")

    start_str = tool_params.get("start")
    end_str   = tool_params.get("end")

    if not (start_str and end_str):
        days_back  = tool_params.get("days_back", 3)
        end_time   = datetime.utcnow()
        start_time = end_time - timedelta(days=days_back)
        start_str  = start_time.strftime("%Y%m%dT%H")
        end_str    = end_time.strftime("%Y%m%dT%H")

    if not api_key or not secret_key:
        return {"error": "Amplitude API Key or Secret Key missing in config/env."}

    # --- CHUNKING LOGIC ---
    # Convert 'YYYYMMDDTHH' strings to datetimes for iteration
    try:
        dt_start = datetime.strptime(start_str, "%Y%m%dT%H")
        dt_end   = datetime.strptime(end_str,   "%Y%m%dT%H")
    except Exception as e:
        return {"error": f"Invalid date format: {e}"}

    all_events = []
    current_start = dt_start

    # Fetch in 24-hour chunks to bypass Amplitude Export API truncation/timeout limits
    while current_start < dt_end:
        chunk_end = min(current_start + timedelta(hours=24), dt_end)
        c_start_str = current_start.strftime("%Y%m%dT%H")
        c_end_str   = chunk_end.strftime("%Y%m%dT%H")
        
        url = f"https://amplitude.com/api/2/export?start={c_start_str}&end={c_end_str}"
        
        try:
            response = requests.get(
                url,
                auth=HTTPBasicAuth(api_key, secret_key),
                stream=True,
                timeout=(10, 120),
            )
            if response.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                    for filename in z.namelist():
                        if filename.endswith(".json.gz"):
                            with z.open(filename) as f:
                                gz_content   = f.read()
                                decompressed = zlib.decompress(gz_content, 16 + zlib.MAX_WBITS)
                                for line in decompressed.decode('utf-8').splitlines():
                                    if line.strip():
                                        ev = json.loads(line)
                                        # Normalise: Export API uses 'event_time' (string).
                                        # Inject a 'time' int (ms) so AuditEngine and all
                                        # downstream code that does e.get('time') works correctly.
                                        if 'time' not in ev or not ev['time']:
                                            dt = _extract_event_time(ev)
                                            if dt:
                                                ev['time'] = int(dt.timestamp() * 1000)
                                        all_events.append(ev)
            elif response.status_code == 404:
                pass # No events in this specific chunk
            else:
                return {"error": f"Amplitude API error {response.status_code} in chunk {c_start_str}: {response.text}"}
        except requests.exceptions.Timeout:
            return {"error": f"Request timed out for chunk {c_start_str}."}
        except Exception as e:
            return {"error": f"Error in chunk {c_start_str}: {str(e)}"}

        current_start = chunk_end

    return {
        "status":         "success",
        "events_returned": len(all_events),
        "events":          all_events,
        "total_in_batch":  len(all_events),
        "chunk_count":     (dt_end - dt_start).days + 1
    }

def execute_get_user_history(tool_params, config):
    """
    Fetches full activity for a specific user via Amplitude User Activity API.
    NOTE: This data comes from OUTSIDE the audited batch and should be used
    for supplemental cross-device diagnosis only.
    """
    api_key    = config.get("api_key")    or os.getenv("AMPLITUDE_API_KEY")
    secret_key = config.get("secret_key") or os.getenv("AMPLITUDE_SECRET_KEY")
    user_id    = tool_params.get("user_id")

    if not api_key or not secret_key:
        return {"error": "Amplitude API Key or Secret Key missing."}
    if not user_id:
        return {"error": "user_id is required."}

    url = f"https://amplitude.com/api/2/useractivity?user={user_id}"

    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(api_key, secret_key),
            timeout=(10, 30),
        )
        if response.status_code != 200:
            return {"error": f"Amplitude API error {response.status_code}: {response.text}"}

        data   = response.json()
        events = data.get("events", [])
        return {
            "status":          "success",
            "user_id":         user_id,
            "events_returned": len(events),
            "events":          events
        }
    except requests.exceptions.Timeout:
        return {"error": f"User history request timed out for user_id={user_id}."}
    except Exception as e:
        return {"error": str(e)}

def execute_run_comprehensive_audit(tool_params, config):
    """
    Runs a full-volume audit and clusters findings to optimize AI context.
    Prioritizes P0 revenue events (Order Completed, Checkout Started).

    FIX: Accepts optional 'tracking_plan_path' from tool_params so uploaded
    tracking plans are used instead of always falling back to tracking_plan.xlsx.
    """
    try:
        events = tool_params.get("events")
        # FIX: Use caller-supplied tracking plan path if provided
        tp_path = tool_params.get("tracking_plan_path", "tracking_plan.xlsx")

        if not events:
            data_path = "simulated_events.json"
            if not os.path.exists(data_path):
                return {"error": "No events provided and simulated_events.json not found. Run simulate.py first."}
            with open(data_path, "r", encoding="utf-8") as f:
                events = json.load(f)

        engine = AuditEngine(tp_path, events)
        summary, issues = engine.run_all_checks()

        if not issues:
            return {"status": "success", "audit_summary": summary, "issues": [], "total_issues_in_data": 0}

        event_platform_map = {
            e.get('insert_id') or (e.get('event_type'), e.get('time')): get_platform(e)
            for e in events
        }

        clusters  = {}
        P0_EVENTS = ["Order Completed", "Checkout Started"]

        for issue in issues:
            platform = event_platform_map.get(
                issue.get('insert_id') or (issue.get('event'), issue.get('timestamp'))
            ) or "unknown"
            key = (issue["code"], issue["event"], platform)

            if key not in clusters:
                clusters[key] = {
                    "code":            issue["code"],
                    "event":           issue["event"],
                    "platform":        platform,
                    "count":           0,
                    "affected_users":  set(),
                    "samples":         [],
                    "business_weight": 0
                }

            c = clusters[key]
            c["count"] += 1
            if issue.get("user_id"): c["affected_users"].add(issue["user_id"])
            if len(c["samples"]) < 3:
                c["samples"].append(issue)

            weight = 0
            if issue["event"] in P0_EVENTS:       weight += 10
            if issue["severity"] == "critical":    weight += 5
            c["business_weight"] = max(c["business_weight"], weight)

        clustered_output = []
        for c in clusters.values():
            c["affected_users"] = len(c["affected_users"])
            clustered_output.append(c)

        clustered_output.sort(key=lambda x: (x["business_weight"], x["count"]), reverse=True)

        return {
            "status":             "success",
            "audit_summary":      summary,
            "clustered_findings": clustered_output[:40],
            "total_raw_issues":   len(issues),
            "note": f"Clustered {len(issues)} issues into {len(clustered_output)} unique Code/Event/Platform groups."
        }
    except Exception as e:
        return {"error": str(e)}

def execute_get_session_count(tool_params, config):
    """Answers 'How many sessions are there?' across the full dataset."""
    try:
        events = tool_params.get("events")
        if not events:
            data_path = "simulated_events.json"
            with open(data_path, "r", encoding="utf-8") as f:
                events = json.load(f)

        sessions = set()
        for e in events:
            sid = e.get("event_properties", {}).get("session_id")
            if sid: sessions.add(sid)
        return {"total_unique_sessions": len(sessions)}
    except Exception as e:
        return {"error": str(e)}

def execute_query_data_distribution(tool_params, config):
    """
    Exhaustively scans data to provide ground-truth property distributions.
    Strictly uses provided 'events' batch (audited batch only).
    """
    prop_name = tool_params.get("property_name")
    top_n     = tool_params.get("top_n", 20)
    try:
        events = tool_params.get("events")
        if not events:
            return {"error": "No event data available for inspection. Please run an audit first."}

        distribution = {}
        for e in events:
            val = e.get("event_properties", {}).get(prop_name)
            if val is not None:
                distribution[str(val)] = distribution.get(str(val), 0) + 1

        sorted_dist = sorted(distribution.items(), key=lambda x: x[1], reverse=True)
        return {
            "property":               prop_name,
            "total_observed_values":  len(distribution),
            "top_values":             dict(sorted_dist[:top_n])
        }
    except Exception as e:
        return {"error": str(e)}

def execute_inspect_data(tool_params, config):
    """
    Exhaustively scans data to provide ground-truth metadata.
    Helps verify: 'How many sarees?', 'What users exist?', 'What properties were sent?'
    Strictly uses provided 'events' batch (audited batch only).
    """
    try:
        events = tool_params.get("events")
        if not events:
            return {"error": "No data available for inspection. Audit required."}

        event_types     = {}
        distinct_users  = set()
        properties_found = set()
        material_dist   = {}
        category_dist   = {}
        brand_dist      = {}

        for e in events:
            etype = e.get("event_type", "unknown")
            event_types[etype] = event_types.get(etype, 0) + 1
            distinct_users.add(e.get("user_id"))

            props = e.get("event_properties", {})
            for k in props.keys():
                properties_found.add(k)

            mat = props.get("material")
            if mat: material_dist[mat] = material_dist.get(mat, 0) + 1

            cat = props.get("category")
            if cat: category_dist[cat] = category_dist.get(cat, 0) + 1

            brand = props.get("brand")
            if brand: brand_dist[brand] = brand_dist.get(brand, 0) + 1

        return {
            "total_events":            len(events),
            "distinct_user_count":     len(distinct_users),
            "event_type_breakdown":    event_types,
            "all_properties_observed": sorted(list(properties_found)),
            "common_distributions": {
                "material": material_dist,
                "category": category_dist,
                "brand":    brand_dist
            },
            "sample_user_ids": list(distinct_users)[:10],
            "note": "Use this tool to verify the existence of specific data points before making claims about the dataset."
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# FIX: Corrected wrapper signatures — start/end are now forwarded properly
# into tool_params so execute_get_amplitude_events receives them.
# Previously **config ate start/end silently, causing every Live Fetch to
# default to "last 3 days" regardless of the date pickers.
# ---------------------------------------------------------------------------

def get_amplitude_events(days_back=3, start=None, end=None, **config):
    """
    Thin wrapper around execute_get_amplitude_events.
    Passes start/end through tool_params when provided; falls back to days_back otherwise.

    FIX 4 — Credential forwarding: previously if api_key/secret_key were passed
    as keyword args they landed in **config (a plain dict) and were forwarded
    correctly. This is still the case, but we now explicitly pop and re-key them
    so that even callers using positional-style kwargs (e.g. api_key=...) are
    guaranteed to reach execute_get_amplitude_events via config.get('api_key').
    """
    tool_params = {"days_back": days_back}
    if start:
        tool_params["start"] = start
    if end:
        tool_params["end"] = end
    # Normalise common alternative key names so callers are never silently wrong
    normalised_config = dict(config)
    for alias, canonical in (("key", "api_key"), ("amplitude_api_key", "api_key"),
                              ("amplitude_secret_key", "secret_key"),
                              ("secret", "secret_key")):
        if alias in normalised_config and canonical not in normalised_config:
            normalised_config[canonical] = normalised_config.pop(alias)
    return execute_get_amplitude_events(tool_params, normalised_config)

def get_user_history(user_id=None, **config):
    return execute_get_user_history({"user_id": user_id}, config)
