import json


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def chat_delta(event_id: str, model: str, content: str) -> str:
    return sse_event(
        {
            "id": event_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
        }
    )


def done() -> str:
    return "data: [DONE]\n\n"


def sse_error(detail: str) -> str:
    return sse_event({"error": {"message": detail}})


class GatewayStreamError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)