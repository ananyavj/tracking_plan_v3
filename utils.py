# utils.py
from datetime import datetime

def _extract_event_time(event: dict):
    """
    Amplitude Export API uses 'event_time' (string: 'YYYY-MM-DD HH:MM:SS.ffffff').
    Amplitude HTTP API uses 'time' (int milliseconds).
    This helper handles both so callers don't have to.
    Returns a datetime or None.
    """
    # Export API string format
    raw = event.get("event_time")
    if raw:
        try:
            return datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    # HTTP API / simulated events integer ms format
    ts = event.get("time")
    if ts:
        try:
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts / 1000)
            return datetime.strptime(str(ts)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return None

def parse_amplitude_time(ts):
    """Parses various Amplitude timestamp formats into a datetime object."""
    if not ts: return None
    try:
        if isinstance(ts, str):
            return datetime.strptime(ts[:19].replace('T', ' '), "%Y-%m-%d %H:%M:%S")
        else:
            return datetime.fromtimestamp(ts/1000)
    except:
        try:
            return datetime.strptime(ts[:10], "%Y-%m-%d")
        except:
            return None

def get_platform(event):
    """Fallback priority for extracting platform dimension."""
    props   = event.get("event_properties", {})
    u_props = event.get("user_properties", {})
    return (props.get("platform") or u_props.get("platform") or "unknown")

def get_dataset_bounds(events):
    """Returns ISO start/end strings for the current event batch."""
    if not events: return None, None
    try:
        dts = [_extract_event_time(e) for e in events]
        dts = [d for d in dts if d]
        if not dts: return None, None
        
        start_dt = min(dts)
        end_dt   = max(dts)
        return start_dt.strftime("%Y%m%dT%H"), end_dt.strftime("%Y%m%dT%H")
    except:
        return None, None
