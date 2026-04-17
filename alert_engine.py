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
    if not triggered_rules:
        return []

    webhook     = config.get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL")
    proj_name   = config.get("project_name", "Kaliper")
    dispatched  = []

    now        = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    health     = summary.get("health_score", "?")
    total_ev   = summary.get("total_events", "?")
    total_iss  = summary.get("total_issues", "?")

    lines = [
        f"*{proj_name} Audit Alert* - {now}",
        f"Events: {total_ev} | Issues: {total_iss} | Health: {health}%",
        "",
        "*Triggered alerts:*",
    ]
    for rule in triggered_rules:
        lines.append(f"{rule['label']} [{rule['severity']}] {rule['description']}")

    slack_body = {"text": "\n".join(lines)}

    if webhook:
        try:
            resp = requests.post(webhook, json=slack_body, timeout=8)
            if resp.status_code == 200:
                dispatched.append("Slack: OK")
            else:
                dispatched.append(f"Slack: FAILED {resp.status_code}")
        except:
            dispatched.append("Slack: EXCEPTION")
    else:
        # Final safety: sanitize lines before printing to Windows console
        sanitized = "\n".join(lines).encode('ascii', 'ignore').decode('ascii')
        print("\n" + "=" * 60)
        print(sanitized)
        print("=" * 60 + "\n")
        dispatched.append("Console: printed")

    return dispatched
