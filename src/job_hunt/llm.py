"""Shared LLM harness — Anthropic first, then NIM → OpenRouter → OpenAI."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List

import httpx
from openai import OpenAI

from .config import env

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_NVIDIA_MODEL = "meta/llama-3.1-8b-instruct"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"


@dataclass
class LLMProvider:
    name: str
    client: OpenAI | None
    model: str
    kind: str = "openai"  # openai | anthropic


def _providers() -> List[LLMProvider]:
    """Anthropic → NVIDIA → OpenRouter → OpenAI."""
    chain: List[LLMProvider] = []
    timeout = float(env("LLM_TIMEOUT_SEC", "90") or "90")

    anthropic = env("ANTHROPIC_API_KEY")
    if anthropic:
        chain.append(
            LLMProvider(
                name="anthropic",
                client=None,
                model=env("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL) or DEFAULT_ANTHROPIC_MODEL,
                kind="anthropic",
            )
        )

    nvidia = env("NVIDIA_API_KEY")
    if nvidia:
        chain.append(
            LLMProvider(
                name="nvidia",
                client=OpenAI(
                    api_key=nvidia,
                    base_url=env("NVIDIA_BASE_URL", NVIDIA_BASE_URL) or NVIDIA_BASE_URL,
                    timeout=timeout,
                ),
                model=env("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL,
            )
        )

    openrouter = env("OPENROUTER_API_KEY")
    if openrouter:
        chain.append(
            LLMProvider(
                name="openrouter",
                client=OpenAI(
                    api_key=openrouter,
                    base_url=env("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL) or OPENROUTER_BASE_URL,
                    timeout=timeout,
                    default_headers={
                        "HTTP-Referer": env(
                            "OPENROUTER_REFERER",
                            "https://github.com/garvitkhurana/job-hunt-agent",
                        )
                        or "https://github.com/garvitkhurana/job-hunt-agent",
                        "X-Title": "job-hunt-agent",
                    },
                ),
                model=env("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL,
            )
        )

    openai_key = env("OPENAI_API_KEY")
    if openai_key:
        chain.append(
            LLMProvider(
                name="openai",
                client=OpenAI(api_key=openai_key, timeout=timeout),
                model=env("OPENAI_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini",
            )
        )

    return chain


def has_llm_key() -> bool:
    return bool(_providers())


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise json.JSONDecodeError("no json object found", text, 0)


def _anthropic_once(model: str, messages: list, temperature: float) -> dict:
    key = env("ANTHROPIC_API_KEY") or ""
    system = ""
    converted = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            converted.append({"role": m["role"], "content": m["content"]})
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": converted,
    }
    if system:
        payload["system"] = system
    timeout = float(env("LLM_TIMEOUT_SEC", "90") or "90")
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return extract_json(text)


def _openai_once(provider: LLMProvider, messages: list, temperature: float) -> dict:
    assert provider.client is not None
    kwargs = {
        "model": provider.model,
        "temperature": temperature,
        "messages": messages,
    }
    try:
        resp = provider.client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"}
        )
        return extract_json(resp.choices[0].message.content or "{}")
    except Exception:
        resp = provider.client.chat.completions.create(**kwargs)
        return extract_json(resp.choices[0].message.content or "{}")


def chat_json(messages: list, temperature: float = 0.4) -> dict:
    providers = _providers()
    if not providers:
        raise RuntimeError(
            "No LLM key set. Add ANTHROPIC_API_KEY and/or NVIDIA_API_KEY / OPENROUTER_API_KEY to .env"
        )
    errors: List[str] = []
    for p in providers:
        try:
            if p.kind == "anthropic":
                return _anthropic_once(p.model, messages, temperature)
            return _openai_once(p, messages, temperature)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p.name}/{p.model}: {e}")
            continue
    raise RuntimeError("All LLM providers failed — " + " | ".join(errors))
