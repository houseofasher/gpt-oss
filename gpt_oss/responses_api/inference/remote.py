"""
Remote model backend — connect the Responses API to a REAL LLM.

Railway (and any CPU-only host) cannot run the gpt-oss weights, so the `stub`
backend just replays canned tokens ("2 + 2 = 4") regardless of input. This
backend fixes that: it decodes the harmony prompt the server built (which
already carries the Brain Orchestrator's doctrines + retrieved knowledge),
forwards it to an OpenAI-compatible chat API, and streams the real answer back
as harmony tokens. The rest of the server is unchanged.

It plugs into the same `setup_model -> infer_next_token` contract the stub uses,
so `/v1/responses` keeps working — it just returns real answers now.

Configure with env vars (set these on Railway):
  LLM_API_KEY    (or OPENAI_API_KEY)  — required for real answers
  LLM_BASE_URL   default https://api.openai.com/v1  (any OpenAI-compatible API:
                 OpenRouter, Groq, Together, a local vLLM, etc.)
  LLM_MODEL      default gpt-4o-mini
  LLM_TEMPERATURE default 0.7
  LLM_MAX_TOKENS  default 1024

If no API key is set, it returns a CLEAR "no model connected" message — never a
fake answer dressed up as a real one.
"""

import os
import re
from typing import Callable, Optional

from openai_harmony import HarmonyEncodingName, load_harmony_encoding

# Pull (role, content) blocks out of a rendered harmony prompt. The trailing
# "<|start|>assistant" (no message yet) is intentionally not matched.
_MSG_RE = re.compile(
    r"<\|start\|>(?P<header>.*?)<\|message\|>(?P<content>.*?)<\|(?:end|return)\|>",
    re.DOTALL,
)


def _parse_prompt_to_messages(prompt_text: str) -> list[dict]:
    """Reconstruct OpenAI-style chat messages from the harmony prompt text."""
    system_parts: list[str] = []
    turns: list[dict] = []
    for match in _MSG_RE.finditer(prompt_text):
        header = match.group("header").strip()
        content = match.group("content").strip()
        if not content:
            continue
        # header looks like "user" or "assistant<|channel|>final"; role is the
        # leading token before any channel marker.
        role = header.split("<|channel|>")[0].strip().split()[0] if header else "user"
        if role in ("system", "developer"):
            system_parts.append(content)
        elif role == "assistant":
            turns.append({"role": "assistant", "content": content})
        else:  # user (and anything unexpected) -> user
            turns.append({"role": "user", "content": content})

    messages: list[dict] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.extend(turns)
    if not any(m["role"] == "user" for m in messages):
        # Fallback: never send an empty/user-less request.
        messages.append({"role": "user", "content": prompt_text.strip()[-4000:]})
    return messages


def _api_key() -> Optional[str]:
    return os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _call_chat_api(messages: list[dict], temperature: float) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint. Honest on failure."""
    key = _api_key()
    if not key:
        return (
            "[backend not configured] No language model is connected to this "
            "server. Set LLM_API_KEY (and optionally LLM_BASE_URL / LLM_MODEL) "
            "in the deployment environment to enable real answers."
        )
    import requests  # local import: only needed when actually serving

    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    try:
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
    except ValueError:
        max_tokens = 1024
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or "[empty response from model]"
    except Exception as exc:  # noqa: BLE001 - surface the error, never fake an answer
        return f"[model backend error] {type(exc).__name__}: {exc}"


def setup_model(_checkpoint: str) -> Callable[[list[int], float, bool], int]:
    """Return an infer_next_token that proxies to a remote chat model."""
    encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
    stop_token = encoding.stop_tokens_for_assistant_actions()[0]
    queue: list[int] = []

    def infer_next_token(
        tokens: list[int], temperature: float = 0.0, new_request: bool = False
    ) -> int:
        nonlocal queue
        if new_request:
            queue = []
            try:
                prompt_text = encoding.decode_utf8(tokens)
                messages = _parse_prompt_to_messages(prompt_text)
                answer = _call_chat_api(messages, temperature or 0.7)
            except Exception as exc:  # noqa: BLE001
                answer = f"[backend error] {type(exc).__name__}: {exc}"
            completion = f"<|channel|>final<|message|>{answer}<|return|>"
            queue = list(encoding.encode(completion, allowed_special="all"))
        if queue:
            return queue.pop(0)
        return stop_token

    return infer_next_token
