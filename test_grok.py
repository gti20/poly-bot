import os
import requests

key = os.getenv("GROK_API_KEY")
print("Key starts with:", key[:10] if key else "MISSING")

headers = {
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json"
}
payload = {
    "model": "grok-3-mini",
    "messages": [{"role": "user", "content": "Say hello"}]
}

r = requests.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers)
print(r.status_code)
print(r.text[:500])  # first 500 chars of response