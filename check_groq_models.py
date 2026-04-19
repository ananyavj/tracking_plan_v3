import os
from groq import Groq
from dotenv import load_dotenv

# Load .env to get the GROQ_API_KEY
load_dotenv(override=True)

def list_models():
    api_key = os.getenv("GROQ_API_KEY")
    
    if not api_key or "your_free_key_here" in api_key:
        print("❌ Error: GROQ_API_KEY is not set correctly in your .env file.")
        return

    try:
        client = Groq(api_key=api_key)
        models = client.models.list()
        
        print("\nSuccess! Your API key has access to these models:\n")
        print(f"{'MODEL ID':<30} {'OWNED BY':<15}")
        print("-" * 45)
        
        for model in models.data:
            # Filter for text models only for clarity
            if "whisper" not in model.id:
                print(f"{model.id:<30} {model.owned_by:<15}")
        
        print("\nRecommendation: Use 'llama3-70b-8192' for the best diagnostic results.")
        
    except Exception as e:
        print(f"Error: Failed to fetch models: {str(e)}")

if __name__ == "__main__":
    list_models()
