"""
Provider-agnostic LLM client (Gemini / Groq / OpenAI) using only the standard library.

If no API key is configured — or the call fails — callers fall back to a deterministic
"offline" generator so the demo always works. Every response says which provider produced it.
"""
import json
import re
import time
import urllib.request
import urllib.error

from .. import config


def active_provider() -> str:
    p = config.LLM_PROVIDER
    if p == "offline":
        return "offline"
    if p == "gemini" and config.GEMINI_API_KEY:
        return "gemini"
    if p == "groq" and config.GROQ_API_KEY:
        return "groq"
    if p == "openai" and config.OPENAI_API_KEY:
        return "openai"
    # auto-detect from whichever key is present
    if config.GEMINI_API_KEY:
        return "gemini"
    if config.GROQ_API_KEY:
        return "groq"
    if config.OPENAI_API_KEY:
        return "openai"
    return "offline"


def _post(url, body, headers, timeout=40):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _openai_style(url, key, model, system, user, temperature, json_mode, history):
    msgs = [{"role": "system", "content": system}]
    for h in history or []:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:4000]})
    msgs.append({"role": "user", "content": user})
    body = {"model": model, "messages": msgs, "temperature": temperature, "max_tokens": 1500}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _post(url, body, {"Authorization": f"Bearer {key}"})
    return data["choices"][0]["message"]["content"]


def _gemini(system, user, temperature, json_mode, history):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
           f"?key={config.GEMINI_API_KEY}")
    contents = []
    for h in history or []:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            contents.append({"role": "user" if h["role"] == "user" else "model",
                             "parts": [{"text": str(h["content"])[:4000]}]})
    contents.append({"role": "user", "parts": [{"text": user}]})
    gen = {"temperature": temperature, "maxOutputTokens": 1500}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": gen}
    data = _post(url, body, {})
    return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])


def generate(system: str, user: str, *, temperature=0.3, json_mode=False, history=None, fallback=None):
    """
    Returns (text, provider, latency_ms). `fallback` is a zero-arg callable producing offline text.
    """
    provider = active_provider()
    t0 = time.time()
    if provider != "offline":
        try:
            if provider == "gemini":
                text = _gemini(system, user, temperature, json_mode, history)
            elif provider == "groq":
                text = _openai_style("https://api.groq.com/openai/v1/chat/completions", config.GROQ_API_KEY,
                                     config.GROQ_MODEL, system, user, temperature, json_mode, history)
            else:
                text = _openai_style("https://api.openai.com/v1/chat/completions", config.OPENAI_API_KEY,
                                     config.OPENAI_MODEL, system, user, temperature, json_mode, history)
            return text.strip(), provider, int((time.time() - t0) * 1000)
        except Exception as e:   # network error, quota, bad key ...
            provider = f"offline (fallback: {type(e).__name__})"
    text = fallback() if fallback else "GenAI provider not configured."
    return text, provider, int((time.time() - t0) * 1000)


def parse_json(text: str):
    """Extract the first JSON object from model output (handles ```json fences)."""
    if isinstance(text, dict):
        return text
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None
