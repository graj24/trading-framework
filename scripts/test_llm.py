"""Quick test to verify NVIDIA NIM LLM connectivity. Run on EC2:
    PYTHONPATH=/app python scripts/test_llm.py
"""
import os, sys
sys.path.insert(0, '/app')
os.chdir('/app')
from dotenv import load_dotenv
load_dotenv()
import litellm

key = os.getenv('NVIDIA_NIM_API_KEY', '')
print(f"Key set: {'yes' if key else 'NO - set NVIDIA_NIM_API_KEY in /app/.env'}")

for pm, model in [('PM1', 'openai/moonshotai/kimi-k2.6'), ('PM2', 'openai/deepseek-ai/deepseek-v3-1')]:
    try:
        r = litellm.completion(
            model=model,
            messages=[{'role': 'user', 'content': 'say hi in one word'}],
            api_base='https://integrate.api.nvidia.com/v1',
            api_key=key,
            max_tokens=10,
        )
        print(f"{pm} ({model}): OK — '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        print(f"{pm} ({model}): FAILED — {e}")
