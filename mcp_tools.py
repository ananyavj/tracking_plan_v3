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
    dt = _extract_event_time(event)
    if dt: return int(dt.timestamp() * 1000)
    return 0

METADATA_FILE = "audit_metadata.json"
HISTORY_FILE  = "audit_history.json"

def save_atomic_json(filepath, data):
    temp_path = filepath + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, filepath)

def load_audit_metadata():
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_audit_metadata(project_id, last_date=None, **stats):
    data = load_audit_metadata()
    pid  = str(project_id)
    if pid not in data: data[pid] = {}
    if last_date:
        if not isinstance(last_date, (datetime, pd.Timestamp)):
            try: last_date = datetime.strptime(str(last_date), "%Y-%m-%d")
            except: pass
        if last_date: data[pid]["last_audit_date"] = last_date.strftime("%Y-%m-%d")
    data[pid]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for k, v in stats.items(): data[pid][k] = v
    save_atomic_json(METADATA_FILE, data)

def get_audit_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            try: return json.load(f)
            except: return []
    return []

def append_audit_history(summary):
    history = get_audit_history()
    entry = {
        "timestamp":      datetime.now().isoformat(),
        "health_score":   summary.get("health_score"),
        "total_issues":   summary.get("total_issues"),
        "critical_issues": summary.get("critical_issues"),
        "total_events":   summary.get("total_events"),
        "project_id":     summary.get("project_id"),
        "top_dedup_keys": summary.get("top_dedup_keys", []),
        "top_driver":     summary.get("top_driver")
    }
    history.append(entry)
    if len(history) > 50: history = history[-50:]
    save_atomic_json(HISTORY_FILE, history)

def execute_get_amplitude_events(tool_params, config):
    api_key    = config.get("api_key")    or os.getenv("AMPLITUDE_API_KEY")
    secret_key = config.get("secret_key") or os.getenv("AMPLITUDE_SECRET_KEY")
    start_str  = tool_params.get("start")
    end_str    = tool_params.get("end")
    if not (start_str and end_str):
        db = tool_params.get("days_back", 3)
        e_t, s_t = datetime.utcnow(), datetime.utcnow() - timedelta(days=db)
        start_str, end_str = s_t.strftime("%Y%m%dT%H"), e_t.strftime("%Y%m%dT%H")
    if not (api_key and secret_key): return {"error": "Missing keys."}
    all_events = []
    try:
        dt_s, dt_e = datetime.strptime(start_str, "%Y%m%dT%H"), datetime.strptime(end_str, "%Y%m%dT%H")
        curr = dt_s
        while curr < dt_e:
            chunk_e = min(curr + timedelta(hours=24), dt_e)
            url = f"https://amplitude.com/api/2/export?start={curr.strftime('%Y%m%dT%H')}&end={chunk_e.strftime('%Y%m%dT%H')}"
            resp = requests.get(url, auth=HTTPBasicAuth(api_key, secret_key), stream=True, timeout=(10, 120))
            if resp.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    for fn in z.namelist():
                        if fn.endswith(".json.gz"):
                            with z.open(fn) as f:
                                for line in zlib.decompress(f.read(), 16+zlib.MAX_WBITS).decode('utf-8').splitlines():
                                    if line.strip():
                                        ev = json.loads(line)
                                        if 'time' not in ev:
                                            dt = _extract_event_time(ev)
                                            if dt: ev['time'] = int(dt.timestamp() * 1000)
                                        all_events.append(ev)
            curr = chunk_e
    except Exception as e: return {"error": str(e)}
    return {"status": "success", "events": all_events}

def execute_get_user_history(tool_params, config):
    ak, sk = config.get("api_key") or os.getenv("AMPLITUDE_API_KEY"), config.get("secret_key") or os.getenv("AMPLITUDE_SECRET_KEY")
    uid = tool_params.get("user_id")
    if not (ak and sk and uid): return {"error": "Params missing."}
    try:
        url = f"https://amplitude.com/api/2/useractivity?user={uid}"
        resp = requests.get(url, auth=HTTPBasicAuth(ak, sk), timeout=(10, 30))
        if resp.status_code == 200: return {"status": "success", "events": resp.json().get("events", [])}
    except: pass
    return {"error": "Failed"}

def execute_inspect_data(tool_params, config):
    events = tool_params.get("events")
    if not events:
        try:
            with open("simulated_events.json", "r") as f: events = json.load(f)
        except: return {"error": "No data source available."}
    
    types, users, props = {}, set(), set()
    for e in events:
        t = e.get("event_type", "unknown")
        types[t] = types.get(t, 0) + 1
        users.add(e.get("user_id"))
        for k in (e.get("event_properties") or {}).keys(): props.add(k)
    return {"event_type_breakdown": types, "user_count": len(users), "properties": sorted(list(props))}

def execute_query_data_distribution(tool_params, config):
    pname, events = tool_params.get("property_name"), tool_params.get("events")
    if not events:
        try:
            with open("simulated_events.json", "r") as f: events = json.load(f)
        except: return {"error": "No data source available."}
    
    dist = {}
    for e in events:
        val = (e.get("event_properties") or {}).get(pname)
        if val is not None: dist[str(val)] = dist.get(str(val), 0) + 1
    return {"property": pname, "top_values": dict(sorted(dist.items(), key=lambda x:x[1], reverse=True)[:20])}

def execute_run_comprehensive_audit(tool_params, config):
    """MCP Bridge: Runs the full deterministic audit engine."""
    events = tool_params.get("events")
    if not events:
        try:
            with open("simulated_events.json", "r") as f: events = json.load(f)
        except: return {"error": "No data provided for audit."}
    
    t_plan = os.getenv("TRACKING_PLAN_PATH", "tracking_plan.xlsx")
    engine = AuditEngine(t_plan, events)
    summary, issues = engine.run_all_checks()
    return {"status": "success", "summary": summary, "issue_count": len(issues)}

def execute_audit_amplitude_direct(tool_params, config):
    """
    HIGH-VOLUME AUDIT: Fetches events DIRECTLY from Amplitude and audits them locally.
    Bypasses Claude's memory limits for 40k+ events.
    """
    days_back = tool_params.get("days_back", 1)
    print(f"[mcp] Initiating direct Amplitude fetch for last {days_back} day(s)...")
    
    fetch_res = execute_get_amplitude_events({"days_back": days_back}, config)
    if "error" in fetch_res: return fetch_res
    
    events = fetch_res.get("events", [])
    if not events: return {"error": "No events found in Amplitude for the specified window."}
    
    print(f"[mcp] Fetched {len(events)} events. Starting audit...")
    t_plan = os.getenv("TRACKING_PLAN_PATH", "tracking_plan.xlsx")
    engine = AuditEngine(t_plan, events)
    summary, issues = engine.run_all_checks()
    
    # Save to history for trend tracking
    append_audit_history(summary)
    
    return {
        "status": "success",
        "events_audited": len(events),
        "summary": summary,
        "issue_count": len(issues)
    }

def get_amplitude_events(days_back=3, start=None, end=None, **config):
    return execute_get_amplitude_events({"days_back": days_back, "start": start, "end": end}, config)

def get_user_history(user_id=None, **config):
    return execute_get_user_history({"user_id": user_id}, config)
