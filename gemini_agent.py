import google.generativeai as genai
import json
from mcp_tools import execute_get_amplitude_events

def run_gemini_audit_agent(google_api_key, system_prompt, tracking_plan, app_config, status_callback=None):
    """
    Runs the agent loop, supplying tools to Gemini so it can fetch the Amplitude events itself.
    """
    if status_callback: status_callback("Initializing Gemini 1.5 Flash Agent...")
    
    genai.configure(api_key=google_api_key)
    
    # Define the tool as a typed Python function for Gemini's auto-schema generator
    def get_amplitude_events(days_back: int = 30) -> dict:
        """Fetch raw events from Amplitude for the currently configured project. This provides real production data for auditing against the tracking plan."""
        if status_callback: status_callback("Gemini executing tool: get_amplitude_events...")
        
        result = execute_get_amplitude_events({"days_back": days_back}, app_config)
        
        if status_callback:
            if "error" in result:
                status_callback(f"Failed fetching events: {result['error']}")
            else:
                num_events = result.get('events_returned', 0)
                status_callback(f"Fetched {num_events} events. Gemini is analyzing...")
                
        return result

    # Initialize Gemini model with tools and system instruction
    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        tools=[get_amplitude_events],
        system_instruction=system_prompt,
        generation_config={"temperature": 0.0}
    )
    MODEL_DISPLAY_NAME = "gemini-flash-latest"
    
    # We use enable_automatic_function_calling so the SDK runs the python function for us!
    chat = model.start_chat(enable_automatic_function_calling=True)
    
    initial_user_message = f"""
    TRACKING_PLAN: {json.dumps(tracking_plan)}
    MODEL_NAME: {MODEL_DISPLAY_NAME}

    You are running as: {MODEL_DISPLAY_NAME}. Use this exact string for the `model_used` field in audit_meta and in the HTML report footer. Do NOT use any Claude model names.

    Please fetch the Amplitude events using the `get_amplitude_events` tool, 
    and then use the system prompt rules to audit those events against the provided TRACKING_PLAN.
    Return ONLY the final JSON output contract containing the html_report. Do not wrap in markdown tags.
    """
    
    if status_callback: status_callback("Prompting Gemini with tracking plan...")
    
    try:
        response = chat.send_message(initial_user_message)
        
        # Google's model might add markdown fences (```json ... ```), so we strip them
        raw_text = response.text.replace("```json", "").replace("```", "").strip()
        
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return {
                "error": "Gemini response was not valid JSON",
                "raw": response.text
            }
            
    except Exception as e:
        return {
            "error": f"Unexpected error during API call: {str(e)}",
            "raw": ""
        }
