"""Test LLM connectivity. Run: PYTHONPATH=/app python scripts/test_llm.py"""
import os, sys
sys.path.insert(0, '/app')
os.chdir('/app')
from dotenv import load_dotenv
load_dotenv()
import litellm

key = os.getenv('GROQ_API_KEY', '')
print(f"GROQ_API_KEY set: {'yes' if key else 'NO'}")

for pm, model in [('PM1', 'groq/llama-3.3-70b-versatile'), ('PM2', 'groq/llama-3.3-70b-versatile')]:
    try:
        r = litellm.completion(model=model, messages=[{'role':'user','content':'say hi in one word'}], api_key=key, max_tokens=10, timeout=15)
        print(f"{pm} ({model}): OK — '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        print(f"{pm} ({model}): FAILED — {e}")
