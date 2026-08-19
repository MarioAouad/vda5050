"""
Judge backends. JUDGE_BACKEND env var picks one: "ollama" (default),
"gemini", or "groq". Ollama is the default specifically because it's
local and free of Groq's ~30 RPM / ~1K RPD free-tier ceiling -- an
evaluation run does 1 generation call + 1 judge call per question, and
with a 10-15 question set that's cheap either way, but Ollama removes the
risk entirely and removes dependence on the two providers already under
quota pressure for the live system (see docs/PROJECT_GUIDE.md 4.7).

If you want to demonstrate cross-provider consistency (a nice thing to
mention in the defense -- "we checked the judge's verdict wasn't just an
artifact of one model"), run this script twice with JUDGE_BACKEND=ollama
and JUDGE_BACKEND=gemini and diff the two results files.
"""
from __future__ import annotations

import json
import os
import re

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")


def _call_ollama(prompt: str) -> str:
    resp = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"]


def _call_gemini(prompt: str) -> str:
    from langchain_google_genai import ChatGoogleGenerativeAI
    model = os.getenv("GEMINI_JUDGE_MODEL", "gemini-3.1-flash-lite")
    llm = ChatGoogleGenerativeAI(model=model, temperature=0.0)
    return llm.invoke(prompt).content


def _call_groq(prompt: str) -> str:
    from langchain_groq import ChatGroq
    model = os.getenv("GROQ_JUDGE_MODEL", "openai/gpt-oss-120b")
    llm = ChatGroq(model=model, temperature=0.0)
    return llm.invoke(prompt).content


_BACKENDS = {"ollama": _call_ollama, "gemini": _call_gemini, "groq": _call_groq}


def call_judge(prompt: str, backend: str | None = None) -> str:
    backend = backend or os.getenv("JUDGE_BACKEND", "ollama")
    if backend not in _BACKENDS:
        raise ValueError(f"Unknown JUDGE_BACKEND '{backend}'. Choose from: {list(_BACKENDS)}")
    return _BACKENDS[backend](prompt)


RUBRIC_PROMPT_TEMPLATE = """You are grading a RAG system's answer to a question about the VDA 5050 \
AGV/AMR communication standard. Score it on three criteria, each 1-5 (5 = best):

- faithfulness: does the answer ONLY state things supported by the retrieved context below? \
(A confident-sounding answer not grounded in the context is a faithfulness failure, even if it \
happens to be factually true from general knowledge.)
- correctness: does the answer match the reference facts listed below?
- relevance: does the answer actually address the question asked, without padding or drift?

If must_refuse is true, the correct answer is some form of "the retrieved context doesn't cover \
this" -- score correctness LOW if the answer instead invents a confident-sounding number or fact.

Question: {query}
Retrieved context:
---
{context}
---
Reference facts a good answer should reflect: {reference_facts}
must_refuse: {must_refuse}

System's answer:
---
{answer}
---

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"faithfulness": <1-5>, "correctness": <1-5>, "relevance": <1-5>, "justification": "<one sentence>"}}
"""


def parse_judge_json(raw: str) -> dict:
    """Judges (especially smaller local models) sometimes wrap JSON in
    prose or code fences despite instructions -- this pulls out the first
    {...} block rather than failing the whole eval run on one bad parse."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"faithfulness": None, "correctness": None, "relevance": None, "justification": f"UNPARSEABLE JUDGE OUTPUT: {raw[:200]}"}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"faithfulness": None, "correctness": None, "relevance": None, "justification": f"UNPARSEABLE JUDGE OUTPUT: {raw[:200]}"}
