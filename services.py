"""Two metered tools. Pricing is in USD at the November 2026 standard rates."""

import json
import os

import requests
from dotenv import load_dotenv

import storage

load_dotenv(storage.ROOT / ".env")
MODEL = "gemini-3.8-flash"
MAX_OUTPUT = 1800
GEMINI_LIMIT = 3.00
EXA_LIMIT = 6.00


def gemini(prompt, run_id, lead_id):
    if not os.environ.get("GOOGLE_API_KEY"):
        raise RuntimeError("Add GOOGLE_API_KEY to .env for live inference.")
    size = len(prompt.encode("utf-8"))
    if size > 100_000:
        raise ValueError("Evidence exceeds the safe prompt size.")
    # UTF-8 byte count bounds input tokens conservatively. Unanswered calls
    # retain their reservation because the provider might have run the request.
    maximum = (size + 256) * 0.75e-6 + MAX_OUTPUT * 3.75e-6
    reservation = storage.reserve(run_id, lead_id, "Gemini", maximum, GEMINI_LIMIT, 8)
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": os.environ["GOOGLE_API_KEY"]},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": MAX_OUTPUT,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        },
        timeout=(8, 55),
        allow_redirects=False,
    )
    response.raise_for_status()
    document = response.json()
    usage = document["usageMetadata"]
    inputs = usage.get("promptTokenCount", 0)
    outputs = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    storage.record_usage(reservation, inputs * 0.75e-6 + outputs * 3.75e-6, inputs, outputs)
    parts = document["candidates"][0]["content"]["parts"]
    value = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object from the model.")
    return value


def exa(query, run_id, lead_id):
    if not os.environ.get("EXA_API_KEY"):
        raise RuntimeError("Add EXA_API_KEY to .env for live research.")
    reservation = storage.reserve(run_id, lead_id, "Exa", 0.014, EXA_LIMIT, 3)
    response = requests.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": os.environ["EXA_API_KEY"]},
        json={
            "query": query,
            "type": "auto",
            "numResults": 6,
            "contents": {"text": {"maxCharacters": 2000}},
        },
        timeout=(8, 30),
        allow_redirects=False,
    )
    response.raise_for_status()
    document = response.json()
    dollars = document.get("costDollars", {}).get("total", 0.007)
    storage.record_usage(reservation, float(dollars))
    return [
        {"url": item["url"], "title": item.get("title", ""), "text": item.get("text", "")[:2000]}
        for item in document.get("results", [])
        if item.get("url")
    ]
