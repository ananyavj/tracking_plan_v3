# audit_engine.py
"""
Phase 3 Audit Engine   Noise-Reduced, Condition-Aware

Key changes from previous version:
  1. Condition evaluation: Before flagging M1/M2, checks the property's
     `condition` field (from Notes column). Skips checks that don't apply,
     e.g. payment_method "Populate on step 3 only"   no false positives
     on steps 1 and 2.
  2. M8 boolean normalisation: Compares lowercased values against lowercased
     allowed_values so Python's True/False don't trigger false enum violations.
  3. Health score is volume-normalised: issues/events, not issues/100.
"""

import json
import os
from datetime import datetime
from tracking_plan_parser import parse_tracking_plan
from utils import _extract_event_time


# ---------------------------------------------------------------------------
# Condition Evaluator
# ---------------------------------------------------------------------------

def _condition_applies(condition: str, event_props: dict) -> bool:
    """
    Returns True if the check SHOULD run based on the 'Notes' column.
    """
    if not condition:
        return True

    cond_lower = condition.lower()

    # Step-based conditions (very common in checkout funnels)
    # Catching: "step 3 only", "populated on step 3", "required for step 3", "step=3"
    for step_num in ("1", "2", "3"):
        markers = [
            f"step {step_num}",
            f"step={step_num}",
            f"on step {step_num}",
            f"for step {step_num}",
            f"populated on step {step_num}"
        ]
        if any(m in cond_lower for m in markers):
            return str(event_props.get("step", "")) == step_num

    # Placeholder for future logic like "only for authenticated"
    # if "authenticated" in cond_lower: ...
    
    return True


# ---------------------------------------------------------------------------
# AuditEngine
# ---------------------------------------------------------------------------

class AuditEngine:

    def __init__(self, tracking_plan_path, events_data, project_id=None):
        self.tracking_plan = parse_tracking_plan(tracking_plan_path)
        def _sort_key(e):
            t = e.get("time")
            if t and isinstance(t, (int, float)) and t > 0:
                return t
            dt = _extract_event_time(e)
            return int(dt.timestamp() * 1000) if dt else 0
        self.events        = sorted(events_data, key=_sort_key)
        self.issues        = []
        
        # Cross-session state for M3 (tracking product ID shifts per user history)
        # "for now" approach: stores canonical ID mapping for a given naming context per user
        self.user_pids = {} # {user_id: {product_name: last_seen_pid}}
        
        self.summary       = {
            "project_id":     project_id,
            "total_events":   len(self.events),
            "critical_issues": 0,
            "warning_issues":  0,
            "by_check": {
                f"M{i}": {
                    "count":    0,
                    "severity": "critical" if i <= 4 else "warning",
                }
                for i in range(9)   # M0   M8
            },
            "by_event": {}, # {event_name: {code: count}}
        }

    # -----------------------------------------------------------------------
    # Public
    # -----------------------------------------------------------------------

    def run_all_checks(self):
        self._check_m0_m1_m2_m8()
        self._check_m3_m7()
        self._check_m4_m6()
        self._check_m5()
        self._finalize_summary()
        return self.summary, self.issues

    # -----------------------------------------------------------------------
    # Issue recorder
    # -----------------------------------------------------------------------

    def _add_issue(self, code, severity, event, issue_text,
                   property=None, found_value=None, expected=None,
                   user_id=None, session_id=None, insert_id=None, timestamp=None):
        self.issues.append({
            "code":        code,
            "severity":    severity,
            "event":       event.get("event_type", "Unknown"),
            "property":    property,
            "found_value": str(found_value) if found_value is not None else None,
            "expected":    expected,
            "issue":       issue_text,
            "user_id":     user_id    or event.get("user_id"),
            "session_id":  session_id or (event.get("event_properties") or {}).get("session_id"),
            "insert_id":   insert_id  or event.get("insert_id"),
            "timestamp":   timestamp  or event.get("time"),
        })
        self.summary["by_check"][code]["count"] += 1
        
        # Track by event name
        ename = event.get("event_type", "Unknown")
        if ename not in self.summary["by_event"]:
            self.summary["by_event"][ename] = {}
        if code not in self.summary["by_event"][ename]:
            self.summary["by_event"][ename][code] = 0
        self.summary["by_event"][ename][code] += 1
        
        if severity == "critical":
            self.summary["critical_issues"] += 1
        else:
            self.summary["warning_issues"] += 1

    # -----------------------------------------------------------------------
    # M0, M1, M2, M8
    # -----------------------------------------------------------------------

    def _check_m0_m1_m2_m8(self):
        event_lookup = self.tracking_plan.get("event_lookup", {})
        global_props = self.tracking_plan.get("global_props", [])

        for ev in self.events:
            name      = ev.get("event_type", "")
            norm_name = name.lower().replace(" ", "_")

            # M0   unknown event name
            if norm_name not in event_lookup:
                self._add_issue("M0", "critical", ev,
                                 f"Event '{name}' not found in tracking plan")
                continue

            schema      = event_lookup[norm_name]
            event_props = ev.get("event_properties") or {}

            # Per-property checks
            for prop in schema.get("properties", []):
                self._check_property(ev, event_props, prop)

            # Global properties
            for gp in global_props:
                self._check_property(ev, event_props, gp)

    def _check_property(self, ev, event_props, prop_schema):
        """Run M1, M2, M8 for a single property."""
        p_name    = prop_schema["name"]
        condition = prop_schema.get("condition", "")

        # Evaluate condition   skip entirely if condition not met
        if not _condition_applies(condition, event_props):
            return

        # 1. M2 Missing Check
        if p_name not in event_props:
            if prop_schema.get("required"):
                self._add_issue("M2", "critical", ev,
                                 f"Missing required property: '{p_name}'",
                                 property=p_name)
            return

        val = event_props[p_name]

        # 2. M1 Type Check
        expected_type = prop_schema.get("type", "string")
        if not self._validate_type(val, expected_type):
            self._add_issue("M1", "critical", ev,
                             f"Type mismatch: '{p_name}' expected {expected_type}, found {type(val).__name__}",
                             property=p_name, found_value=val, expected=expected_type)
            return

        # 3. Nested Array Validation (Product Items)
        # "for now" approach: if we see an array named 'products', we validate the inner items
        # against a hardcoded Shopify-standard internal schema.
        if expected_type == "array" and p_name == "products" and isinstance(val, list):
            self._validate_product_items(ev, val)

        # 4. M8 Enum Check
        allowed = prop_schema.get("allowed_values", [])
        if allowed:
            if isinstance(val, bool):
                val_str = str(val).lower()          # True   "true", False   "false"
            else:
                val_str = str(val)
            allowed_normalised = [str(a).lower() if isinstance(a, bool) else str(a)
                                  for a in allowed]
            if val_str not in allowed_normalised:
                self._add_issue("M8", "warning", ev,
                                 f"Invalid enum value for '{p_name}': "
                                 f"found '{val_str}', expected one of {allowed}",
                                 property=p_name, found_value=val, expected=allowed)

    # -----------------------------------------------------------------------
    # Type validator
    # -----------------------------------------------------------------------

    @staticmethod
    def _validate_type(value, expected_type):
        if value is None:
            return False
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "float":
            # integers are valid floats; strings are not
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "array":
            return isinstance(value, list)
        return True  # ISO8601 and unknown types pass through

    def _validate_product_items(self, ev, products):
        """
        Hardcoded internal schema for products array validation.
        Ensures consistency inside nested lists where the tracking plan parser doesn't reach.
        """
        REQUIRED_FIELDS = ["product_id", "price", "quantity", "variant_id"]
        for idx, item in enumerate(products):
            if not isinstance(item, dict):
                continue
            for rf in REQUIRED_FIELDS:
                if rf not in item:
                    self._add_issue("M2", "critical", ev,
                                     f"Missing required property: 'products[{idx}].{rf}'",
                                     property=f"products[{idx}].{rf}")
                elif rf == "price" and not isinstance(item[rf], (int, float)):
                    self._add_issue("M1", "critical", ev,
                                     f"Type mismatch: 'products[{idx}].price' expected float, found {type(item[rf]).__name__}",
                                     property=f"products[{idx}].price", found_value=item[rf])

    # -----------------------------------------------------------------------
    # M3, M7
    # -----------------------------------------------------------------------

    def _check_m3_m7(self):
        # M7   duplicate insert_id
        seen_insert_ids = {}
        for ev in self.events:
            iid = ev.get("insert_id")
            if iid:
                if iid in seen_insert_ids:
                    self._add_issue("M7", "warning", ev,
                                     f"Duplicate insert_id: '{iid}' also seen on "
                                     f"'{seen_insert_ids[iid]}'")
                else:
                    seen_insert_ids[iid] = ev.get("event_type", "?")

        # Inconsistent Product ID (PDP vs Cart & Cross-session)
        sessions = self._build_session_map()
        for sid, sess_evs in sessions.items():
            uid = sess_evs[0].get("user_id")
            viewed_ids = set()
            added_pids = {} # name -> pid for cross-session tracking
            
            for e in sess_evs:
                ep = e.get("event_properties") or {}
                pid = ep.get("product_id")
                name = ep.get("name") # Logic relies on 'name' being consistent
                
                if pid:
                    # Collect session scope
                    if e["event_type"] == "Product Viewed":
                        viewed_ids.add(pid)
                    if e["event_type"] == "Product Added":
                        # M3: Intra-session - Added product must have been viewed (PDP funnel check)
                        # We only flag if source=pdp (direct PDP add) or if no source (default)
                        source = ep.get("source", "pdp")
                        if source == "pdp" and pid not in viewed_ids:
                            self._add_issue(
                                "M3", "critical", e,
                                f"Product Added ('{pid}') without prior Product View in session",
                                property="product_id", session_id=sid
                            )
                        
                        # Cross-session tracking (User-level persistence)
                        if uid and name:
                            if uid not in self.user_pids: self.user_pids[uid] = {}
                            if name in self.user_pids[uid]:
                                last_pid = self.user_pids[uid][name]
                                if last_pid != pid:
                                    self._add_issue(
                                        "M3", "critical", e,
                                        f"Inconsistent Product ID for '{name}': "
                                        f"Previously '{last_pid}', now '{pid}'",
                                        property="product_id", user_id=uid
                                    )
                            self.user_pids[uid][name] = pid

    # -----------------------------------------------------------------------
    # M4, M6
    # -----------------------------------------------------------------------

    def _check_m4_m6(self):
        sessions    = self._build_session_map()
        user_orders = {}   # user_id   order count seen so far (chronological)

        # M4   funnel break (per session)
        for sid, sess_evs in sessions.items():
            types = [e["event_type"] for e in sess_evs]

            if "Order Completed" not in types:
                continue

            if "Checkout Started" not in types:
                self._add_issue("M4", "critical", sess_evs[0],
                                 "Order Completed fired without Checkout Started in session",
                                 session_id=sid)
                continue

            # Checkout Started exists   verify at least one Step Completed before Order
            start_seen = False
            step_seen  = False
            for e in sess_evs:
                t = e["event_type"]
                if t == "Checkout Started":
                    start_seen = True
                if start_seen and t == "Checkout Step Completed":
                    step_seen = True
                if t == "Order Completed":
                    break

            if not step_seen:
                self._add_issue("M4", "critical", sess_evs[0],
                                 "Order Completed bypassed Checkout Steps "
                                 "(Checkout Started   Order Completed with no intermediate steps)",
                                 session_id=sid)

        # M6   user state inconsistency (chronological, cross-session)
        for ev in self.events:
            uid = ev.get("user_id")
            if not uid:
                continue

            ep       = ev.get("event_properties") or {}
            up       = ev.get("user_properties")   or {}
            is_first = ep.get("is_first_order")
            is_ret   = ep.get("is_returning") if "is_returning" in ep else up.get("is_returning")

            # Contradiction: returning user + first order in same event
            if is_ret is True and is_first is True:
                self._add_issue("M6", "warning", ev,
                                 "Contradictory: is_returning=true AND is_first_order=true")

            if ev["event_type"] == "Order Completed":
                prior = user_orders.get(uid, 0)
                if is_first is True and prior > 0:
                    self._add_issue("M6", "warning", ev,
                                     f"is_first_order=true but user already has "
                                     f"{prior} prior Order Completed in dataset",
                                     user_id=uid)
                user_orders[uid] = prior + 1

    # -----------------------------------------------------------------------
    # M5
    # -----------------------------------------------------------------------

    def _check_m5(self):
        for ev in self.events:
            if ev.get("event_type") != "Product Viewed":
                continue
            props    = ev.get("event_properties") or {}
            price    = props.get("price")
            compare  = props.get("compare_at_price")
            reported = props.get("discount_pct")

            if not (isinstance(price, (int, float))
                    and isinstance(compare, (int, float))
                    and compare > 0
                    and isinstance(reported, (int, float))):
                continue

            expected = round((compare - price) / compare * 100, 1)
            if abs(reported - expected) > 1.0:
                self._add_issue("M5", "warning", ev,
                                 f"discount_pct error: reported {reported}%, "
                                 f"expected {expected}%",
                                 property="discount_pct",
                                 found_value=reported, expected=expected)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_session_map(self) -> dict:
        sessions = {}
        for ev in self.events:
            sid = (ev.get("event_properties") or {}).get("session_id")
            if sid:
                sessions.setdefault(sid, []).append(ev)
        return sessions

    def _finalize_summary(self):
        # Weighted Penalty System:
        # P0/Critical: -10 pts | P1: -5 pts | P2: -1-2 pts
        granularity = {
            "M0": 10, "M1": 5, "M2": 10, "M3": 5, "M4": 10,
            "M5": 2,  "M6": 2, "M7": 2,  "M8": 1
        }
        
        total_penalty = 0
        for code, details in self.summary["by_check"].items():
            weight = granularity.get(code, 2)
            total_penalty += details["count"] * weight
            
        m0_count = self.summary["by_check"]["M0"]["count"]
        denominator = max(self.summary["total_events"] - m0_count, 1)
        
        # Scale: 1 P0 in 100 events results in -1pt to health score (10/100 * 10)
        score = max(0, 100 - (total_penalty / denominator * 10))
        
        self.summary["health_score"]   = round(score, 1)
        self.summary["total_issues"]   = self.summary["critical_issues"] + self.summary["warning_issues"]
        self.summary["total_penalty"]  = total_penalty
        self.summary["audit_date"]     = datetime.now().isoformat()
