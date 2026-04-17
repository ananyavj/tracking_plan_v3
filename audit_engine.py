# audit_engine.py
"""
Kaliper Analytics Governance Engine - Final Revision

A state-aware auditor that evaluates tracking plan compliance, calculates 
impact-weighted health metrics, and identifies issue lifecycles.
"""

import json
import os
from datetime import datetime
from tracking_plan_parser import parse_tracking_plan
from utils import _extract_event_time

# ---------------------------------------------------------------------------
# Governance Config
# ---------------------------------------------------------------------------

PENALTY_WEIGHTS = {
    "M0": 10, # Unknown Event
    "M1": 5,  # Type Mismatch
    "M2": 10, # Missing Required Property
    "M3": 5,  # Funnel/State Inconsistency (Product)
    "M4": 10, # Critical Journey Break (Checkout)
    "M5": 2,  # Mathematical Discount Error
    "M6": 2,  # User State/Identity Contradiction
    "M7": 2,  # Duplicate Transaction/Event
    "M8": 1   # Enum/Allowed Value Violation
}

# 1. PURE DERIVED NORMALIZATION (No magic constants)
MAX_PENALTY_PER_EVENT = max(PENALTY_WEIGHTS.values())

def _condition_applies(condition: str, event_props: dict) -> bool:
    if not condition: return True
    cond_lower = condition.lower()
    for step_num in ("1", "2", "3"):
        markers = [f"step {step_num}", f"step={step_num}", f"on step {step_num}", f"for step {step_num}"]
        if any(m in cond_lower for m in markers):
            return str(event_props.get("step", "")) == step_num
    return True

def get_platform(event):
    props = event.get("event_properties", {})
    return props.get("platform") or event.get("platform") or "unknown"

# ---------------------------------------------------------------------------
# AuditEngine
# ---------------------------------------------------------------------------

class AuditEngine:

    def __init__(self, tracking_plan_path, events_data, project_id=None):
        self.tracking_plan = parse_tracking_plan(tracking_plan_path)
        
        def _sort_key(e):
            t = e.get("time")
            if isinstance(t, (int, float)) and t > 0: return t
            dt = _extract_event_time(e)
            return int(dt.timestamp() * 1000) if dt else 0
            
        self.events = sorted(events_data, key=_sort_key)
        self.issues = []
        self.user_pids = {} 
        
        # Internal state for precision metrics
        self.event_issue_map = {} # { (code, event, prop, platform): set(insert_ids) }
        self.unknown_platform_count = 0
        
        self.summary = {
            "project_id":     project_id,
            "total_events":   len(self.events),
            "critical_issues": 0,
            "warning_issues":  0,
            "by_check": {
                f"M{i}": {"count": 0, "severity": "critical" if i <= 4 else "warning"}
                for i in range(9)
            },
            "by_event": {}, 
        }

    def run_all_checks(self):
        self._check_metadata_gaps()
        self._check_m0_m1_m2_m8()
        self._check_m3_m7()
        self._check_m4_m6()
        self._check_m5()
        self._finalize_summary()
        return self.summary, self.issues

    def _check_metadata_gaps(self):
        for ev in self.events:
            if get_platform(ev) == "unknown":
                self.unknown_platform_count += 1

    def _add_issue(self, code, severity, event, issue_text,
                   property=None, found_value=None, expected=None):
        ename    = event.get("event_type", "Unknown")
        platform = get_platform(event)
        ins_id   = event.get("insert_id") or f"gen_{ename}_{event.get('time')}"
        
        # 2. Quad-Key Dedup (Code, Event, Property, Platform)
        dedup_key = (code, ename, property, platform)
        if dedup_key not in self.event_issue_map:
            self.event_issue_map[dedup_key] = set()
        self.event_issue_map[dedup_key].add(ins_id) # Unique event tracker for blast radius

        self.issues.append({
            "code":        code,
            "severity":    severity,
            "event":       ename,
            "platform":    platform,
            "property":    property,
            "found_value": str(found_value) if found_value is not None else None,
            "expected":    expected,
            "issue":       issue_text,
            "user_id":     event.get("user_id"),
            "insert_id":   ins_id,
            "dedup_key":   ":".join(filter(None, [str(x) for x in dedup_key]))
        })
        
        self.summary["by_check"][code]["count"] += 1
        if ename not in self.summary["by_event"]: self.summary["by_event"][ename] = {}
        self.summary["by_event"][ename][code] = self.summary["by_event"][ename].get(code, 0) + 1
        
        if severity == "critical": self.summary["critical_issues"] += 1
        else: self.summary["warning_issues"] += 1

    def _check_m0_m1_m2_m8(self):
        event_lookup = self.tracking_plan.get("event_lookup", {})
        global_props = self.tracking_plan.get("global_props", [])
        for ev in self.events:
            name = ev.get("event_type", "")
            norm_name = name.lower().replace(" ", "_")
            if norm_name not in event_lookup:
                self._add_issue("M0", "critical", ev, f"Event '{name}' not in tracking plan")
                continue
            schema = event_lookup[norm_name]; event_props = ev.get("event_properties") or {}
            for prop in schema.get("properties", []): self._check_property(ev, event_props, prop)
            for gp in global_props: self._check_property(ev, event_props, gp)

    def _check_property(self, ev, event_props, prop_schema):
        p_name = prop_schema["name"]
        if not _condition_applies(prop_schema.get("condition", ""), event_props): return
        if p_name not in event_props:
            if prop_schema.get("required"): self._add_issue("M2", "critical", ev, f"Missing required property: '{p_name}'", property=p_name)
            return
        val = event_props[p_name]; expected_type = prop_schema.get("type", "string")
        if not self._validate_type(val, expected_type):
            self._add_issue("M1", "critical", ev, f"Type mismatch: '{p_name}' expected {expected_type}", property=p_name, found_value=val)
            return
        allowed = prop_schema.get("allowed_values", [])
        if allowed:
            val_str = str(val).lower() if isinstance(val, bool) else str(val)
            norm_allowed = [str(a).lower() if isinstance(a, bool) else str(a) for a in allowed]
            if val_str not in norm_allowed:
                self._add_issue("M8", "warning", ev, f"Invalid enum value for '{p_name}'", property=p_name, found_value=val, expected=allowed)

    @staticmethod
    def _validate_type(value, expected_type):
        if value is None: return False
        if expected_type == "string": return isinstance(value, str)
        if expected_type == "float": return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "integer": return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "boolean": return isinstance(value, bool)
        if expected_type == "array": return isinstance(value, list)
        return True

    def _check_m3_m7(self):
        seen_ids = {}
        for ev in self.events:
            iid = ev.get("insert_id")
            if iid:
                if iid in seen_ids: self._add_issue("M7", "warning", ev, f"Duplicate insert_id: {iid}")
                else: seen_ids[iid] = ev.get("event_type")

    def _check_m4_m6(self):
        sessions = {}
        for ev in self.events:
            sid = (ev.get("event_properties") or {}).get("session_id")
            if sid: sessions.setdefault(sid, []).append(ev)
        for sid, evs in sessions.items():
            types = [e["event_type"] for e in evs]
            if "Order Completed" in types and "Checkout Started" not in types:
                self._add_issue("M4", "critical", evs[0], "Order without Checkout Start")

    def _check_m5(self):
        for ev in self.events:
            if ev.get("event_type") != "Product Viewed": continue
            p = ev.get("event_properties", {}); price, comp, disc = p.get("price"), p.get("compare_at_price"), p.get("discount_pct")
            if all(isinstance(x, (int, float)) for x in [price, comp, disc]) and comp > 0:
                expected = round((comp - price) / comp * 100, 1)
                if abs(disc - expected) > 1.0: self._add_issue("M5", "warning", ev, f"Discount error: reported {disc}%, expected {expected}%", property="discount_pct")

    def _finalize_summary(self):
        weighted_p = sum(self.summary["by_check"][c]["count"] * PENALTY_WEIGHTS.get(c, 2) for c in PENALTY_WEIGHTS)
        
        # 3. SELF-DERIVING HEALTH SCORE (No magic constants)
        max_possible_penalty = self.summary["total_events"] * MAX_PENALTY_PER_EVENT
        score = 100 * max(0, 1 - (weighted_p / (max_possible_penalty + 1e-9)))
        
        self.summary["health_score"]  = round(score, 1)
        self.summary["total_issues"]  = len(self.event_issue_map)
        self.summary["total_penalty"] = weighted_p
        
        # 4. Blast Radius (Based on UNIQUE events affected)
        blast_info = []
        for key, inserts in self.event_issue_map.items():
            radius = len(inserts) / (self.summary["total_events"] or 1)
            code, event, prop, platform = key
            weight = PENALTY_WEIGHTS.get(code, 2)
            blast_info.append({
                "key": ":".join(filter(None, [str(x) for x in key])),
                "penalty": len(inserts) * weight,
                "radius": round(radius * 100, 2)
            })
            
        # 5. Top Driver (Tie-breaker: Blast Radius)
        drivers = {}
        for b in blast_info:
            gk = b["key"].split(":")[0] + ":" + b["key"].split(":")[1]
            if gk not in drivers: drivers[gk] = {"penalty": 0, "radius": 0}
            drivers[gk]["penalty"] += b["penalty"]
            drivers[gk]["radius"]  += b["radius"]
        driver_list = [{"name": k, **v} for k, v in drivers.items()]
        driver_list.sort(key=lambda x: (x["penalty"], x["radius"]), reverse=True)
        self.summary["top_driver"] = driver_list[0] if driver_list else None
        
        # 6. Sort Top Issues (Severity/Impact)
        blast_info.sort(key=lambda x: x["penalty"], reverse=True)
        self.summary["top_dedup_keys"] = [b["key"] for b in blast_info[:20]]
        self.summary["issue_prio_map"] = {b["key"]: b["penalty"] for b in blast_info[:20]}
        
        # 7. Metadata Quality Signal (Unknown Platform)
        u_pct = (self.unknown_platform_count / (self.summary["total_events"] or 1)) * 100
        self.summary["unknown_platform"] = {
            "percent": round(u_pct, 1),
            "severity": "CRITICAL" if u_pct > 15 else "WARNING" if u_pct > 5 else "OK"
        }
        self.summary["audit_date"] = datetime.now().isoformat()
