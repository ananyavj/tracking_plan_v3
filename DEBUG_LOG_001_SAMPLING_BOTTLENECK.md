# Debug Log 001: The "500-Event Sampling" Bottleneck

## 1. The Symptom
During initial testing in Claude Desktop, the user requested a "Comprehensive Health Check" on live Amplitude data. The resulting report felt "simplified" compared to the local simulation—it caught basic missing events but missed the complex session-level bugs (like M4 funnel breaks) that were present in the 39,000-event simulation dataset.

---

## 2. The Diagnosis: "The Middleman Problem"
The issue was identified as a **Data Truncation** bottleneck caused by the LLM context window.

### The Problematic Flow:
1.  **Claude** calls the `Amplitude MCP` to fetch events.
2.  **Amplitude MCP** sends raw JSON for thousands of events to Claude.
3.  **Claude** (to stay within memory/token limits) truncates the list to the latest **500 events**.
4.  **Claude** passes those 500 events to the `Tracking Plan MCP`.
5.  **Audit Engine**: Correctly scans all 500 events, but find 0 complex errors because the sample size is too small to contain a full user checkout lifecycle.

**Result**: A "passing" health score that hide deep, systemic regressions.

---

## 3. The Solution: "Direct Amplitude Auditing"
To overcome this, we moved the data transfer from the **LLM Layer** to the **System Layer**.

### The Refined Flow (`audit_amplitude_direct`):
1.  **Claude** sends a single trigger command: *"Audit Amplitude directly for the last 24h."*
2.  **Tracking Plan MCP**: Opens a direct HTTP socket to the Amplitude Export API using the credentials in `.env`.
3.  **Python Engine**: Downloads the full raw dataset (10k - 50k events) directly into local RAM.
4.  **Audit Engine**: Performs a 100% deterministic scan on the full dataset.
5.  **Summary Return**: Only the distilled, high-signal clusters (the "Top Drivers") are sent back to Claude.

---

## 4. Why This Works
- **Bypasses Memory Limits**: We can now audit 50,000 events as easily as 50.
- **Restores Signal Quality**: The complex M4 (Funnel) and M3 (Identity) checks now have enough data to "trigger" correctly.
- **Production-Ready**: This is how real-world governance tools work—they move the code to the data, not the data to the code.

## 5. Summary of Change
We transitioned the system from an **"AI-Fed Auditor"** to an **"Autonomous Direct-Fetch Auditor."** 

> [!TIP]
> **To verify this solution**, always use the prompt:
> *"Use the `audit_amplitude_direct` tool to audit the last [X] hours."*
