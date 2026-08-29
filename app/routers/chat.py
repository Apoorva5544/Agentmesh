import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.dependencies import container
from app.gateway import BudgetBlocked, ChatGateway
from app.schemas import ChatCompletionRequest, ChatCompletionResponse
from app.sse import chat_delta, done, sse_error
from app.sse import GatewayStreamError

gateway = ChatGateway(container)
router = APIRouter(tags=["chat"])


async def _sse_stream(payload: ChatCompletionRequest) -> AsyncIterator[str]:
    event_id = f"chatcmpl-{uuid.uuid4().hex}"
    try:
        async for delta in gateway.stream(payload):
            yield chat_delta(event_id, payload.model or "auto", delta)
    except GatewayStreamError as exc:
        yield sse_error(str(exc))
    finally:
        yield done()


@router.post("/v1/chat/completions")
async def chat_completions(payload: ChatCompletionRequest, request: Request):
    await container.auth.require_tenant(request)

    if payload.stream:
        if payload.response_format and payload.response_format.type == "json_schema":
            raise HTTPException(
                status_code=400,
                detail="response_format.type=json_schema is not supported with stream=true; validate on the non-streaming path",
            )
        verdict = await container.budgets.evaluate(payload.agent_id or "default")
        if verdict.status == "blocked":
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "budget exhausted",
                    "agent_id": payload.agent_id or "default",
                    "monthly_spend_inr": verdict.spend_inr,
                    "monthly_budget_inr": verdict.budget_inr,
                    "status": verdict.status,
                },
            )
        return StreamingResponse(_sse_stream(payload), media_type="text/event-stream")

    try:
        return ChatCompletionResponse(**await gateway.complete(payload))
    except BudgetBlocked as exc:
        verdict = exc.verdict
        raise HTTPException(
            status_code=402,
            detail={
                "error": "budget exhausted",
                "agent_id": payload.agent_id or "default",
                "monthly_spend_inr": verdict.spend_inr,
                "monthly_budget_inr": verdict.budget_inr,
                "status": verdict.status,
            },
        )