import streamlit as st
import pandas as pd
import requests
import json
import io
from openpyxl import load_workbook
from collections import defaultdict
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Tracking Plan Auditor",
    page_icon="🔍",
    layout="wide"
)

# ─── STYLES ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: #f8f9fa;
    border-radius: 10px;
    padding: 1rem 1.2rem;
    border-left: 4px solid #6c63ff;
    margin-bottom: 0.5rem;
}
.finding-critical { border-left-color: #e74c3c !important; }
.finding-warning  { border-left-color: #f39c12 !important; }
.finding-info     { border-left-color: #3498db !important; }
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 4px;
}
.badge-critical { background:#fde8e8; color:#c0392b; }
.badge-warning  { background:#fef3cd; color:#856404; }
.badge-info     { background:#dbeafe; color:#1e40af; }
.badge-ok       { background:#d1fae5; color:#065f46; }
</style>
""", unsafe_allow_html=True)

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def parse_tracking_plan(uploaded_file):
    """Parse the Excel tracking plan into a structured dict."""
    wb = load_workbook(uploaded_file, read_only=True)
    plan = {}  # event_name -> {properties: {name: {req, type, allowed}}}

    sheets_to_parse = ['Identify', 'Page', 'Browsing', 'Purchase Funnel',
                       'Post-Purchase', 'Marketing']

    for sheet_name in sheets_to_parse:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        current_event = None

        for row in ws.iter_rows(values_only=True):
            if not any(row):
                continue
            cell0 = str(row[0]).strip() if row[0] else ""
            cell1 = str(row[1]).strip() if row[1] else ""

            # Header rows and section separators
            if cell0 in ("Event Name", "Property", "") or cell0.isupper():
                continue
            if cell0.startswith("▶"):
                continue

            # If col0 looks like an event name and col1 looks like a property
            if cell0 and cell1 and len(row) >= 4:
                req  = str(row[2]).strip() if row[2] else "Optional"
                typ  = str(row[3]).strip() if row[3] else "string"
                allowed = str(row[5]).strip() if len(row) > 5 and row[5] else None

                # First time we see this event name
                if cell0 not in plan:
                    plan[cell0] = {"properties": {}}
                    current_event = cell0

                plan[cell0]["properties"][cell1] = {
                    "required": req == "Required",
                    "type": typ.lower(),
                    "allowed_values": [v.strip() for v in allowed.split("|")] if allowed else None
                }

    return plan


def fetch_amplitude_events(api_key, project_id, days_back=90):
    """Fetch events from Amplitude using the Dashboard REST API."""
    end   = datetime.utcnow()
    start = end - timedelta(days=days_back)

    url = "https://amplitude.com/api/2/events/list"
    try:
        r = requests.get(url, auth=(api_key, ""), timeout=15)
        if r.status_code == 200:
            return r.json().get("data", [])
        else:
            return None, f"Amplitude API error {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return None, str(e)


def fetch_amplitude_export(api_key, secret_key, days_back=7):
    """Use Amplitude Export API to pull raw events."""
    end   = datetime.utcnow()
    start = end - timedelta(days=days_back)

    url = "https://amplitude.com/api/2/export"
    params = {
        "start": start.strftime("%Y%m%dT%H"),
        "end":   end.strftime("%Y%m%dT%H"),
    }
    try:
        r = requests.get(url, params=params, auth=(api_key, secret_key), timeout=30)
        if r.status_code == 200:
            events = []
            for line in r.text.strip().split("\n"):
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except:
                        pass
            return events, None
        else:
            return None, f"Export API error {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return None, str(e)


def audit_events(events, plan):
    """Run all audit checks against the tracking plan."""
    findings = []
    stats = defaultdict(int)

    # Group events by session for sequence checks
    sessions = defaultdict(list)
    for ev in events:
        props = ev.get("event_properties", {})
        sess  = props.get("session_id", "unknown")
        sessions[sess].append(ev)

    for ev in events:
        etype = ev.get("event_type", "")
        props = ev.get("event_properties", {})
        stats["total"] += 1

        if etype not in plan:
            continue

        stats["matched"] += 1
        spec = plan[etype]["properties"]

        # ── M1: Type checks ──────────────────────────────────────────────
        for prop_name, prop_spec in spec.items():
            if prop_name not in props:
                continue
            val      = props[prop_name]
            expected = prop_spec["type"]

            if expected == "float" and isinstance(val, str):
                findings.append({
                    "severity":   "critical",
                    "code":       "M1",
                    "event":      etype,
                    "property":   prop_name,
                    "issue":      f"Price sent as string '{val}' — expected float",
                    "insert_id":  ev.get("insert_id", ""),
                    "user_id":    ev.get("user_id", ""),
                    "probable_cause": "SDK serialising numeric values as strings. Check JSON payload before send."
                })
                stats["M1"] += 1

            if expected == "integer" and not isinstance(val, int):
                try:
                    int(val)
                except:
                    findings.append({
                        "severity":   "warning",
                        "code":       "M1",
                        "event":      etype,
                        "property":   prop_name,
                        "issue":      f"Expected integer, got {type(val).__name__}: '{val}'",
                        "insert_id":  ev.get("insert_id", ""),
                        "user_id":    ev.get("user_id", ""),
                        "probable_cause": "Type coercion issue — check event builder."
                    })

        # ── M2: Missing required properties ─────────────────────────────
        for prop_name, prop_spec in spec.items():
            if prop_spec["required"] and prop_name not in props:
                findings.append({
                    "severity":   "critical",
                    "code":       "M2",
                    "event":      etype,
                    "property":   prop_name,
                    "issue":      f"Required property '{prop_name}' is missing",
                    "insert_id":  ev.get("insert_id", ""),
                    "user_id":    ev.get("user_id", ""),
                    "probable_cause": f"Property dropped before send — check {etype} event builder for '{prop_name}'."
                })
                stats["M2"] += 1

        # ── M5: discount_pct validation ──────────────────────────────────
        if "price" in props and "compare_at_price" in props and "discount_pct" in props:
            price    = props["price"]
            cap      = props["compare_at_price"]
            disc_pct = props["discount_pct"]
            if isinstance(price, (int, float)) and isinstance(cap, (int, float)) and cap > 0:
                correct = round((cap - price) / cap * 100, 1)
                if isinstance(disc_pct, (int, float)) and abs(disc_pct - correct) > 2:
                    findings.append({
                        "severity":   "warning",
                        "code":       "M5",
                        "event":      etype,
                        "property":   "discount_pct",
                        "issue":      f"discount_pct={disc_pct} but correct value is {correct}",
                        "insert_id":  ev.get("insert_id", ""),
                        "user_id":    ev.get("user_id", ""),
                        "probable_cause": "discount_pct computed separately from price/compare_at_price — centralise calculation."
                    })
                    stats["M5"] += 1

        # ── M6: is_first_order flag ──────────────────────────────────────
        if etype == "Order Completed":
            user_props = ev.get("user_properties", {})
            is_returning = user_props.get("is_returning", False)
            is_first = props.get("is_first_order", None)
            if is_returning and is_first is True:
                findings.append({
                    "severity":   "warning",
                    "code":       "M6",
                    "event":      etype,
                    "property":   "is_first_order",
                    "issue":      "is_first_order=true but user_properties.is_returning=true",
                    "insert_id":  ev.get("insert_id", ""),
                    "user_id":    ev.get("user_id", ""),
                    "probable_cause": "is_first_order derived from stale session data — pull from backend order count."
                })
                stats["M6"] += 1

    # ── M3 & M4: Session-level checks ────────────────────────────────────
    for sess_id, sess_events in sessions.items():
        event_types = [e["event_type"] for e in sess_events]

        # M4: Order Completed without Checkout Started
        if "Order Completed" in event_types and "Checkout Started" not in event_types:
            oc = next(e for e in sess_events if e["event_type"] == "Order Completed")
            findings.append({
                "severity":   "critical",
                "code":       "M4",
                "event":      "Order Completed",
                "property":   "event_sequence",
                "issue":      "Order Completed fired without Checkout Started in same session",
                "insert_id":  oc.get("insert_id", ""),
                "user_id":    oc.get("user_id", ""),
                "probable_cause": "Checkout Started may be firing on a different session_id — check session ID reset logic."
            })
            stats["M4"] += 1

        # M3: product_id consistency across funnel
        pdp_ids   = {}
        cart_ids  = {}
        for ev in sess_events:
            pid = ev.get("event_properties", {}).get("product_id")
            if not pid:
                continue
            if ev["event_type"] == "Product Viewed":
                pdp_ids[pid] = ev
            elif ev["event_type"] == "Product Added":
                cart_ids[pid] = ev

        for cart_pid, cart_ev in cart_ids.items():
            if pdp_ids and cart_pid not in pdp_ids:
                findings.append({
                    "severity":   "warning",
                    "code":       "M3",
                    "event":      "Product Added",
                    "property":   "product_id",
                    "issue":      f"product_id '{cart_pid}' in cart not seen in any Product Viewed in session",
                    "insert_id":  cart_ev.get("insert_id", ""),
                    "user_id":    cart_ev.get("user_id", ""),
                    "probable_cause": "product_id format inconsistent between PDP and cart — normalise to canonical SKU."
                })
                stats["M3"] += 1

    stats["findings"] = len(findings)
    return findings, dict(stats)


def render_report(findings, stats, plan):
    """Render the audit report in Streamlit."""

    # Summary metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Events audited",  stats.get("matched", 0))
    col2.metric("Total findings",  stats.get("findings", 0))
    col3.metric("Critical",        sum(1 for f in findings if f["severity"] == "critical"))
    col4.metric("Warnings",        sum(1 for f in findings if f["severity"] == "warning"))
    col5.metric("Plan events",     len(plan))

    st.markdown("---")

    if not findings:
        st.success("No issues found — data looks clean against the tracking plan!")
        return

    # Group by mistake code
    by_code = defaultdict(list)
    for f in findings:
        by_code[f["code"]].append(f)

    code_labels = {
        "M1": ("Type mismatch",           "critical"),
        "M2": ("Missing required prop",   "critical"),
        "M3": ("Inconsistent product_id", "warning"),
        "M4": ("Missing Checkout Started","critical"),
        "M5": ("Wrong discount_pct",      "warning"),
        "M6": ("Wrong is_first_order",    "warning"),
    }

    for code in ["M1", "M2", "M4", "M3", "M5", "M6"]:
        items = by_code.get(code, [])
        if not items:
            continue
        label, sev = code_labels.get(code, (code, "info"))
        badge_cls  = f"badge-{sev}"

        with st.expander(
            f"**{code} — {label}** &nbsp;&nbsp;"
            f'<span class="badge {badge_cls}">{len(items)} instances</span>',
            expanded=(sev == "critical")
        ):
            df = pd.DataFrame([{
                "Event":     f["event"],
                "Property":  f["property"],
                "Issue":     f["issue"],
                "User ID":   f["user_id"],
                "Insert ID": f["insert_id"][:8] + "…" if f.get("insert_id") else "",
            } for f in items])
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.markdown(f"**Probable cause:** {items[0]['probable_cause']}")


# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔍 Audit Config")
    st.markdown("---")

    st.subheader("1. Tracking plan")
    uploaded_plan = st.file_uploader(
        "Upload Excel tracking plan",
        type=["xlsx"],
        help="Your tracking plan Excel file"
    )

    st.subheader("2. Amplitude credentials")
    amplitude_api_key = st.text_input(
        "API Key",
        type="password",
        value="ea3eef799cfae7dfcd3f25cad701c6da",
        help="Amplitude project API key"
    )
    amplitude_secret = st.text_input(
        "Secret Key",
        type="password",
        help="Amplitude secret key (for Export API)"
    )

    st.subheader("3. Data range")
    days_back = st.slider("Days to audit", 1, 90, 30)

    st.markdown("---")
    run_btn = st.button("▶ Run Audit", type="primary", use_container_width=True)

    st.markdown("---")
    st.caption("Upload simulated_events.json to audit locally without Amplitude credentials.")
    local_file = st.file_uploader("Local events JSON", type=["json"])


# ─── MAIN ────────────────────────────────────────────────────────────────────
st.title("🔍 Tracking Plan Auditor")
st.caption("Compare live Amplitude data against your tracking plan and surface data quality issues.")

if not uploaded_plan and not local_file:
    st.info("Upload your tracking plan in the sidebar to get started.")

    with st.expander("What does this tool check?"):
        st.markdown("""
| Code | Check | Severity |
|------|-------|----------|
| **M1** | Price or numeric fields sent as strings instead of floats/integers | Critical |
| **M2** | Required properties missing from events | Critical |
| **M3** | `product_id` inconsistent between Product Viewed and Product Added in the same session | Warning |
| **M4** | Order Completed fires without a Checkout Started in the same session | Critical |
| **M5** | `discount_pct` mathematically wrong vs actual `price` and `compare_at_price` | Warning |
| **M6** | `is_first_order: true` for users with prior orders | Warning |
        """)
    st.stop()

# Parse the tracking plan
plan = {}
if uploaded_plan:
    with st.spinner("Parsing tracking plan..."):
        plan = parse_tracking_plan(uploaded_plan)
    st.success(f"Tracking plan loaded — {len(plan)} events defined")

    with st.expander("Preview tracking plan"):
        for event, spec in plan.items():
            req_props = [p for p, s in spec["properties"].items() if s["required"]]
            opt_props = [p for p, s in spec["properties"].items() if not s["required"]]
            st.markdown(f"**{event}** — required: `{'`, `'.join(req_props)}`")

if run_btn or local_file:
    events = []

    # ── Load from local JSON ──────────────────────────────────────────────
    if local_file:
        with st.spinner("Loading local events..."):
            raw = json.load(local_file)
            # Handle both flat list and nested formats
            if isinstance(raw, list):
                events = raw
            st.success(f"Loaded {len(events):,} events from local file")

    # ── Or fetch from Amplitude ───────────────────────────────────────────
    elif run_btn and amplitude_api_key:
        with st.spinner("Fetching events from Amplitude..."):
            if amplitude_secret:
                events, err = fetch_amplitude_export(amplitude_api_key, amplitude_secret, days_back)
            else:
                # Fall back to events list API
                result = fetch_amplitude_events(amplitude_api_key, "", days_back)
                if isinstance(result, tuple):
                    events, err = result
                else:
                    events, err = result, None

            if err:
                st.error(f"Could not fetch from Amplitude: {err}")
                st.info("Tip: Upload simulated_events.json locally to audit without API credentials.")
                st.stop()
            elif events:
                st.success(f"Fetched {len(events):,} events from Amplitude")

    if not events:
        st.warning("No events to audit. Upload a local JSON file or check your Amplitude credentials.")
        st.stop()

    if not plan:
        st.warning("No tracking plan loaded. Upload your Excel file in the sidebar.")
        st.stop()

    # ── Run audit ─────────────────────────────────────────────────────────
    with st.spinner("Running audit checks..."):
        findings, stats = audit_events(events, plan)

    st.markdown("## Audit Report")
    st.caption(f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC · {len(events):,} events · {len(plan)} plan events")

    render_report(findings, stats, plan)

    # ── Download report ───────────────────────────────────────────────────
    if findings:
        st.markdown("---")
        report_df = pd.DataFrame(findings)
        csv = report_df.to_csv(index=False)
        st.download_button(
            "⬇ Download findings CSV",
            data=csv,
            file_name=f"audit_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv"
        )