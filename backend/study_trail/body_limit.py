"""Bound request bodies even when a client sends chunked transfer encoding."""

import json
from uuid import uuid4

from starlette.exceptions import HTTPException


class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(status_code=413, detail="Request exceeds configured body limit")


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        maximum = (
            501 * 1024**2
            if path.endswith("/files") and scope["method"] == "POST"
            else 11 * 1024**2
            if path.endswith("/images")
            else 2 * 1024**2
        )
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        size = 0
        started = False

        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > maximum:
                    raise BodyTooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def reject():
            body = json.dumps(
                {
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "message": "请求超过容量上限，请减少文件或输入内容。",
                        "details": {},
                    },
                    "request_id": scope.get("state", {}).get("request_id") or str(uuid4()),
                },
                ensure_ascii=False,
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        if length and length.isdigit() and int(length) > maximum:
            return await reject()
        try:
            await self.app(scope, bounded_receive, tracked_send)
        except BodyTooLarge:
            if started:
                raise
            await reject()
