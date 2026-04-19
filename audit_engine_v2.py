# audit_engine_v2.py
"""
Kaliper V2 Audit Engine - Deterministic Validation Layer

Pure function that validates a list of standardized events against 
the tracking plan rules. 

PRINCIPLE: Strictly stateless. No history access.
Computes all metrics (Score, Blast Radius, Penalties) independently.
"""

import json

# Execution Lock: Mandatory Weights
PENALTY_WEIGHTS = {
    "M0": 10, # Unknown Event
    "M1": 5,  # Type Mismatch
    "M2": 10, # Missing Required Property
    "M3": 5,  # Funnel/State Inconsistency
    "M4": 10, # Critical Journey Break
    "M5": 2,  # Mathematical/Calculated Error
    "M6": 2,  # Identity Contradiction
    "M7": 2,  # Duplicate Event
    "M8": 1   # Enum/Constraint Violation
}

MAX_PENALTY_PER_EVENT = max(PENALTY_WEIGHTS.values())

class AuditEngineV2:
    def __init__(self, schema):
        self.schema = schema
        self.issue_clusters = {} # (dedup_key): { ...metrics }
        self.total_weighted_penalty = 0
        self.impacted_event_ids = set()

    def run(self, events):
        """
        Input: List[dict] (standardized events from fetcher_v2)
        Output: dict (intermediate audit result)
        """
        total_events = len(events)
        if total_events == 0:
            return self._empty_result()

        for ev in events:
            self._audit_single_event(ev)

        # Finalize Metrics
        max_possible_penalty = total_events * MAX_PENALTY_PER_EVENT
        health_score = 100 * max(0, 1 - (self.total_weighted_penalty / (max_possible_penalty + 1e-9)))

        unknown_p_count = sum(1 for e in events if e.get("platform") == "unknown")

        clusters = []
        for key, data in self.issue_clusters.items():
            k_parts = key.split(":")
            clusters.append({
                "dedup_key":        key,
                "code":             k_parts[0],
                "event":            k_parts[1],
                "property":         k_parts[2] if len(k_parts) > 2 else None,
                "platform":         k_parts[3] if len(k_parts) > 3 else None,
                "count":            data["count"],
                "unique_events":    len(data["event_ids"]),
                "blast_radius":     round((len(data["event_ids"]) / total_events) * 100, 2),
                "weighted_penalty": data["penalty"],
                "example_issue":    data["example_issue"]
            })

        return {
            "summary": {
                "health_score":         round(health_score, 1),
                "total_events":         total_events,
                "weighted_penalty":    self.total_weighted_penalty,
                "unknown_platform_pct": round((unknown_p_count/total_events)*100, 1) if total_events > 0 else 0
            },
            "issue_clusters": clusters
        }

    def _audit_single_event(self, ev):
        ename = ev.get("event_name")
        platform = ev.get("platform", "unknown").lower()
        
        event_rules = self.schema["events"].get(ename)
        if not event_rules:
            self._add_issue("M0", ename, None, platform, ev, f"Event '{ename}' not in tracking plan")
            return

        # Core Rule Logic: Platform-specific overrides global
        # 1. Gather all rules that apply to this platform
        active_rules = []
        global_rules = event_rules["rules"]["global"]
        platform_rules = event_rules["rules"].get(platform, [])
        
        # Override Strategy: If a property exists in platform rules, skip it in global rules.
        specific_props = {r["name"] for r in platform_rules}
        for r in global_rules:
            if r["name"] not in specific_props:
                active_rules.append(r)
        active_rules.extend(platform_rules)

        # 2. Validate Properties
        for rule in active_rules:
            self._validate_rule(ev, rule, platform)

    def _validate_rule(self, ev, rule, platform):
        path = rule["name"]
        val = self._get_nested_value(ev["properties"], path)
        
        # Check Requirement
        if val is None:
            if rule.get("required"):
                self._add_issue("M2", ev["event_name"], path, platform, ev, f"Missing required property: {path}")
            return

        # Check Type
        expected_type = rule.get("type")
        if not self._check_type(val, expected_type):
            self._add_issue("M1", ev["event_name"], path, platform, ev, f"Type mismatch: {path} expected {expected_type}")
            return

        # Check Allowed Values (Enums)
        allowed = rule.get("allowed", [])
        if allowed:
            val_str = str(val).lower() if isinstance(val, bool) else str(val)
            norm_allowed = [str(a).lower() for a in allowed]
            if val_str not in norm_allowed:
                self._add_issue("M8", ev["event_name"], path, platform, ev, f"Invalid value for {path}", expected=allowed)

    def _add_issue(self, code, event, prop, platform, ev, msg, expected=None):
        key = f"{code}:{event}:{prop or ''}:{platform}"
        weight = PENALTY_WEIGHTS.get(code, 2)
        ins_id = ev.get("insert_id") or f"gen_{event}_{ev['timestamp']}"

        if key not in self.issue_clusters:
            self.issue_clusters[key] = {
                "count": 0,
                "event_ids": set(),
                "penalty": 0,
                "example_issue": msg
            }
        
        self.issue_clusters[key]["count"] += 1
        self.issue_clusters[key]["event_ids"].add(ins_id)
        self.issue_clusters[key]["penalty"] += weight
        self.total_weighted_penalty += weight

    def _get_nested_value(self, obj, path):
        """Execution Lock: Dot-notation path traversal."""
        keys = path.split(".")
        for k in keys:
            if not isinstance(obj, dict): return None
            obj = obj.get(k)
            if obj is None: return None
        return obj

    def _check_type(self, val, t):
        if t == "string": return isinstance(val, str)
        if t == "float":  return isinstance(val, (int, float)) and not isinstance(val, bool)
        if t == "integer": return isinstance(val, int) and not isinstance(val, bool)
        if t == "boolean": return isinstance(val, bool)
        if t == "array": return isinstance(val, list)
        if t == "object": return isinstance(val, dict)
        return True

    def _empty_result(self):
        return {
            "summary": {"health_score": 100, "total_events": 0, "weighted_penalty": 0, "unknown_platform_pct": 0},
            "issue_clusters": []
        }
