# 📊 Tracking Plan Auditor

**An autonomous analytics validation engine.** Organizes your tracking plan (Excel) and your actual live Amplitude data, then uses AI (Claude/Gemini) to cross-reference them and catch instrumentation bugs before they pollute your data.

---

## 🎯 What it Does
*   **Automated Audits:** Validates how closely your actual Amplitude events align with your defined tracking plan.
*   **M0–M6 Classification:** Specifically detects and reports mistake types:
    *   **M1–M3:** Schema errors (Type mismatches, missing properties, invalid event names).
    *   **M4–M6:** Logic errors (Funnel breaks, inconsistent product IDs across sessions, user state issues).
*   **Dual-Path Architecture:** 
    1.  **Claude Desktop:** Direct, local interaction via the Native Claude app using MCP (Primary).
    2.  **Streamlit Dashboard:** Interactive web dashboard using Gemini (Fallback/Rapid Testing).

---

## 🚀 How to Run the Project

### 1. Initial Setup
Clone the repository and enter the project folder:
```bash
git clone https://github.com/ananyavj/tracking_plan_v3.git
cd tracking_plan_v3
```

### 2. Environment Configuration
Create a virtual environment and install the required dependencies:
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Authentication & API Keys
Create a `.env` file in the root directory with your credentials:
```env
GOOGLE_API_KEY=your_gemini_key
AMPLITUDE_API_KEY=your_amplitude_api_key
AMPLITUDE_SECRET_KEY=your_amplitude_secret_key
AMPLITUDE_PROJECT_ID=your_id
```

### 4. Running the Web App (Streamlit)
Launch the interactive dashboard:
```bash
streamlit run app.py
```

### 5. Running via Claude Desktop (Optional)
To use Claude Desktop natively:
1.  Open your `claude_desktop_config.json` file.
2.  Add both the official `@amplitude/mcp-server` and this project’s `tracking_mcp_server.py`.
3.  *Detailed setup can be found in `claude_desktop_instructions.md`.*

---

## 💡 Technical Notes & Limitations

*   **Spot Check Logic:** This tool is designed for **Probabilistic Auditing**, not 100% database validation. It audits a high-signal 0.75% sample of your data to catch systemic bugs.
*   **Architectural Gap:** 
    *   **Claude:** Uses "Multi-Turn" fetching, allowing it to reconstruct deep, full sessions for logic checks (M4–M6).
    *   **Gemini:** Currently uses a "Single-Shot" prompt limit (200 events). A **High-Signal Sampler** is being implemented to maximize the quality of these 200 events.
*   **Security:** All API keys are stored in your local `.env`. Ensure this file remains in your `.gitignore` and is never pushed to public repositories.

---

**Built with ❤️ for better data quality.**
