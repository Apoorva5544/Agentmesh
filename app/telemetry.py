import contextlib
from typing import Iterator

from app.config import Settings

try:  # pragma: no cover - optional dependency
    from opentelemetry import trace  # type: ignore
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor  # type: ignore
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover
    _OTEL_AVAILABLE = False


def setup_telemetry(app, settings: Settings) -> None:
    """Wire OpenTelemetry into the ASGI app, asyncpg, and Redis.

    Safe no-op when the optional opentelemetry packages are not installed.
    """
    if not _OTEL_AVAILABLE or not settings.otel_enabled:
        return
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    AsyncPGInstrumentor().instrument()


def is_otel_enabled() -> bool:
    return _OTEL_AVAILABLE


@contextlib.contextmanager
def span(name: str, attributes: dict | None = None) -> Iterator[None]:
    """Tracing span wrapper; a no-op context manager when OTel is unavailable."""
    if not _OTEL_AVAILABLE:
        yield
        return
    tracer = trace.get_tracer("agentmesh")
    with tracer.start_as_current_span(name, attributes=attributes or {}) as _active:
        yield