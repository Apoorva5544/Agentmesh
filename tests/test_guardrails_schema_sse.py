"""Tests for the PII guardrail, JSON-schema enforcement, and SSE framing."""

import json

import pytest

from app.guardrails import redact_pii
from app.json_schema import SchemaValidationError, build_prompt, validate_output
from app.sse import chat_delta, done, sse_error, sse_event


# ------------------------------------------------------------------- guardrails
def test_redact_aadhaar_and_pan() -> None:
    text = "Customer aadhaar 1234 5678 9012 and PAN ABCDE1234F attached."
    redacted, detections = redact_pii(text)
    assert "1234 5678 9012" not in redacted
    assert "ABCDE1234F" not in redacted
    assert not any(d["type"] == "credit_card" for d in detections)
    types = sorted(d["type"] for d in detections)
    assert types == ["aadhaar", "pan"]
    assert sum(d["count"] for d in detections) == 2


def test_redact_phone_and_email() -> None:
    text = "Reach vikram@acme.in or call 9876543210."
    redacted, detections = redact_pii(text)
    assert "vikram@acme.in" not in redacted
    assert "9876543210" not in redacted
    assert {d["type"] for d in detections} == {"email", "phone"}


def test_redact_credit_card() -> None:
    text = "Card 4111111111111111 used at checkout."
    redacted, detections = redact_pii(text)
    assert "4111111111111111" not in redacted
    assert detections[0]["type"] == "credit_card"


def test_no_pii_passthrough() -> None:
    text = "The weather is pleasant today."
    redacted, detections = redact_pii(text)
    assert detections == []
    assert redacted == text


# ---------------------------------------------------------------- json schema
CHEM_SCHEMA = {
    "type": "object",
    "properties": {
        "compound": {"type": "string"},
        "formula": {"type": "string"},
        "toxicity": {"type": "number"},
    },
    "required": ["compound", "formula", "toxicity"],
}


def test_build_prompt_injects_schema() -> None:
    messages = [{"role": "user", "content": "Analyze aspirin"}]
    enriched = build_prompt(messages, CHEM_SCHEMA)
    assert enriched[0]["role"] == "system"
    assert "JSON schema" in enriched[0]["content"]
    assert "aspirin" in enriched[-1]["content"]
    assert "toxicity" in enriched[0]["content"]


def test_build_prompt_appends_to_existing_system_message() -> None:
    messages = [{"role": "system", "content": "Be helpful."}, {"role": "user", "content": "Go"}]
    enriched = build_prompt(messages, CHEM_SCHEMA)
    assert enriched[0]["role"] == "system"
    assert "Be helpful." in enriched[0]["content"]
    assert "SCHEMA:" in enriched[0]["content"]


def test_validate_output_ok_strips_fences() -> None:
    result = validate_output(
        """```json
        {"compound": "aspirin", "formula": "C9H8O4", "toxicity": 3.2}
        ```""",
        CHEM_SCHEMA,
    )
    assert result["formula"] == "C9H8O4"


def test_validate_output_rejects_bad_shape() -> None:
    with pytest.raises(SchemaValidationError) as exc:
        validate_output('{"name": "aspirin"}', CHEM_SCHEMA)
    assert exc.value.valid is False
    assert "validation" in exc.value.message


def test_validate_output_rejects_non_json() -> None:
    with pytest.raises(SchemaValidationError):
        validate_output("sorry, I cannot comply", CHEM_SCHEMA)


# ------------------------------------------------------------------------ sse
def test_sse_event_framing() -> None:
    assert sse_event({"id": "x"}) == 'data: {"id": "x"}\n\n'


def test_chat_delta_openai_chunk_shape() -> None:
    event = chat_delta("chatcmpl-x", "gpt-4o-mini", "hel lo")
    assert event.startswith("data: ")
    payload = json.loads(event[6:])
    assert payload["id"] == "chatcmpl-x"
    assert payload["object"] == "chat.completion.chunk"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["choices"][0]["delta"]["content"] == "hel lo"


def test_done_marker() -> None:
    assert done() == "data: [DONE]\n\n"


def test_sse_error_event() -> None:
    event = sse_error("boom")
    assert "boom" in event
    assert "error" in json.loads(event[6:])