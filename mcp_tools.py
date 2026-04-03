import requests
import json
import io
import zipfile
from datetime import datetime, timedelta
from tracking_plan_parser import sample_events

# Tool Definition for Claude
GET_AMPLITUDE_EVENTS_TOOL = {
    "name": "get_amplitude_events",
    "description": "Fetch raw events from Amplitude for the currently configured project. This provides real production data for auditing against the tracking plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "days_back": {
                "type": "integer",
                "description": "Number of days back from today to fetch events for. Default should usually be 30.",
                "default": 30
            }
        },
        "required": ["days_back"]
    }
}

def fetch_amplitude_events(api_key, project_id, days_back=30):
    """Fetch events from Amplitude using the Dashboard REST API."""
    end   = datetime.utcnow()
    start = end - timedelta(days=days_back)

    url = "https://amplitude.com/api/2/events/list"
    try:
        r = requests.get(url, auth=(api_key, ""), timeout=60)
        if r.status_code == 200:
            return r.json().get("data", []), None
        else:
            return None, f"Amplitude API error {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return None, str(e)


def fetch_amplitude_export(api_key, secret_key, project_id, days_back=30, eu_datacenter=False):
    """Use Amplitude Export API to pull raw events."""
    end   = datetime.utcnow()
    start = end - timedelta(days=days_back)

    base_url = "https://analytics.eu.amplitude.com" if eu_datacenter else "https://amplitude.com"
    url = f"{base_url}/api/2/export"
    params = {
        "start": start.strftime("%Y%m%dT%H"),
        "end":   end.strftime("%Y%m%dT%H"),
        "project_id": project_id,
    }
    try:
        r = requests.get(url, params=params, auth=(api_key, secret_key), timeout=300)
        if r.status_code == 200:
            events = []
            try:
                with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                    for name in zf.namelist():
                        with zf.open(name) as f:
                            import gzip
                            try:
                                with gzip.open(f, 'rt', encoding='utf-8') as gz:
                                    for line in gz:
                                        line = line.strip()
                                        if line:
                                            try: events.append(json.loads(line))
                                            except: pass
                            except Exception:
                                f.seek(0)
                                for line in f:
                                    line = line.strip()
                                    if line:
                                        try: events.append(json.loads(line.decode('utf-8')))
                                        except: pass
            except zipfile.BadZipFile:
                for line in r.text.strip().split("\n"):
                    if line.strip():
                        try: events.append(json.loads(line))
                        except: pass
            return events, None
        elif r.status_code == 403:
            return None, (
                f"Export API error 403: {r.text[:300]}\n\n"
                "💡 Check that you're using the correct **Project API Key** and **Secret Key** "
                "from Amplitude. Also verify your EU datacenter setting."
            )
        else:
            return None, f"Export API error {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return None, str(e)

def execute_get_amplitude_events(tool_params, config):
    """
    Executes the tool using credentials provided by the Streamlit app.
    config should have: api_key, secret_key, project_id, eu_datacenter, fallback_events (for local testing without auth)
    """
    days_back = tool_params.get("days_back", 30)
    
    # If using local mock file, return those instead of calling Amplitude
    if config.get("fallback_events"):
        sampled_fallback = sample_events(config["fallback_events"], {"sample_size": 200})
        return {"events": sampled_fallback, "source": "local JSON file"}

    api_key = config.get("api_key")
    secret_key = config.get("secret_key")
    project_id = config.get("project_id")
    eu_datacenter = config.get("eu_datacenter", False)
    
    if not api_key:
        return {"error": "Missing Amplitude API Key in environment"}
    
    events, err = None, None
    if secret_key and project_id:
        events, err = fetch_amplitude_export(api_key, secret_key, project_id, days_back, eu_datacenter)
    else:
        # Fall back to standalone API if no export API credentials
        events, err = fetch_amplitude_events(api_key, project_id, days_back)
        
    if err:
        return {"error": str(err)}
    
    # To avoid Gemini Free Tier Token Limits (250k tokens/min), we use the session-preserving sampler.
    # This grabs exactly 200 events, but keeps sessions perfectly intact for M3/M4/M6 funnel checks.
    sampled_events = sample_events(events, {"sample_size": 200})
    
    return {
        "status": "success",
        "events_returned": len(sampled_events),
        "total_available": len(events),
        "events": sampled_events
    }
