# app_v2.py
"""
Kaliper V2 - Pure Visualization Layer (Streamlit)

A professional analytics governance dashboard that consumes the 
'audit_output.json' contract. Follows the "Deterministic First" model.

Responsibilities:
1. Visualize pre-computed audit results.
2. Provide drill-down exploration.
3. Trigger AI Diagnosis (Pure Reasoning).
"""

import streamlit as st
import json
import os
import pandas as pd
import plotly.express as px
from datetime import datetime
from scheduler_v2 import run_pipeline
from groq_agent_v2 import GroqAgentV2

st.set_page_config(page_title="Kaliper V2 | Governance Dashboard", layout="wide")

# --- CUSTOM STYLING ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');
    html, body, [class*="css"] { font-family: 'Outfit', sans-serif; }
    .stApp { background: #0f1116; color: #f0f2f6; }
    .header {
        background: rgba(255, 255, 255, 0.02); padding: 1.5rem; border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.05); text-align: center; margin-bottom: 2rem;
    }
    .metric-card {
        background: #1a1c22; border: 1px solid rgba(255,255,255,0.05);
        padding: 1.25rem; border-radius: 10px; text-align: center;
    }
    .score-high { color: #00ffaa; }
    .score-med  { color: #ffcc00; }
    .score-low  { color: #ff4444; }
</style>
""", unsafe_allow_html=True)

# --- DATA SELECTION ---
OUTPUT_FILE = "audit_output.json"

def load_data():
    if not os.path.exists(OUTPUT_FILE):
        return None
    with open(OUTPUT_FILE, "r") as f:
        return json.load(f)

data = load_data()

# --- HEADER ---
st.markdown("<div class='header'><h1>K A L I P E R <span style='color:#4facfe'>V2</span></h1><p>Deterministic Analytics Governance System</p></div>", unsafe_allow_html=True)

if not data:
    st.warning("No audit data found. Please run the pipeline to generate the first report.")
    if st.button("🚀 Run Initial Audit"):
        with st.spinner("Executing Deterministic Pipeline..."):
            run_pipeline(mode="simulation")
            st.rerun()
    st.stop()

summary = data["summary"]
metadata = data["metadata"]

# --- TOP METRICS ---
m1, m2, m3, m4 = st.columns(4)

score = summary["health_score"]
color_class = "score-high" if score > 90 else "score-med" if score > 70 else "score-low"

with m1: 
    st.markdown(f"<div class='metric-card'><p>Health Score</p><h1 class='{color_class}'>{score}%</h1></div>", unsafe_allow_html=True)
with m2:
    st.markdown(f"<div class='metric-card'><p>Total Events</p><h2>{summary['total_events']:,}</h2></div>", unsafe_allow_html=True)
with m3:
    st.markdown(f"<div class='metric-card'><p>Unknown Platform</p><h2 style='color:#ffcc00'>{summary['unknown_platform_pct']}%</h2></div>", unsafe_allow_html=True)
with m4:
    st.markdown(f"<div class='metric-card'><p>Total Issues</p><h2>{summary['total_issues']}</h2></div>", unsafe_allow_html=True)

st.markdown("---")

# --- TREND & OVERVIEW ---
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("📈 Health Score Trend")
    trend_data = data.get("trend", [])
    if trend_data:
        df_trend = pd.DataFrame({"Run": range(len(trend_data)), "Score": trend_data})
        fig = px.line(df_trend, x="Run", y="Score", template="plotly_dark", markers=True)
        fig.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No trend data available yet.")

with c2:
    st.subheader("📦 Audit Metadata")
    st.write(f"**Mode:** `{metadata['mode']}`")
    st.write(f"**Window:** `{metadata['window_days']} days`")
    st.write(f"**Last Sync:** `{metadata['timestamp'][:19]}`")
    st.write(f"**Duration:** `{metadata['success_duration']}s`")
    
    if st.button("🔄 Refresh Data (Live Audit)"):
        with st.spinner("Syncing with Amplitude..."):
            run_pipeline(mode="simulation") # Fixed to simulation for this demo env
            st.rerun()

st.markdown("---")

# --- ISSUE EXPLORATION ---
st.subheader("🛡️ Strategic Issue Registry")

# Issues Table
issues = data["issues"]
if issues:
    df_issues = pd.DataFrame(issues)
    
    # Filtering
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        f_lifecycle = st.multiselect("LifeCycle", ["New", "Persistent", "Regression"], default=["New", "Persistent", "Regression"])
    with col_f2:
        f_platform = st.multiselect("Platform", list(df_issues["platform"].unique()), default=list(df_issues["platform"].unique()))
    with col_f3:
        f_code = st.multiselect("Issue Code", list(df_issues["code"].unique()), default=list(df_issues["code"].unique()))

    filtered_df = df_issues[
        (df_issues["lifecycle"].isin(f_lifecycle)) &
        (df_issues["platform"].isin(f_platform)) &
        (df_issues["code"].isin(f_code))
    ]

    st.dataframe(
        filtered_df[["lifecycle", "code", "event", "property", "platform", "unique_events", "blast_radius", "weighted_penalty", "dedup_key"]],
        use_container_width=True,
        hide_index=True
    )

    # --- DRILL DOWN & DIAGNOSIS ---
    st.markdown("---")
    st.subheader("🔍 Localized Diagnostic")
    
    selected_key = st.selectbox("Select Issue for Deep-Dive Analysis", filtered_df["dedup_key"].unique())
    
    if selected_key:
        issue_details = [i for i in issues if i["dedup_key"] == selected_key][0]
        
        d1, d2 = st.columns([1, 1])
        with d1:
            st.info(f"**Error Message:** {issue_details['example_issue']}")
            st.write(f"**Total Impact:** {issue_details['count']} total occurrences")
            st.write(f"**Blast Radius:** {issue_details['blast_radius']}% of total traffic")
        
        with d2:
            if st.button("🧬 Trigger Groq Llama3 Diagnosis"):
                with st.spinner("AI reasoning in progress..."):
                    agent = GroqAgentV2()
                    diagnosis = agent.diagnose(issue_details, summary)
                    
                    if "error" in diagnosis:
                        st.error(diagnosis["error"])
                    else:
                        st.success("Analysis Complete")
                        st.markdown(f"### 🛡️ Root Cause\n{diagnosis['root_cause']}")
                        st.markdown(f"### 📊 Business Impact\n{diagnosis['impact']}")
                        st.markdown(f"### 🛠️ Suggested Fix\n{diagnosis['suggested_fix']}")

else:
    st.success("No issues detected! Your tracking plan is 100% compliant.")

st.markdown("<div style='text-align:center; padding:2rem; opacity:0.3;'>Kaliper V2 - Deterministic Governance System</div>", unsafe_allow_html=True)
