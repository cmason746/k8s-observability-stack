"""RED metrics and log clean-up for a FastAPI app.

The same file is used by orders and inventory. Call setup(app) once.

Metrics, served on GET /metrics in the Prometheus text format:
  http_requests_total{method, route, status}          Counter: Rate and Errors
  http_request_duration_seconds{method, route}        Histogram: Duration

The route label is the route template (for example /orders), never the raw URL,
so the number of label values stays small and fixed. Requests that match no
route are labelled "unmatched" for the same reason.
"""

import logging
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Requests from Kubernetes and Prometheus, not users: no metrics, no access log lines
UNTRACKED = {"/health", "/ready", "/metrics"}

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by route and status code",
    ["method", "route", "status"],
)
DURATION = Histogram(
    "http_request_duration_seconds",
    "Time taken to handle HTTP requests",
    ["method", "route"],
    # Bucket upper bounds in seconds, sized for a fast internal API
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)


class SkipUntrackedLogs(logging.Filter):
    """Drop uvicorn access log lines for probes and scrapes."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(f'"GET {path} ' in message for path in UNTRACKED)


def setup(app: FastAPI) -> None:
    logging.getLogger("uvicorn.access").addFilter(SkipUntrackedLogs())

    @app.middleware("http")
    async def record_metrics(request: Request, call_next):
        start = time.perf_counter()
        status = 500  # Counted as a 500 if the handler crashes
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            # FastAPI records the matched route in the request once it's been routed
            route = request.scope.get("route")
            path = route.path if route else "unmatched"
            if path not in UNTRACKED:
                REQUESTS.labels(request.method, path, str(status)).inc()
                DURATION.labels(request.method, path).observe(time.perf_counter() - start)

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
