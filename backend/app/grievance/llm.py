"""
backend/app/grievance/llm.py

PHASE 4 — LLM-assisted formalization of a grievance draft.

Turns the student's raw complaint into a polished, factual formal grievance:

  raw:  "my admit card is missing and my exam is next week"
  out:  {"subject": "Missing admit card", "text": "My admit card has not
          been issued and my examination is scheduled next week. ..."}

Safety contract (hard requirements):
  * The LLM never invents facts. Instructions forbid adding names, dates,
    amounts, roll numbers, or events that the student did not state. Unknown
    details are left vague (e.g. "on the scheduled date" instead of a made-up
    date).
  * The output is always validated: subject/text lengths, forbidden-fact
    checking is best-effort (the prompt is the main guard), and any failure
    (network, JSON, model errors) falls back to a clean manual draft instead
    of erroring — the student can always write/edit by hand (spec §25/§26).
  * Ollama runs locally; nothing leaves the machine.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import settings

_log = logging.getLogger("cus_ai")

SYSTEM_PROMPT = (
    "You are an assistant that helps a university student formalize a "
    "grievance. Rewrite the student's complaint into polite, clear, formal "
    "English suitable for an official grievance. Restate ONLY what the "
    "student actually wrote. NEVER invent facts: do not add names, dates, "
    "amounts, roll numbers, documents, applications, deadlines, statuses, "
    "causes, or an event that was not mentioned. If a detail is unknown, "
    "leave it out. Do not include demands for money or compensation. "
    "Return ONLY a JSON object with keys \"subject\" (a very short title, "
    "under 60 characters) and \"text\" (the formal grievance, 40 to 700 "
    "words)."
)

MAX_INPUT_CHARS = 4000
MAX_OUTPUT_CHARS = 4000
_TIMEOUT = 60.0


def _sanitize_input(raw: str) -> str:
    """Trim and cap the raw complaint before it reaches the LLM."""
    text = re.sub(r"\s+", " ", (raw or "").strip())
    return text[:MAX_INPUT_CHARS]


def _parse_llm_json(content: str) -> dict:
    """Parse and validate the LLM's JSON response, tolerating markdown fences."""
    text = content.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in LLM response")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response is not an object")
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("text") or "").strip()
    if not subject or not body:
        raise ValueError("LLM response missing subject/text")
    return {"subject": subject[:200], "text": body[:MAX_OUTPUT_CHARS]}


def _manual_draft(raw: str) -> dict:
    """Deterministic fallback draft when the LLM is unavailable or invalid."""
    clean = (" ".join(re.split(r"\s+", raw.strip())) or "My request").strip()
    subject = _derive_subject(clean)
    return {"subject": subject, "text": clean, "manual": True}


def _derive_subject(raw: str) -> str:
    """Small deterministic title from the raw complaint (fallback only)."""
    words = re.split(r"\s+", raw)
    if len(words) <= 6:
        base = raw
    else:
        base = " ".join(words[:6]) + "..."
    if len(base) > 60:
        base = base[:57] + "..."
    return base


def formalize(raw_input: str, model: str | None = None) -> dict:
    """Produce a formal grievance draft from the raw student input.

    Returns:
      {"generated": bool, "subject": str, "text": str,
       "error": str|None, "manual": bool}
    generated is False when the LLM was unavailable/invalid and the
    deterministic fallback was used (frontend switches to manual editing).
    Never raises for LLM/network failures.
    """
    clean = _sanitize_input(raw_input)
    if not clean:
        return {"generated": False, "subject": "", "text": "", "error": "empty input", "manual": True}

    model = model or settings.LLM_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": clean},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_predict": 700,
        },
    }
    try:
        with httpx.Client(base_url=settings.OLLAMA_BASE_URL, timeout=_TIMEOUT) as client:
            resp = client.post("/api/chat", json=payload)
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
        data = _parse_llm_json(content)
        return {
            "generated": True,
            "subject": data["subject"],
            "text": data["text"],
            "error": None,
            "manual": False,
        }
    except Exception as exc:  # noqa: BLE001  — must never block the student
        _log.warning("grievance formalization failed (%s); using manual draft", exc)
        draft = _manual_draft(clean)
        return {
            "generated": False,
            "subject": draft["subject"],
            "text": draft["text"],
            "error": str(exc)[:200],
            "manual": True,
        }


__all__ = ["formalize"]