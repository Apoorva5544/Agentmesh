from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class JSONSchemaFormat(BaseModel):
    name: str = "output"
    value: dict = Field(alias="schema")
    strict: bool = False


class ResponseFormat(BaseModel):
    type: Literal["text", "json_schema"] = "text"
    json_schema: JSONSchemaFormat | None = None


class ChatCompletionRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    provider: Literal["openai", "anthropic", "ollama", "sarvam"] | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    use_cache: bool = True
    compress_prompt: bool = True
    stream: bool = False
    response_format: ResponseFormat | None = None
    agent_id: str | None = None
    run_id: str | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    provider: str
    model: str
    content: str
    cache_hit: bool
    prompt_tokens: int
    compressed_prompt_tokens: int
    completion_tokens: int
    tokens_saved: int
    routing_reason: str
    run_id: str | None
    agent_id: str | None
    estimated_cost_inr: float
    estimated_cost_usd: float
    estimated_cost_saved_inr: float


# ------------------------------------------------------------------ virtual keys
class KeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class KeyCreateResponse(BaseModel):
    id: int
    name: str
    key: str
    key_prefix: str


# --------------------------------------------------------------------- agents
class AgentConfig(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    display_name: str = ""
    monthly_budget_inr: float = Field(default=0, ge=0.0)
    alert_thresholds: list[float] = Field(default=[0.5, 0.8, 0.95])
    hard_limit: bool = False
    fallback_model: str | None = None


class BudgetStatus(BaseModel):
    agent_id: str
    monthly_spend_inr: float
    monthly_budget_inr: float
    ratio: float
    status: Literal["ok", "warn", "blocked", "unconfigured"]
    fallback_model: str | None


# ------------------------------------------------------------------ MCP gateway
class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    endpoint: str
    description: str = ""
    auth_type: Literal["none", "bearer", "basic"] = "none"
    auth_token: str | None = None
    rate_limit_rpm: int = Field(default=60, ge=0)
    cost_per_call_inr: float = Field(default=0.0, ge=0.0)
    timeout_ms: int = Field(default=15000, ge=100, le=120000)
    enabled: bool = True


class MCPInvokeRequest(BaseModel):
    server: str
    tool: str
    arguments: dict | None = None
    run_id: str | None = None
    agent_id: str | None = None


class MCPInvokeResponse(BaseModel):
    ok: bool
    server: str
    tool: str
    content: str
    error: str | None = None
    cost_inr: float
    latency_ms: int
    retried: bool
    run_id: str | None = None


# ----------------------------------------------------------------- agent runs
class RunAction(BaseModel):
    type: Literal["llm", "mcp"]
    prompt: str | None = None
    model: str | None = None
    use_cache: bool = True
    compress_prompt: bool = True
    server: str | None = None
    tool: str | None = None
    arguments: dict | None = None


class AgentRunRequest(BaseModel):
    actions: list[RunAction] = Field(min_length=1, max_length=50)
    run_id: str | None = None
    agent_id: str = "default"


class TraceStepView(BaseModel):
    step_no: int
    step_type: str
    provider: str | None = None
    model: str | None = None
    tool: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_inr: float = 0.0
    saved_cost_inr: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False
    retried: bool = False
    error: str | None = None


class AgentRunResponse(BaseModel):
    run_id: str
    agent_id: str
    status: str = "completed"
    total_cost_inr: float = 0.0
    total_latency_ms: int = 0
    cache_hits: int = 0
    retry_count: int = 0
    steps: list[TraceStepView]