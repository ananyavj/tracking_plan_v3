# alert_engine.py
"""
Phase 3 Alert Engine

Evaluates audit summaries against business-weighted threshold rules and
dispatches real-time alerts to Slack.
"""

import os
import json
import requests
from datetime import datetime
import mcp_tools

def _get_trend_indicator():
    """Returns a visual trend bar of the last 5 scores."""
    history = mcp_tools.get_health_trend(limit=5)
    if not history: return ""
    
    bar = []
    for entry in history:
        score = entry.get("health_score", 100)
        if score >= 90: bar.append("🟢")
        elif score >= 75: bar.append("🟡")
        else: bar.append("🔴")
    return " ".join(bar)

def should_alert(summary, prev_summary):
    """
    Quiet Mode Logic: Only return True if:
    - Health dropped > 2%
    - New critical issues appeared
    - Volume of issues increased significantly
    """
    if not prev_summary: return True # Always alert on first run
    
    curr_h = summary.get("health_score", 100)
    prev_h = prev_summary.get("health_score", 100)
    threshold = float(os.getenv("HEALTH_DROP_THRESHOLD", "2.0"))
    
    if curr_h < (prev_h - threshold): return True
    if summary.get("critical_issues", 0) > prev_summary.get("critical_issues", 0): return True
    
    # Volume spike (total issues increased > 10%)
    curr_i = summary.get("total_issues", 0)
    prev_i = prev_summary.get("total_issues", 0)
    if prev_i > 0 and (curr_i / prev_i) > 1.1: return True
    
    return False

ALERT_RULES = [
    {
        "id":          "p0_revenue_missing_props",
        "severity":    "P0",
        "description": "M2 critical: missing required props on Order Completed - revenue data at risk",
        "condition":   lambda s, meta, pid: _m2_on_event(s, "Order Completed") > 5,
        "channels":    ["slack"],
        "label":       "[CRITICAL]",
        "emoji":       "🔴",
    },
    {
        "id":          "p0_funnel_break_rate",
        "severity":    "P0",
        "description": "M4 funnel breaks exceed 2% - checkout pipeline broken",
        "condition":   lambda s, meta, pid: _m4_rate(s) > 0.02,
        "channels":    ["slack"],
        "label":       "[CRITICAL]",
        "emoji":       "🔴",
    },
    {
        "id":          "p1_schema_drift",
        "severity":    "P1",
        "description": "M0 unknown events detected - possible new feature or tracking regression",
        "condition":   lambda s, meta, pid: s.get("by_check", {}).get("M0", {}).get("count", 0) > 0,
        "channels":    ["slack"],
        "label":       "[WARNING]",
        "emoji":       "⚠️",
    },
    {
        "id":          "p1_type_mismatch_spike",
        "severity":    "P1",
        "description": "M1 type mismatches exceed 3% of events - serialization issues",
        "condition":   lambda s, meta, pid: (
            s.get("by_check", {}).get("M1", {}).get("count", 0)
            / max(s.get("total_events", 1), 1)
        ) > 0.03,
        "channels":    ["slack"],
        "label":       "[WARNING]",
        "emoji":       "⚠️",
    },
    {
        "id":          "p2_health_degraded",
        "severity":    "P2",
        "description": "Data health score fell below 75%",
        "condition":   lambda s, meta, pid: s.get("health_score", 100) < 75,
        "channels":    ["slack"],
        "label":       "[INFO]",
        "emoji":       "ℹ️",
    },
    {
        "id":          "p2_audit_gap",
        "severity":    "P2",
        "description": "Project not audited in > 7 days",
        "condition":   lambda s, meta, pid: _audit_gap_days(meta, pid) > 7,
        "channels":    ["slack"],
        "label":       "[INFO]",
        "emoji":       "ℹ️",
    },
    {
        "id":          "p3_user_state_anomaly",
        "severity":    "P3",
        "description": "M6 user state anomalies exceed 5% of Orders",
        "condition":   lambda s, meta, pid: _m6_rate(s) > 0.05,
        "channels":    ["slack"],
        "label":       "[DEBUG]",
        "emoji":       "🔍",
    },
]

def _m4_rate(summary: dict) -> float:
    m4    = summary.get("by_check", {}).get("M4", {}).get("count", 0)
    total = max(summary.get("total_events", 1), 1)
    return m4 / total

def _m6_rate(summary: dict) -> float:
    m6    = summary.get("by_check", {}).get("M6", {}).get("count", 0)
    total = max(summary.get("total_events", 1), 1)
    return m6 / total

def _m2_on_event(summary: dict, event_name: str) -> int:
    return summary.get("by_event", {}).get(event_name, {}).get("M2", 0)

def _audit_gap_days(metadata: dict, project_id: str) -> int:
    entry = metadata.get(str(project_id), {})
    last  = entry.get("last_audit_date", "1970-01-01")
    try:
        return (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return 9999

def evaluate_alerts(summary: dict, metadata: dict, project_id: str) -> list:
    triggered = []
    for rule in ALERT_RULES:
        try:
            if rule["condition"](summary, metadata, project_id):
                triggered.append(rule)
        except Exception as e:
            pass
    return triggered

def dispatch_alerts(triggered_rules: list, summary: dict, config: dict) -> list:
    history = mcp_tools.get_audit_history()
    prev_summary = history[-2] if len(history) >= 2 else None
    
    # Check if we should actually post to Slack (Noise Reduction)
    if not should_alert(summary, prev_summary) and not config.get("force"):
        return ["Skipped: Threshold not met"]

    webhook     = config.get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL")
    proj_name   = config.get("project_name", "Kaliper")
    dispatched  = []

    now        = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    health     = summary.get("health_score", "?")
    total_ev   = summary.get("total_events", "?")
    total_iss  = summary.get("total_issues", "?")
    crit_iss   = summary.get("critical_issues", 0)
    trend      = _get_trend_indicator()

    # Trend calculation string
    h_change = ""
    if prev_summary:
        diff = health - prev_summary.get("health_score", 100)
        if diff < 0: h_change = f" (📉 {round(diff, 1)}%)"
        elif diff > 0: h_change = f" (📈 +{round(diff, 1)}%)"

    lines = [
        f"*{proj_name} Audit Dashboard* - {now}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"*KPIs*",
        f"• *Health Score:* `{health}%`{h_change}",
        f"• *Trend:* {trend}",
        f"• *Events:* {total_ev} | *Total Issues:* {total_iss} | *Critical:* {crit_iss}",
        "",
        "*Top Issues Identified:*",
    ]

    # Group by severity for scannability
    important_rules = sorted(triggered_rules, key=lambda x: x['severity'])[:5]
    for rule in important_rules:
        lines.append(f"• {rule['emoji']} [{rule['severity']}] {rule['description']}")

    # AI Section (if present)
    ai_report = config.get("ai_diagnosis")
    if ai_report:
        lines.append("\n*🤖 AI Context & Root Cause:*")
        # Extract last section or first 3 lines
        diagnosis = ai_report.split("\n\n")[-1] if "\n\n" in ai_report else ai_report
        lines.append(f"> {diagnosis[:300].strip()}...")

    slack_body = {"text": "\n".join(lines)}

    if webhook:
        try:
            resp = requests.post(webhook, json=slack_body, timeout=8)
            if resp.status_code == 200: dispatched.append("Slack: OK")
            else: dispatched.append(f"Slack: FAILED {resp.status_code}")
        except: dispatched.append("Slack: EXCEPTION")
    else:
        sanitized = "\n".join(lines).encode('ascii', 'ignore').decode('ascii')
        print("\n" + "=" * 60 + "\n" + sanitized + "\n" + "=" * 60 + "\n")
        dispatched.append("Console: printed")

    return dispatched
