"""OpenTelemetry helpers. Tracing is optional and off by default."""

from aeris.telemetry.otel import configure_tracer, get_tracer, traced

__all__ = ["configure_tracer", "get_tracer", "traced"]
