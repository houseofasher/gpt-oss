"""Tests for the remote (real-LLM) inference backend.

These verify the prompt->messages parsing and the token round-trip WITHOUT a
live API key, by monkeypatching the network call.
"""

import pytest

openai_harmony = pytest.importorskip("openai_harmony")
from openai_harmony import (  # noqa: E402
    Conversation,
    DeveloperContent,
    HarmonyEncodingName,
    Message,
    Role,
    StreamableParser,
    SystemContent,
    load_harmony_encoding,
)

from gpt_oss.responses_api.inference import remote  # noqa: E402

_ENC = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)


def _render_prompt(user_text: str, instructions: str = "Answer like a person.") -> list[int]:
    msgs = [
        Message.from_role_and_content(Role.SYSTEM, SystemContent.new()),
        Message.from_role_and_content(
            Role.DEVELOPER, DeveloperContent.new().with_instructions(instructions)
        ),
        Message.from_role_and_content(Role.USER, user_text),
    ]
    return _ENC.render_conversation_for_completion(
        Conversation.from_messages(msgs), Role.ASSISTANT
    )


def test_parse_prompt_extracts_system_and_user():
    prompt = _ENC.decode_utf8(_render_prompt("what type of algorithm is chatgpt"))
    messages = remote._parse_prompt_to_messages(prompt)
    assert messages[0]["role"] == "system"
    assert "Answer like a person." in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "what type of algorithm is chatgpt"}


def test_infer_streams_a_real_answer(monkeypatch):
    answer = "ChatGPT is a transformer-based large language model (a neural network)."
    monkeypatch.setattr(remote, "_call_chat_api", lambda messages, temp: answer)

    infer = remote.setup_model("unused")
    tokens_in = _render_prompt("what type of algorithm is chatgpt")

    # Drive the backend exactly like the server's generation loop does.
    parser = StreamableParser(_ENC, role=Role.ASSISTANT)
    seq = list(tokens_in)
    new_request = True
    stop = set(_ENC.stop_tokens_for_assistant_actions())
    for _ in range(2000):
        tok = infer(seq, temperature=0.0, new_request=new_request)
        new_request = False
        seq.append(tok)
        parser.process(tok)
        if tok in stop:
            break

    finals = [m for m in parser.messages if m.channel == "final"]
    assert finals, "expected a final assistant message"
    assert finals[-1].content[0].text == answer


def test_no_api_key_returns_honest_message(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = remote._call_chat_api([{"role": "user", "content": "hi"}], 0.7)
    assert "not configured" in out.lower()
    # Crucially, it is NOT a fake answer like "2 + 2 = 4".
    assert "2 + 2" not in out
