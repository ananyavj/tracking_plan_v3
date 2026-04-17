# Kaliper Tracking Auditor v3.0

The ultimate autonomous tracking audit engine for Shopify & Segment stacks.

## 🚀 Quick Start (Local Dashboard)
1. Install dependencies: `pip install -r requirements.txt`
2. Configure `.env` with your API keys.
3. Run the dashboard: `streamlit run app.py`

## 🤖 Claude Desktop Integration (MCP)
Run high-performance audits directly from your Claude chat using the **Hybrid strategy**:
1. Follow the steps in `claude_desktop_instructions.md` to configure your `claude_desktop_config.json`.
2. Open Claude and use this prompt: 
   > "Fetch the latest 500 events from Amplitude and run a comprehensive audit using your local tools."

## 📅 Automated Daily Audits (GitHub Actions)
The project includes a `daily_audit.yml` that runs every day at 3 AM UTC.
To set this up, go to your GitHub Repo -> **Settings** -> **Secrets and variables** -> **Actions** and add:
- `AMPLITUDE_API_KEY`
- `AMPLITUDE_SECRET_KEY`
- `AMPLITUDE_PROJECT_ID`
- `SLACK_WEBHOOK_URL` (Optional)

## 🛠 Project Structure
- `audit_engine.py`: The core zero-bias logic (M0-M7 rules).
- `tracking_mcp_server.py`: The bridge between Python and Claude.
- `scheduler.py`: Headless entry point for automated runs.
- `simulate.py`: High-fidelity e-commerce data simulator.
- `tracking_plan.xlsx`: Your source of truth (Shopify/Segment compliant).
