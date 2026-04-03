"""
tracking_plan_parser.py
Parses the Fashion Marketplace Tracking Plan Excel file into a JSON schema
that can be passed directly to the Claude audit agent.

Usage:
    from tracking_plan_parser import parse_tracking_plan
    schema = parse_tracking_plan("Fashion_Marketplace_Tracking_Plan.xlsx")
"""

import openpyxl
import json
from pathlib import Path

# Sheets that contain event definitions (in order they appear in the workbook)
EVENT_SHEETS = [
    "Identify",
    "Page",
    "Browsing",
    "Purchase Funnel",
    "Post-Purchase",
    "Marketing",
]

# Sheets that contain supporting data (not events)
SUPPORT_SHEETS = {
    "Global Props":    "global_props",
    "Data Dictionary": "data_dictionary",
}

# Column positions in event sheets (0-indexed, from col A)
COL_EVENT_NAME  = 0   # A — event name (row starts with ▶)
COL_PROPERTY    = 1   # B — property name
COL_REQ_OPT     = 2   # C — "Required" or "Optional"
COL_TYPE        = 3   # D — data type
COL_EXAMPLE     = 4   # E — example value
COL_ALLOWED     = 5   # F — allowed values (pipe-separated)
COL_DESCRIPTION = 6   # G — description
COL_NOTES       = 7   # H — implementation notes

# Column positions in Global Props sheet
GCOL_PROPERTY   = 0   # A
GCOL_REQ_OPT    = 1   # B
GCOL_TYPE       = 2   # C
GCOL_EXAMPLE    = 3   # D
GCOL_AMP_MAP    = 4   # E — Amplitude mapping
GCOL_DESC       = 5   # F

# Normalize type strings to canonical set
TYPE_NORMALIZE = {
    "string":  "string",
    "str":     "string",
    "float":   "float",
    "number":  "float",
    "decimal": "float",
    "integer": "integer",
    "int":     "integer",
    "boolean": "boolean",
    "bool":    "boolean",
    "array":   "array",
    "list":    "array",
    "iso8601": "ISO8601",
    "datetime":"ISO8601",
    "timestamp":"ISO8601",
}


def _cell(row, idx):
    """Safely get a cell value by index, returning None if out of range."""
    try:
        val = row[idx]
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None
    except IndexError:
        return None


def _normalize_type(raw):
    if raw is None:
        return "string"
    return TYPE_NORMALIZE.get(raw.lower().strip(), "string")


def _parse_allowed(raw):
    if not raw:
        return []
    return [v.strip() for v in raw.split("|") if v.strip()]


def _is_required(raw):
    if raw is None:
        return False
    return "required" in raw.lower()


def parse_event_sheets(wb):
    """Parse all event definition sheets into a list of event schema objects."""
    events = []

    for sheet_name in EVENT_SHEETS:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        current_event = None

        for row in ws.iter_rows(min_row=3, values_only=True):
            # Convert tuple to list for safe indexing
            row = list(row)

            evt_col = _cell(row, COL_EVENT_NAME)
            prop    = _cell(row, COL_PROPERTY)

            # Detect event header row (starts with ▶ or is the event name in col A)
            if evt_col and ("▶" in evt_col or (prop and _cell(row, COL_REQ_OPT) is None)):
                event_name = evt_col.replace("▶", "").strip()
                if not event_name:
                    continue
                description = _cell(row, COL_PROPERTY) or ""   # description is in col B of header row
                current_event = {
                    "event_name":  event_name,
                    "sheet":       sheet_name,
                    "description": description,
                    "properties":  [],
                }
                events.append(current_event)
                continue

            # Detect property row (col A = event name repeated, col B = property name)
            if current_event and prop:
                req     = _cell(row, COL_REQ_OPT)
                dtype   = _cell(row, COL_TYPE)
                example = _cell(row, COL_EXAMPLE)
                allowed = _cell(row, COL_ALLOWED)
                desc    = _cell(row, COL_DESCRIPTION)
                notes   = _cell(row, COL_NOTES)

                current_event["properties"].append({
                    "name":           prop,
                    "required":       _is_required(req),
                    "type":           _normalize_type(dtype),
                    "example":        example or "",
                    "allowed_values": _parse_allowed(allowed),
                    "description":    desc or "",
                    "notes":          notes or "",
                })

    return events


def parse_global_props(wb):
    """Parse the Global Props sheet."""
    if "Global Props" not in wb.sheetnames:
        return []

    ws = wb["Global Props"]
    props = []

    for row in ws.iter_rows(min_row=3, values_only=True):
        row  = list(row)
        prop = _cell(row, GCOL_PROPERTY)
        if not prop or prop.startswith("─"):
            continue
        props.append({
            "name":           prop,
            "required":       _is_required(_cell(row, GCOL_REQ_OPT)),
            "type":           _normalize_type(_cell(row, GCOL_TYPE)),
            "example":        _cell(row, GCOL_EXAMPLE) or "",
            "amplitude_map":  _cell(row, GCOL_AMP_MAP) or "",
            "description":    _cell(row, GCOL_DESC) or "",
        })

    return props


def parse_data_dictionary(wb):
    """Parse the Data Dictionary sheet into a flat enum map."""
    if "Data Dictionary" not in wb.sheetnames:
        return {}

    ws = wb["Data Dictionary"]
    dictionary = {}

    for row in ws.iter_rows(min_row=3, values_only=True):
        row      = list(row)
        prop     = _cell(row, 0)
        allowed  = _cell(row, 1)
        dtype    = _cell(row, 2)
        notes    = _cell(row, 3)

        if not prop or prop.startswith("─"):
            continue

        dictionary[prop] = {
            "allowed_values": _parse_allowed(allowed) if allowed else [],
            "type":           _normalize_type(dtype),
            "notes":          notes or "",
        }

    return dictionary


def parse_tracking_plan(filepath) -> dict:
    """
    Main entry point. Parses the tracking plan Excel file and returns
    a JSON-serializable dict matching the audit agent's input contract.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)

    events          = parse_event_sheets(wb)
    global_props    = parse_global_props(wb)
    data_dictionary = parse_data_dictionary(wb)

    # Build a quick lookup: normalized event name → schema
    event_lookup = {}
    for ev in events:
        key = ev["event_name"].lower().replace(" ", "_")
        event_lookup[key] = ev

    return {
        "events":          events,
        "event_lookup":    event_lookup,   # for fast M0 checks
        "global_props":    global_props,
        "data_dictionary": data_dictionary,
        "meta": {
            "source_file":   str(filepath),
            "total_events":  len(events),
            "sheets_parsed": EVENT_SHEETS,
        }
    }


def sample_events(events: list, config: dict) -> list:
    """
    Sample events from a large dataset before passing to Claude.
    Respects priority sampling strategy from audit_config.
    """
    sample_size   = config.get("sample_size", 300)
    always_include = config.get("sampling_strategy", {}).get("always_include", ["Order Completed"])

    if len(events) <= sample_size:
        return sorted(events, key=lambda e: e.get("time", 0))

    # Sort chronologically
    events_sorted = sorted(events, key=lambda e: e.get("time", 0))

    priority  = [e for e in events_sorted if e.get("event_type") in always_include]
    remainder = [e for e in events_sorted if e.get("event_type") not in always_include]
    budget    = sample_size - len(priority)

    if budget <= 0:
        return priority[:sample_size]

    # Preserve full sessions: group remainder by session_id
    session_map = {}
    for ev in remainder:
        sid = ev.get("event_properties", {}).get("session_id", "no_session")
        session_map.setdefault(sid, []).append(ev)

    # Pick sessions proportionally until budget is filled
    sampled = list(priority)
    for sid, sess_events in session_map.items():
        if len(sampled) >= sample_size:
            break
        if len(sampled) + len(sess_events) <= sample_size:
            sampled.extend(sess_events)
        else:
            remaining_budget = sample_size - len(sampled)
            sampled.extend(sess_events[:remaining_budget])

    return sorted(sampled, key=lambda e: e.get("time", 0))


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "Fashion_Marketplace_Tracking_Plan.xlsx"
    schema = parse_tracking_plan(path)
    print(json.dumps(schema, indent=2))
    print(f"\nParsed {schema['meta']['total_events']} events from {len(schema['meta']['sheets_parsed'])} sheets")