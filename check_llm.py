"""Diagnose the Gemini connection. Run:  python check_llm.py

Prints the actual HTTP status so we know exactly what's wrong:
  200 = key works. 400/401/403 = bad/wrong-type key. 429 = daily quota exhausted.
"""
import os, sys, httpx

# reuse the app's own .env loader so we test exactly what the app sees
import semantic  # noqa: F401  (its import runs _load_dotenv_once)

key = os.environ.get("GEMINI_API_KEY", "")
if not key:
    sys.exit("No GEMINI_API_KEY found — .env missing or in the wrong folder.")
print(f"Key loaded (starts '{key[:4]}...', length {len(key)})")

try:
    r = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": semantic.MODEL_ID,
              "messages": [{"role": "user", "content": "Reply with exactly: OK"}]},
        timeout=30,
    )
except Exception as e:
    sys.exit(f"Network error before reaching Google: {type(e).__name__}: {e}")

print("HTTP", r.status_code)
if r.status_code == 200:
    print("Model replied:", r.json()["choices"][0]["message"]["content"].strip())
    print("=> KEY WORKS. The semantic layer should run.")
elif r.status_code in (400, 401, 403):
    print(r.text[:500])
    print("=> Key rejected. Get an API key at aistudio.google.com -> Get API key.")
elif r.status_code == 429:
    print(r.text[:300])
    print("=> Quota exhausted for today. Try tomorrow or a key on a different account.")
else:
    print(r.text[:500])
