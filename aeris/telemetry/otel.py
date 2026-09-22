"""Lightweight tracing around the ATC loop.

V0 uses the OTel SDK in-process. No collector is required. When disabled,
spans are no-ops via a tracer that still exists but may not export.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode, Tracer

_CONFIGURED = False
F = TypeVar("F", bound=Callable[..., Any])


def configure_tracer(*, enabled: bool, service_name: str = "aeris") -> Tracer:
    global _CONFIGURED
    if enabled and not _CONFIGURED:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _CONFIGURED = True
    return trace.get_tracer("aeris")


def get_tracer() -> Tracer:
    return trace.get_tracer("aeris")


def traced(name: str) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        if asyncio_is_coroutine(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any):
                tracer = get_tracer()
                with tracer.start_as_current_span(name) as span:
                    _annotate(span, args, kwargs)
                    try:
                        result = await fn(*args, **kwargs)
                        span.set_status(Status(StatusCode.OK))
                        return result
                    except Exception as exc:
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        span.record_exception(exc)
                        raise

            return async_wrapper  # type: ignore[return-value]

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any):
            tracer = get_tracer()
            with tracer.start_as_current_span(name) as span:
                _annotate(span, args, kwargs)
                try:
                    result = fn(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    raise

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def asyncio_is_coroutine(fn: Callable[..., Any]) -> bool:
    import asyncio
    import inspect

    return inspect.iscoroutinefunction(fn) or asyncio.iscoroutinefunction(fn)


def _annotate(span, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    for key in ("flight_id", "mission_id", "route_id"):
        if key in kwargs and kwargs[key] is not None:
            span.set_attribute(key, str(kwargs[key]))
    for arg in args:
        flight_id = getattr(arg, "flight_id", None)
        mission_id = getattr(arg, "mission_id", None)
        route_id = getattr(arg, "current_route_id", None) or getattr(arg, "route_id", None)
        if flight_id:
            span.set_attribute("flight_id", str(flight_id))
        if mission_id:
            span.set_attribute("mission_id", str(mission_id))
        if route_id:
            span.set_attribute("route_id", str(route_id))
