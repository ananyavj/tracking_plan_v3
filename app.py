# app.py
import streamlit as st
import streamlit.components.v1 as components
import os
import json
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, date
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)
from groq_agent import run_groq_audit_agent
from audit_engine import AuditEngine
from utils import _extract_event_time, parse_amplitude_time
import mcp_tools
import alert_engine

# --- THEME & AESTHETICS ---
st.set_page_config(
    page_title="Kaliper | Hybrid Tracking Auditor",
    layout="wide"
)

CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');
    html, body, [class*="css"] { font-family: 'Outfit', sans-serif; }
    .stApp { background: radial-gradient(circle at top right, #1a1a2e, #16213e, #0f3460); color: #e94560; }
    .main-header {
        background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1); padding: 2rem; border-radius: 20px;
        margin-bottom: 2rem; text-align: center; box-shadow: 0 8px 32px 0 rgba(0,0,0,0.37);
    }
    .main-header h1 {
        background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        font-weight: 800; font-size: 3.5rem;
    }
    .metric-card {
        background: rgba(255,255,255,0.03); border-left: 5px solid #4facfe;
        padding: 1.5rem; border-radius: 12px; transition: all 0.3s ease;
    }
    .metric-card:hover { transform: translateY(-5px); background: rgba(255,255,255,0.07); }
    .stButton>button {
        background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%); color: white !important;
        border: none; padding: 0.8rem 2rem; border-radius: 50px; font-weight: 600;
        letter-spacing: 1px; transition: all 0.3s ease; text-transform: uppercase;
        box-shadow: 0 4px 15px rgba(79,172,254,0.4);
    }
    .stButton>button:hover { transform: scale(1.05); box-shadow: 0 6px 20px rgba(79,172,254,0.6); }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# --- STATE INITIALIZATION ---
metadata = mcp_tools.load_audit_metadata()

# Version sentinel — bump this string any time session_state keys or message
# formats change. On first run after a code change, old cached values are wiped
# cleanly instead of showing stale text from the previous version.
_STATE_VERSION = "v8"
if st.session_state.get("_state_version") != _STATE_VERSION:
    for _k in ["recency_msg", "snap_msg", "events_list", "audit_summary",
               "audit_issues", "alerts", "ai_analysis", "tracking_plan_gaps",
               "start_date", "end_date", "snap_data_start", "snap_data_end",
               "date_filter_active"]:
        st.session_state.pop(_k, None)
    st.session_state["_state_version"] = _STATE_VERSION

if "recency_msg"            not in st.session_state: st.session_state.recency_msg = None
if "snap_msg"               not in st.session_state: st.session_state.snap_msg = None
if "events_list"            not in st.session_state: st.session_state.events_list = None
if "audit_summary"          not in st.session_state: st.session_state.audit_summary = None
if "audit_issues"           not in st.session_state: st.session_state.audit_issues = []
if "alerts"                 not in st.session_state: st.session_state.alerts = []
if "ai_analysis"            not in st.session_state: st.session_state.ai_analysis = None
if "tracking_plan_gaps"     not in st.session_state: st.session_state.tracking_plan_gaps = []

if "snap_data_start" not in st.session_state: st.session_state.snap_data_start = None
if "snap_data_end"   not in st.session_state: st.session_state.snap_data_end   = None

# Date anchor: default to March 2026 (where simulation data lives).
if "start_date" not in st.session_state:
    st.session_state.start_date = date(2026, 3, 1)
if "end_date" not in st.session_state:
    st.session_state.end_date = date(2026, 3, 31)

# --- SIDEBAR ---
with st.sidebar:
    st.title("Kaliper Auditor")
    st.caption("Hybrid Intelligence Strategy")
    st.markdown("---")

    data_mode = st.radio("Event Source", ["Live Amplitude Fetch", "Local JSON Upload"], index=0)
    uploaded_plan = st.file_uploader("Tracking Plan (Excel)", type=["xlsx"])

    uploaded_events = None
    if data_mode == "Local JSON Upload":
        uploaded_events = st.file_uploader("Live Events (JSON)", type=["json"])
        use_defaults = st.toggle("Use project template files", value=True)
    else:
        use_defaults = False

    st.markdown("---")
    groq_key = st.text_input("Groq API Key", type="password", value=os.getenv("GROQ_API_KEY", ""))

    # Groq setup guide
    if groq_key == "gsk_your_free_key_here":
        st.warning(
            "⚠️ **Default Groq Key detected**.\n\n"
            "Please visit [console.groq.com](https://console.groq.com) "
            "to get your own free API key and paste it here or update your .env file."
        )

    amp_api_key    = os.getenv("AMPLITUDE_API_KEY", "")
    amp_secret_key = os.getenv("AMPLITUDE_SECRET_KEY", "")
    amp_project_id = os.getenv("AMPLITUDE_PROJECT_ID", "")

    st.subheader("Amplitude Settings")
    amp_api_key    = st.text_input("Amplitude API Key",    type="password", value=amp_api_key)
    amp_secret_key = st.text_input("Amplitude Secret Key", type="password", value=amp_secret_key)
    amp_project_id = st.text_input("Amplitude Project ID", value=amp_project_id)

    # FIX 1 cont. — Date inputs read from session_state which is now anchored
    # to March 2026. Removing the key= parameters prevents widget-override
    # conflicts on reruns.
    # Date pickers are optional overrides — only shown if user wants to narrow the window.
    # Run Full Volume Audit uses snap_data_start/end (the full discovered range) by default
    # and only falls back to these pickers if the user explicitly changes them.
    with st.expander("🗓️ Filter by date range (optional)", expanded=False):
        st.caption("Leave collapsed to audit your full data range automatically.")
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            start_date = st.date_input("Start Date", value=st.session_state.start_date)
            st.session_state.start_date = start_date
        with col_d2:
            end_date = st.date_input("End Date", value=st.session_state.end_date)
            st.session_state.end_date = end_date
        st.session_state.date_filter_active = True
    if "date_filter_active" not in st.session_state:
        st.session_state.date_filter_active = False

    st.markdown("---")
    st.subheader("AI Strategy")
    force_pro = st.toggle("High-Precision Mode", help="Use Llama 3 70B for deeper reasoning. Slower but more accurate.")
    if force_pro:
        st.caption("ℹ️ Uses advanced reasoning for complex cross-device diagnostics.")


    col_disc1, col_disc2 = st.columns(2)
    with col_disc1:
        # CHECK PIPELINE HEALTH
        # Purpose: "Is Amplitude receiving my events right now?"
        # Logic: fetches last 7 days, reads server_received_time (ingestion timestamp).
        # Tells you when YOUR SERVER last successfully delivered an event to Amplitude.
        # Does NOT update the date pickers — it's a read-only health probe.
        if st.button("Check Pipeline Health", use_container_width=True):
            if not amp_api_key or not amp_secret_key:
                st.error("Missing keys.")
            else:
                # Clear the other button's message so they never show simultaneously
                st.session_state.snap_msg = None
                with st.spinner("Scouting..."):
                    st.toast("Checking Amplitude ingestion health...")
                    res = mcp_tools.get_amplitude_events(days_back=7, api_key=amp_api_key, secret_key=amp_secret_key)
                    if "error" in res:
                        st.session_state.recency_msg = None
                        st.error(res["error"])
                    elif "events" in res and res["events"]:
                        events = res["events"]
                        # server_received_time = when Amplitude stamped the event on arrival.
                        # Measures ingestion pipeline latency, NOT when the event logically occurred.
                        ingestion_times = [
                            e.get('server_received_time') or e.get('server_upload_time') or e.get('time', 0)
                            for e in events
                        ]
                        latest_ts  = max(ingestion_times)
                        latest_dt  = parse_amplitude_time(latest_ts)
                        earliest_ts = min(ingestion_times)
                        earliest_dt = parse_amplitude_time(earliest_ts)
                        event_count = len(events)
                        if latest_dt and earliest_dt:
                            st.session_state.recency_msg = (
                                f"✅ Pipeline Healthy — {event_count:,} events ingested in the last 7 days. "
                                f"First arrived: {earliest_dt.strftime('%Y-%m-%d %H:%M')} · "
                                f"Last arrived: {latest_dt.strftime('%Y-%m-%d %H:%M')}"
                            )
                            st.rerun()
                    else:
                        st.session_state.recency_msg = "⚠️ No events ingested in the last 7 days — pipeline may be down."
                        st.rerun()

    with col_disc2:
        # SNAP TO LATEST EVENTS
        # Purpose: "Where does my actual data live? Update the date pickers to match."
        # Logic: fetches last 31 days, reads the event's own `time` field (logical event time).
        # Tells you the timestamp of the newest event in your dataset, then shifts
        # Start/End date pickers to a 7-day window ending on that date.
        # Does NOT tell you about ingestion health — only about data location.
        if st.button("Snap to Latest Events", use_container_width=True):
            if not amp_api_key or not amp_secret_key:
                st.error("Missing keys.")
            else:
                # Clear the other button's message so they never show simultaneously
                st.session_state.recency_msg = None
                with st.spinner("Scanning..."):
                    st.toast("Finding the most recent event timestamps...")
                    res = mcp_tools.get_amplitude_events(days_back=31, api_key=amp_api_key, secret_key=amp_secret_key)
                    if "error" in res:
                        st.session_state.snap_msg = None
                        st.error(res["error"])
                    elif "events" in res and res["events"]:
                        events = res["events"]
                        # After normalisation in mcp_tools, every event has a 'time' int (ms).
                        # Also check 'event_time' string as a belt-and-suspenders fallback.
                        event_times = []
                        for e in events:
                            t = e.get('time')
                            if t and isinstance(t, (int, float)) and t > 0:
                                event_times.append(t)
                            else:
                                raw = e.get('event_time')
                                if raw:
                                    try:
                                        dt = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
                                        event_times.append(int(dt.timestamp() * 1000))
                                    except Exception:
                                        pass
                        if event_times:
                            latest_ts   = max(event_times)
                            earliest_ts = min(event_times)
                            latest_dt   = parse_amplitude_time(latest_ts)
                            earliest_dt = parse_amplitude_time(earliest_ts)
                            if latest_dt:
                                new_end   = latest_dt.date()
                                new_start = (latest_dt - timedelta(days=7)).date()
                                st.session_state.end_date   = new_end
                                st.session_state.start_date = new_start
                                # Store the full data span so the user can widen to it
                                st.session_state.snap_data_start = earliest_dt.date() if earliest_dt else new_start
                                st.session_state.snap_data_end   = new_end
                                data_start_str = earliest_dt.strftime('%Y-%m-%d') if earliest_dt else '?'
                                data_end_str   = latest_dt.strftime('%Y-%m-%d')
                                st.session_state.snap_msg = (
                                    f"📅 Date pickers updated → {new_start} to {new_end}. "
                                    f"(Your data spans {data_start_str} – {data_end_str}; "
                                    f"window anchored to the most recent 7 days.)"
                                )
                                st.rerun()
                        else:
                            st.session_state.snap_msg = (
                                "⚠️ Events were returned but none had a readable timestamp. "
                                "Check that your Amplitude project is sending event_time or time fields."
                            )
                            st.rerun()
                    else:
                        st.session_state.snap_msg = "⚠️ No events found in the last 31 days. Check your Amplitude credentials."
                        st.rerun()

    # Two separate banners: health check result (pipeline status) and snap result (date info).
    # Keeping them separate means clicking one button never silently erases the other's output.
    if st.session_state.recency_msg:
        st.info(st.session_state.recency_msg)
    if st.session_state.snap_msg:
        st.success(st.session_state.snap_msg)

    # If Snap found a full data range wider than the 7-day window, offer a one-click expand
    # Clicking this button directly triggers the audit — no second click needed.
    if (st.session_state.snap_data_start and st.session_state.snap_data_end and
            st.session_state.snap_data_start < st.session_state.start_date):
        if st.button(
            f"📅 Audit Full Range ({st.session_state.snap_data_start} → {st.session_state.snap_data_end})",
            use_container_width=True
        ):
            st.session_state.start_date       = st.session_state.snap_data_start
            st.session_state.end_date         = st.session_state.snap_data_end
            st.session_state.date_filter_active = True
            st.session_state._trigger_audit   = True
            st.rerun()

# --- DATA RESOLUTION ---
tp_path = "tracking_plan.xlsx"
if uploaded_plan:
    with open("temp_tp.xlsx", "wb") as f: f.write(uploaded_plan.getbuffer())
    tp_path = "temp_tp.xlsx"

if data_mode == "Local JSON Upload":
    if uploaded_events:
        try:
            st.session_state.events_list = json.load(uploaded_events)
        except Exception as e:
            st.error(f"Failed to parse uploaded JSON: {e}")
    elif use_defaults and st.session_state.events_list is None:
        if os.path.exists("simulated_events.json"):
            with open("simulated_events.json", "r") as f:
                st.session_state.events_list = json.load(f)

# --- MAIN UI ---
st.markdown("<div class='main-header'><h1>K A L I P E R</h1><p>Autonomous Analytics Validation Engine</p></div>", unsafe_allow_html=True)

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Execute Professional Audit")
    run_btn = st.button("Run Full Volume Audit", use_container_width=True)
with col2:
    st.subheader("Strategic Insight")
    _ai_ready = bool(st.session_state.audit_summary)
    ai_btn = st.button("Diagnose with AI", disabled=not _ai_ready, use_container_width=True)
    if not _ai_ready:
        st.caption("Run an audit first to enable.")

if ai_btn:
    if not groq_key or groq_key == "gsk_your_free_key_here":
        st.error("Please provide a Groq API Key in the sidebar.")
    elif not st.session_state.audit_summary:
        st.error("No audit results found. Please run a Full Volume Audit first.")
    else:
        # Use cached events if available, otherwise empty list (agent will use audit issues)
        _events_for_agent = st.session_state.events_list or []
        
        print("[app] AI Diagnosis triggered...")
        with st.spinner("Groq Llama3 Diagnostician at work..."):
            try:
                # Build clustered findings from stored audit issues
                _clusters = {}
                for _issue in (st.session_state.audit_issues or []):
                    _dk = _issue.get("dedup_key")
                    if _dk not in _clusters:
                        _clusters[_dk] = {
                            "dedup_key": _dk,
                            "code": _issue.get("code"),
                            "event": _issue.get("event"),
                            "property": _issue.get("property"),
                            "platform": _issue.get("platform"),
                            "count": 0,
                            "example_issue": _issue.get("issue")
                        }
                    _clusters[_dk]["count"] += 1

                findings = {
                    "status": "success",
                    "summary": st.session_state.audit_summary,
                    "issue_count": len(st.session_state.audit_issues or []),
                    "clustered_findings": list(_clusters.values())
                }

                if not findings["clustered_findings"]:
                    st.info("No issues found in your data — your tracking plan looks clean! Nothing to diagnose.")
                else:
                    print(f"[app] Clustering successful. {len(findings['clustered_findings'])} clusters. Starting agent...")
                    with open("SKILL.md", "r") as f:
                        system_prompt = f.read()

                    temp_engine = AuditEngine(tp_path, _events_for_agent) if _events_for_agent else None
                    _tp = temp_engine.tracking_plan if temp_engine else {}

                    agent = run_groq_audit_agent(
                        groq_api_key=groq_key,
                        system_prompt=system_prompt,
                        tracking_plan=_tp,
                        clustered_findings=findings,
                        events=_events_for_agent,
                        app_config={
                            "api_key": amp_api_key,
                            "secret_key": amp_secret_key,
                            "project_id": amp_project_id
                        },
                        force_pro=force_pro
                    )

                    for step in agent:
                        if step["type"] == "report":
                            st.session_state.ai_analysis = step["report"]
                            st.session_state.tracking_plan_gaps = step["report"].get("tracking_plan_gaps", [])
                            st.toast("AI Diagnosis complete!")
                        elif step["type"] == "tool_call":
                            pass
                        elif step["type"] == "error":
                            # FIX 2 cont. — Surface Vertex key sentinel with a friendly message.
                            if step.get("error") == "VERTEX_KEY_DETECTED":
                                st.error(
                                    "Vertex AI key detected inside the agent. "
                                    "Please switch to an AI Studio key (AIza...) in the sidebar."
                                )
                            else:
                                st.error(f"AI Agent Error: {step['error']}")
            except Exception as e:
                import traceback
                st.error("AI Strategy Failure: A diagnostic crash occurred.")
                with st.expander("Show Traceback"):
                    st.code(traceback.format_exc())

# Allow the sidebar "Audit Full Range" button to directly fire the audit
_audit_triggered = run_btn or st.session_state.pop("_trigger_audit", False)

if _audit_triggered:
    if data_mode == "Live Amplitude Fetch" and (not amp_api_key or not amp_secret_key):
        st.error("Missing Amplitude API credentials.")
        st.stop()
    else:
        with st.spinner("Harvesting and Scanning Data..."):
            try:
                if data_mode == "Live Amplitude Fetch":
                    # DEFAULT BEHAVIOUR (no date filter set by user):
                    # Use the full data range discovered by Snap — fetch everything,
                    # audit everything. The user doesn't need to touch any date picker.
                    #
                    # OVERRIDE BEHAVIOUR (user opened the date filter expander):
                    # Respect start_date/end_date and filter after fetching.

                    today = datetime.utcnow().date()

                    if st.session_state.snap_data_start and not st.session_state.date_filter_active:
                        # Full-range mode: fetch from the true data origin Snap discovered
                        fetch_from = st.session_state.snap_data_start
                        fetch_to   = st.session_state.snap_data_end
                    else:
                        # Filtered mode: user explicitly set dates
                        fetch_from = st.session_state.start_date
                        fetch_to   = st.session_state.end_date

                    days_back = max((today - fetch_from).days + 1, 1)
                    days_back = min(days_back, 90)

                    res = mcp_tools.get_amplitude_events(
                        days_back=days_back,
                        api_key=amp_api_key,
                        secret_key=amp_secret_key
                    )
                    if "error" in res:
                        st.error(f"Amplitude Fetch Failed: {res['error']}")
                        st.stop()

                    all_fetched = res["events"]

                    if st.session_state.date_filter_active:
                        # Apply the user's date filter
                        sel_start = datetime.combine(fetch_from, datetime.min.time())
                        sel_end   = datetime.combine(fetch_to,   datetime.max.time())
                        filtered = [e for e in all_fetched
                                    if (dt := _extract_event_time(e)) and sel_start <= dt <= sel_end]
                        st.session_state.events_list = filtered if filtered else all_fetched
                        st.info(
                            f"📊 Fetched **{len(all_fetched):,}** events — "
                            f"**{len(filtered):,}** match your filter "
                            f"({fetch_from} → {fetch_to})."
                            + (f" {len(all_fetched)-len(filtered):,} outside range were excluded."
                               if len(all_fetched) != len(filtered) else "")
                        )
                    else:
                        # No filter — clip only to the snap range so we don't include
                        # today's partial data beyond snap_data_end
                        if fetch_to:
                            sel_end = datetime.combine(fetch_to, datetime.max.time())
                            sel_start = datetime.combine(fetch_from, datetime.min.time())
                            all_fetched = [e for e in all_fetched
                                           if (dt := _extract_event_time(e)) and sel_start <= dt <= sel_end]
                        st.session_state.events_list = all_fetched
                        st.info(
                            f"📊 Fetched **{len(all_fetched):,}** events — "
                            f"full range **{fetch_from} → {fetch_to}**. Auditing all of them."
                        )

                events = st.session_state.events_list

                if not events:
                    if data_mode == "Live Amplitude Fetch":
                        st.error(
                            f"No events returned from Amplitude for "
                            f"{st.session_state.start_date} → {st.session_state.end_date}. "
                            f"This usually means: (1) no data exists in Amplitude for that date range, "
                            f"or (2) the Export API returned 404 for all chunks (data not yet available). "
                            f"Try clicking **Snap to Latest Events** to find where your data actually lives."
                        )
                    else:
                        st.error("No events found to audit. Check your uploaded JSON file.")
                    st.stop()
                else:
                    engine  = AuditEngine(tp_path, events)
                    summary, issues = engine.run_all_checks()
                    st.session_state.audit_summary = summary
                    st.session_state.audit_issues = issues

                    # FIX 5 — persist the audit date so the p2_audit_gap alert resets correctly
                    mcp_tools.save_audit_metadata(amp_project_id or "LOCAL", datetime.now())

                    try:
                        triggered = alert_engine.evaluate_alerts(summary, metadata, amp_project_id or "LOCAL")
                        st.session_state.alerts = triggered
                        if triggered:
                            alert_engine.dispatch_alerts(triggered, summary, {
                                "slack_webhook": os.getenv("SLACK_WEBHOOK_URL"),
                                "project_name":  f"Kaliper [{amp_project_id or 'LOCAL'}]",
                            })
                    except: pass
                    st.toast("Audit complete!")
            except Exception as e:
                st.error(f"System Failure: {str(e)}")

if st.session_state.audit_summary:
    s = st.session_state.audit_summary
    m1, m2, m3, m4 = st.columns(4)
    with m1: st.markdown(f'<div class="metric-card"><p>Total Events</p><h2>{s["total_events"]:,}</h2></div>', unsafe_allow_html=True)
    with m2: st.markdown(f'<div class="metric-card" style="border-left-color:red;"><p>Critical Issues</p><h2>{s["critical_issues"]:,}</h2></div>', unsafe_allow_html=True)
    with m3: st.markdown(f'<div class="metric-card" style="border-left-color:yellow;"><p>Warnings</p><h2>{s["warning_issues"]:,}</h2></div>', unsafe_allow_html=True)
    with m4: st.markdown(f'<div class="metric-card" style="border-left-color:cyan;"><p>Health</p><h2>{s.get("health_score", 0):.1f}%</h2></div>', unsafe_allow_html=True)

    if st.session_state.alerts:
        st.subheader("Active Alerts")
        for alert in st.session_state.alerts:
            st.error(f"[{alert['severity']}] {alert['description']}")

    st.markdown("---")
    v1, v2 = st.columns([1, 1])
    with v1:
        st.subheader("Issue Distribution")
        df_dist = pd.DataFrame([{"Check": k, "Count": v["count"]} for k, v in s["by_check"].items() if v["count"] > 0])
        if not df_dist.empty:
            fig = px.bar(df_dist, x="Check", y="Count", template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)
    with v2:
        st.subheader("Issue Detail Log")
        if st.session_state.audit_issues:
            df_issues = pd.DataFrame(st.session_state.audit_issues)
            st.dataframe(df_issues[["code","severity","event","property","issue"]].head(1000), use_container_width=True)

    # --- AI DIAGNOSIS RENDERING ---
    if st.session_state.ai_analysis and "html_report" in st.session_state.ai_analysis:
        st.markdown("---")

        meta = st.session_state.ai_analysis.get("audit_meta", {})

        # FIX 4 — Honest "Ground-Verified" badge: distinguish between tool calls
        # that verified data inside the audited batch vs. get_user_history calls
        # that fetched supplemental data from outside the batch.
        verified_flag = meta.get("verified", False)
        ext_history   = meta.get("external_history_used", False)

        if verified_flag and not ext_history:
            verified_tag = " ✅ Ground-Verified (audited batch)"
        elif verified_flag and ext_history:
            verified_tag = " 🔍 Verified + Supplemental User History"
        else:
            verified_tag = ""

        st.caption(
            f"🤖 **Model:** {meta.get('model','unknown')} | "
            f"**Iterations:** {meta.get('iterations','-')} | "
            f"**Termination:** {meta.get('termination','-')}"
            f"{verified_tag}"
        )

        ra1, ra2 = st.columns([3, 1])
        with ra1:
            st.subheader("🤖 AI Diagnostic Report")
        with ra2:
            st.download_button(
                label="Download Report",
                data=st.session_state.ai_analysis["html_report"],
                file_name=f"Kaliper_Audit_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                mime="text/html",
                use_container_width=True
            )

        components.html(st.session_state.ai_analysis["html_report"], height=2500, scrolling=True)

        if meta.get("tool_trace"):
            with st.expander("🔍 Evidence & Verification (Data Trace)"):
                for t in meta["tool_trace"]:
                    # FIX 4 cont. — Label each tool call with its data provenance.
                    source_label = (
                        " *(supplemental — outside audited batch)*"
                        if t.get("external")
                        else " *(audited batch)*"
                    )
                    st.markdown(f"**Tool:** `{t['tool']}` | **Target:** `{t['target']}`{source_label}")
                    st.info(f"**Observation:** {t['observation']}")

    if st.session_state.tracking_plan_gaps:
        st.subheader("📋 Predicted Tracking Plan Gaps")
        for gap in st.session_state.tracking_plan_gaps:
            with st.expander(f"Event: {gap.get('event_name', 'Unknown')}"):
                st.write(f"**Verdict:** {gap.get('verdict')}")
                st.write(f"**Reason:** {gap.get('reason')}")

    if st.session_state.audit_issues:
        with st.expander("Export Raw Audit Findings"):
            csv = pd.DataFrame(st.session_state.audit_issues).to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV Log", data=csv, file_name="audit_issues.csv", mime="text/csv")

# Footer
st.markdown("<div style='text-align:center; margin-top:5rem; opacity:0.5;'>Kaliper v3.0 - ASCII Hardened</div>", unsafe_allow_html=True)
