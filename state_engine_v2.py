# state_engine_v2.py
"""
Kaliper V2 State Engine - Lifecycle & History Layer

Sole authority for identifying issue lifecycles (New, Persistent, Regression) 
and maintaining health trends over time.

PRINCIPLE: No metric calculation. No validation. Only state comparison.
"""

import json
import os
from datetime import datetime

HISTORY_FILE = "audit_history.json"

class StateEngineV2:
    def __init__(self, history_path=HISTORY_FILE):
        self.history_path = history_path
        self.history = self._load_history()

    def _load_history(self):
        if os.path.exists(self.history_path):
            with open(self.history_path, "r") as f:
                try: return json.load(f)
                except: return []
        return []

    def apply_lifecycle(self, current_clusters):
        """
        Input: List of clusters from audit_engine_v2
        Output: Same list with 'lifecycle' field added.
        """
        if not self.history:
            for c in current_clusters:
                c["lifecycle"] = "New"
            return current_clusters

        # 1. Prepare history indices
        last_run_keys = set(self.history[-1].get("issue_keys", [])) if self.history else set()
        
        # Last 5 runs keys (excluding the very last one for regression logic)
        historical_runs = self.history[-5:-1] if len(self.history) > 1 else []
        all_historical_keys = set()
        for run in historical_runs:
            for k in run.get("issue_keys", []):
                all_historical_keys.add(k)

        # 2. Strict Exclusive logic
        for cluster in current_clusters:
            key = cluster["dedup_key"]
            
            if key in last_run_keys:
                cluster["lifecycle"] = "Persistent"
            elif key in all_historical_keys:
                cluster["lifecycle"] = "Regression"
            else:
                cluster["lifecycle"] = "New"

        return current_clusters

    def get_health_trend(self):
        """Returns the last 5 health scores for the summary trend."""
        return [run.get("health_score", 100) for run in self.history[-5:]]

    def update_history(self, summary, clusters):
        """Appends a snapshot of the current audit to the history file."""
        entry = {
            "timestamp":    datetime.now().isoformat(),
            "health_score": summary.get("health_score"),
            "total_issues": len(clusters),
            "issue_keys":   [c["dedup_key"] for c in clusters]
        }
        self.history.append(entry)
        
        # Keep only last 50 entries
        if len(self.history) > 50:
            self.history = self.history[-50:]
            
        # Atomic Write
        temp_path = self.history_path + ".tmp"
        with open(temp_path, "w") as f:
            json.dump(self.history, f, indent=2)
        os.replace(temp_path, self.history_path)
