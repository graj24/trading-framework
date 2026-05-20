"""Test LLM connectivity. Run: PYTHONPATH=/app python scripts/test_llm.py"""
import os, sys
sys.path.insert(0, '/app')
os.chdir('/app')
from dotenv import load_dotenv
load_dotenv()
import litellm

key = os.getenv('AGENTROUTER_API_KEY') or os.getenv('GROQ_API_KEY', '')
provider = 'AgentRouter' if os.getenv('AGENTROUTER_API_KEY') else 'Groq'
print(f"{provider} key set: {'yes' if key else 'NO'}")

for pm, model in [('PM1', 'openai/moonshotai/kimi-k2.6'), ('PM2', 'openai/moonshotai/kimi-k2.6')]:
    try:
        kwargs = dict(model=model, messages=[{'role':'user','content':'say hi in one word'}], api_key=key, max_tokens=10, timeout=15)
        if provider == 'AgentRouter':
            kwargs['api_base'] = 'https://agentrouter.org/'
        r = litellm.completion(**kwargs)
        print(f"{pm} ({model}): OK — '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        print(f"{pm} ({model}): FAILED — {e}")
