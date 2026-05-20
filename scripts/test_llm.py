"""Test LLM connectivity. Run: PYTHONPATH=/app python scripts/test_llm.py"""
import os, sys
sys.path.insert(0, '/app')
os.chdir('/app')
from dotenv import load_dotenv
load_dotenv()
import litellm

key = os.getenv('AZURE_AI_API_KEY', '')
base = 'https://trd-frm-resource.services.ai.azure.com/models'
print(f"AZURE_AI_API_KEY set: {'yes' if key else 'NO'}")

for pm, model in [('PM1', 'openai/Kimi-K2.6'), ('PM2', 'openai/Kimi-K2.6')]:
    try:
        r = litellm.completion(model=model, messages=[{'role':'user','content':'say hi in one word'}], api_key=key, api_base=base, max_tokens=10, timeout=20)
        print(f"{pm} ({model}): OK — '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        print(f"{pm} ({model}): FAILED — {e}")
