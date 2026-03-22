"""Request-context middleware for request-id propagation and timing logs."""

from __future__ import annotations

import time
import uuid

from fastapi import Request

from ..observability import get_logger, reset_request_id, set_request_id

log = get_logger("api")


async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = set_request_id(request_id)
    start = time.perf_counter()
    try:
        log.info("request.start method={} path={}", request.method, request.url.path)
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0
        response.headers["x-request-id"] = request_id
        log.info(
            "request.done method={} path={} status_code={} duration_ms={:.2f}",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
    finally:
        reset_request_id(token)
