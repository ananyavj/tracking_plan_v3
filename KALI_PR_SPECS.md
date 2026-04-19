# Kaliper: State-Aware Analytics Governance & Monitoring

## 1. Project Identity & Mission
**Kaliper** is a production-grade analytics monitoring system designed to enforce 100% compliance between a company's **Tracking Plan** (Source of Truth) and its **Live Data Streams** (Amplitude). 

Unlike traditional monitoring that only alerts on volume drops, Kaliper performs a **Deep Schema & Lifecycle Audit** to detect silent regressions, data quality drift, and instrumentation gaps before they corrupt business decisions.

---

## 2. Core Architecture: The "Governed Hybrid" Model

The system operates on a "Hybrid" model that separates **Deterministic Scanning** from **Heuristic Diagnosis**.

### Phase A: The Deterministic Engine (`AuditEngine.py`)
- **Philosophy**: 100% Scan, 0% AI Hallucination.
- **Logic**: Every incoming event is scanned against a normalized schema derived from the `tracking_plan.xlsx`.
- **Constraint**: The audit is entirely code-based (Python), ensuring that issue counts and health scores are mathematically accurate and defensible.

### Phase B: Mathematical Foundations
- **Self-Deriving Health Score**: To avoid "magic constants," the system derives the maximum possible penalty from the severity weights in the config. 
  - *Formula*: `100 * (1 - WeightedPenalty / (TotalEvents * MaxSeverityWeight))`
- **Blast Radius Tracking**: Instead of just counting raw errors, Kaliper tracks the **Unique Event Impact %**. If one bug affects 3 properties on 1 event, the blast radius is still only 1 event.

### Phase C: Stateful Lifecycle Registry
Kaliper maintains a "memory" of the last 5 audit cycles. This allows for **State-Aware Alerting**:
- **📍 Persistent**: The issue was found in the previous run. (Team hasn't fixed it yet).
- **🔄 Regression**: The issue was previously resolved but has just returned. (High-signal warning).
- **🆕 New**: A brand-new tracking gap detected for the first time.

### Phase D: Autonomous AI Diagnosis (`GeminiAgent.py`)
- **Philosophy**: AI is a "Forensic Scientist," not a scanner.
- **Role**: Once the Python engine identifies a critical cluster (e.g., a "Persistent P0"), the Gemini 2.0 Agent is triggered.
- **Tools**: The agent has autonomous access to `get_user_history` to distinguish between a **genuine instrument gap** and a **cross-device session split**.
- **Constraint**: AI output is strictly forced into a **Root Cause / Impact / Suggested Fix** structure.

---

## 3. Deployment & Interaction Layers

### 1. The Headless Scheduler (`scheduler.py`)
- **Mode**: Production monitoring.
- **Function**: Runs a "Scan -> Score -> Alert" loop (e.g., every 60 minutes).
- **Observability**: Tracks `last_success_duration` and `last_attempt` heartbeats to ensure the monitoring system itself hasn't failed.

### 2. The MCP Server (`tracking_mcp_server.py`)
- **Mode**: Investigative/Hybrid.
- **Function**: Connects Claude Desktop (or any LLM) directly to the local Python engine.
- **Breakthrough**: Includes an `audit_amplitude_direct` tool. This allows Claude to trigger a 50,000-event audit where the Python script pulls the data directly from Amplitude, bypassing the memory/token limits of the LLM context window.

---

## 4. Engineering Constraints & Principles (The "How")

### I. Quad-Key Deduplication
To prevent "Alert Fatigue," every issue is assigned a unique **Quad-Key**: 
`(ErrorCode : EventName : PropertyName : Platform)`
This ensures that we distinguish between a bug happening on "iOS" vs "Android" and don't group them as a single generic error.

### II. Mutually Exclusive Logic
Issue classification follows a strict hierarchy during evaluation:
1. Is it in the last run? → **Persistent**
2. If not, is it in the last 5 runs? → **Regression**
3. If not, it is **New**.
This prevents overlapping labels and keeps the logic explainable during technical interviews.

### III. Data Integrity
- **Atomic Writes**: All persistence files (`audit_metadata.json`, `audit_history.json`) use a **temp-and-replace** write pattern to prevent corruption during system crashes.
- **Metadata Quality Audit**: The system treats "Missing Platform Metadata" as a primary error (`Unknown Platform %`), categorizing it as a data quality gap that directly affects the health score.

---

## 5. Summary of Achievement
Kaliper has evolved from a basic script into a **Stateful Governance Pipeline**. It effectively bridges the gap between raw Big Data (40k+ events) and actionable executive summaries (Health Trends & Root Causes), allowing organizations to treat their data pipeline with the same rigor as their source code.
