# fetcher_v2.py
"""
Kaliper V2 Fetcher - Deterministic Ingestion Layer

Centralized module for fetching raw event data from diverse sources.
Ensures the core pipeline receives a timestamp-sorted list of events
with standardized envelopes.

PRINCIPLE: Preserve ALL raw data. Do not flatten, drop, or transform
the content of properties.
"""

import os
import json
import requests
import zlib
import io
import zipfile
from datetime import datetime, timedelta
from requests.auth import HTTPBasicAuth
from utils import _extract_event_time

class FetcherV2:
    @staticmethod
    def fetch(mode="amplitude", days_back=1, **config):
        """
        Main entry point for data ingestion.
        Returns: List[dict] (timestamp-sorted events)
        """
        if mode == "simulation":
            return FetcherV2._fetch_simulation(config.get("path", "simulated_events.json"))
        
        return FetcherV2._fetch_amplitude(days_back, config)

    @staticmethod
    def _fetch_simulation(path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Simulation file not found: {path}")
        with open(path, "r") as f:
            events = json.load(f)
        return FetcherV2._standardize_and_sort(events)

    @staticmethod
    def _fetch_amplitude(days_back, config):
        api_key = config.get("api_key") or os.getenv("AMPLITUDE_API_KEY")
        secret_key = config.get("secret_key") or os.getenv("AMPLITUDE_SECRET_KEY")
        
        if not (api_key and secret_key):
            raise ValueError("Missing Amplitude API/Secret keys in config or environment.")

        # Default window: last N days
        end_t = datetime.utcnow()
        start_t = end_t - timedelta(days=days_back)
        start_str = start_t.strftime("%Y%m%dT%H")
        end_str = end_t.strftime("%Y%m%dT%H")

        print(f"[fetcher_v2] Fetching from Amplitude: {start_str} to {end_str}")
        
        all_events = []
        curr = start_t
        while curr < end_t:
            chunk_e = min(curr + timedelta(hours=24), end_t)
            url = f"https://amplitude.com/api/2/export?start={curr.strftime('%Y%m%dT%H')}&end={chunk_e.strftime('%Y%m%dT%H')}"
            
            resp = requests.get(
                url, 
                auth=HTTPBasicAuth(api_key, secret_key), 
                stream=True, 
                timeout=(10, 120)
            )
            
            if resp.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    for fn in z.namelist():
                        if fn.endswith(".json.gz"):
                            with z.open(fn) as f:
                                decompression = zlib.decompress(f.read(), 16+zlib.MAX_WBITS)
                                for line in decompression.decode('utf-8').splitlines():
                                    if line.strip():
                                        all_events.append(json.loads(line))
            elif resp.status_code == 404:
                print(f"[fetcher_v2] No data found for chunk {curr.strftime('%Y%m%dT%H')}")
            else:
                print(f"[fetcher_v2] API Error {resp.status_code}: {resp.text}")
            
            curr = chunk_e

        return FetcherV2._standardize_and_sort(all_events)

    @staticmethod
    def _standardize_and_sort(events):
        """
        Enforce standardized envelope fields without touching property content.
        Required fields per Execution Lock: event_name, timestamp, user_id, platform, properties.
        """
        standardized = []
        for e in events:
            # Determine timestamp
            t = e.get("time")
            if not (t and isinstance(t, (int, float)) and t > 0):
                dt = _extract_event_time(e)
                t = int(dt.timestamp() * 1000) if dt else 0
            
            # Standardization mapping
            std_event = {
                "event_name": e.get("event_type") or e.get("event_name") or "unknown",
                "timestamp":  t,
                "user_id":    e.get("user_id"),
                "session_id": (e.get("event_properties") or {}).get("session_id") or e.get("session_id"),
                "platform":   e.get("platform") or (e.get("event_properties") or {}).get("platform") or "unknown",
                "properties": e.get("event_properties") or e.get("properties") or {},
                "insert_id":  e.get("insert_id"),
                "raw":        e # Keep link to raw event for debugging
            }
            standardized.append(std_event)

        # Sort by timestamp ASC
        return sorted(standardized, key=lambda x: x["timestamp"])
