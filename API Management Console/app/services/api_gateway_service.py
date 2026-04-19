"""
API-only gateway service for secure tunnel exposure.

This service runs a lightweight local HTTP proxy that only exposes:
- POST /api/bot/{bot_id}/chat
- GET  /api/bot/{bot_id}/status
- GET  /embed/{bot_id}
- GET  /health

Everything else is blocked with 404/405. The tunnel points to this gateway
instead of the main app, reducing attack surface.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


logger = logging.getLogger(__name__)


class _GatewayHTTPServer(ThreadingHTTPServer):
    """HTTP server with attached gateway state."""

    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, gateway_service):
        self.gateway_service = gateway_service
        super().__init__(server_address, RequestHandlerClass)


class ApiGatewayService:
    """Lifecycle manager for API-only local gateway."""

    CHAT_PATH_RE = re.compile(r"^/api/bot/[0-9a-fA-F-]{36}/chat$")
    CHAT_STREAM_PATH_RE = re.compile(r"^/api/bot/[0-9a-fA-F-]{36}/chat/stream$")
    TOOL_TRIGGER_PATH_RE = re.compile(r"^/api/bot/[0-9a-fA-F-]{36}/tools/trigger$")
    STATUS_PATH_RE = re.compile(r"^/api/bot/[0-9a-fA-F-]{36}/status$")
    EMBED_PATH_RE = re.compile(r"^/embed/[0-9a-fA-F-]{36}$")
    MAX_BODY_BYTES = 64 * 1024

    def __init__(self) -> None:
        self._server: Optional[_GatewayHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None
        self.target_base_url: str = "http://127.0.0.1:8000"
        self._breaker = CircuitBreaker(name="api_gateway_upstream")

    def _build_handler(self):
        class GatewayHandler(BaseHTTPRequestHandler):
            server: _GatewayHTTPServer

            def _set_common_headers(self, status_code: int, content_type: str = "application/json") -> None:
                self.send_response(status_code)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization, X-Tunnel-Token, bypass-tunnel-reminder",
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")

            def _json(self, status_code: int, payload: bytes) -> None:
                self._set_common_headers(status_code, "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _forbidden(self, message: str) -> None:
                self._json(403, f'{{"detail":"{message}"}}'.encode("utf-8"))

            def _not_found(self) -> None:
                self._json(404, b'{"detail":"Not found"}')

            def _method_not_allowed(self) -> None:
                self._json(405, b'{"detail":"Method not allowed"}')

            def _too_large(self) -> None:
                self._json(413, b'{"detail":"Request body too large"}')

            def _is_allowed_path(self) -> bool:
                path = urllib.parse.urlparse(self.path).path
                if path in {"/health", "/health/live", "/health/ready", "/health/fresh"}:
                    return True
                return bool(
                    self.server.gateway_service.CHAT_PATH_RE.match(path)
                    or self.server.gateway_service.CHAT_STREAM_PATH_RE.match(path)
                    or self.server.gateway_service.TOOL_TRIGGER_PATH_RE.match(path)
                    or self.server.gateway_service.STATUS_PATH_RE.match(path)
                    or self.server.gateway_service.EMBED_PATH_RE.match(path)
                )

            def _is_allowed_method(self) -> bool:
                path = urllib.parse.urlparse(self.path).path
                if path in {"/health", "/health/live", "/health/ready", "/health/fresh"}:
                    return self.command == "GET"
                if (
                    self.server.gateway_service.CHAT_PATH_RE.match(path)
                    or self.server.gateway_service.CHAT_STREAM_PATH_RE.match(path)
                    or self.server.gateway_service.TOOL_TRIGGER_PATH_RE.match(path)
                ):
                    return self.command in {"POST", "OPTIONS"}
                if self.server.gateway_service.STATUS_PATH_RE.match(path):
                    return self.command in {"GET", "OPTIONS"}
                if self.server.gateway_service.EMBED_PATH_RE.match(path):
                    return self.command in {"GET", "OPTIONS"}
                return False

            def _check_optional_token(self) -> bool:
                required = os.getenv("TUNNEL_SHARED_SECRET", "").strip()
                if not required:
                    return False

                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query or "")
                query_token = (params.get("token") or [None])[0]
                tunnel_token = self.headers.get("X-Tunnel-Token")
                auth_header = self.headers.get("Authorization")
                bearer_token = None
                if auth_header is not None:
                    auth_value = auth_header.strip()
                    scheme, _, credentials = auth_value.partition(" ")
                    if scheme.lower() == "bearer" and credentials.strip():
                        bearer_token = credentials.strip()
                candidate = bearer_token or tunnel_token or query_token or ""
                return hmac.compare_digest(candidate, required)

            def _sanitize_forward_path(self) -> str:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qsl(parsed.query or "", keep_blank_values=True)
                filtered = [(key, value) for key, value in params if key.lower() != "token"]
                sanitized_query = urllib.parse.urlencode(filtered)
                return urllib.parse.urlunparse(
                    (parsed.scheme, parsed.netloc, parsed.path, parsed.params, sanitized_query, parsed.fragment)
                )

            def _forward(self, body: Optional[bytes]) -> None:
                target = f"{self.server.gateway_service.target_base_url.rstrip('/')}{self._sanitize_forward_path()}"
                request = urllib.request.Request(url=target, data=body, method=self.command)

                # Forward only safe/needed headers.
                pass_headers = ("Content-Type", "Accept", "User-Agent", "Origin", "Authorization")
                for key in pass_headers:
                    value = self.headers.get(key)
                    if value:
                        request.add_header(key, value)

                tunnel_token = self.headers.get("X-Tunnel-Token")
                if not tunnel_token:
                    parsed = urllib.parse.urlparse(self.path)
                    params = urllib.parse.parse_qs(parsed.query or "")
                    tunnel_token = (params.get("token") or [None])[0]
                if tunnel_token:
                    request.add_header("X-Tunnel-Token", tunnel_token)

                client_ip = self.client_address[0] if self.client_address else "unknown"
                request.add_header("X-Forwarded-For", client_ip)
                request.add_header("X-Forwarded-Proto", "https")

                try:
                    self.server.gateway_service._breaker.before_request()
                    with urllib.request.urlopen(request, timeout=60) as response:
                        data = response.read()
                        self.server.gateway_service._breaker.record_success()
                        self._set_common_headers(response.getcode(), response.headers.get("Content-Type", "application/json"))
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                except urllib.error.HTTPError as exc:
                    data = exc.read()
                    if exc.code >= 500:
                        self.server.gateway_service._breaker.record_failure(f"upstream_http_{exc.code}")
                    else:
                        self.server.gateway_service._breaker.record_success()
                    self._set_common_headers(exc.code, exc.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except CircuitBreakerOpenError as exc:
                    payload = (
                        f'{{"detail":"Gateway circuit open","retry_after_seconds":{exc.retry_after:.2f}}}'
                    ).encode("utf-8")
                    self._json(503, payload)
                except Exception as exc:
                    self.server.gateway_service._breaker.record_failure(exc)
                    self._json(502, b'{"detail":"Gateway upstream error"}')

            def do_OPTIONS(self):
                if not self._is_allowed_path():
                    self._not_found()
                    return
                if not self._is_allowed_method():
                    self._method_not_allowed()
                    return
                # Do not enforce tunnel token on preflight requests.
                # Browsers require a 2xx preflight response before sending the actual
                # request where token enforcement is applied (POST/GET).
                self._set_common_headers(204, "text/plain")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                if not self._is_allowed_path():
                    self._not_found()
                    return
                if not self._is_allowed_method():
                    self._method_not_allowed()
                    return
                if not self._check_optional_token():
                    self._forbidden("Forbidden")
                    return
                self._forward(None)

            def do_POST(self):
                if not self._is_allowed_path():
                    self._not_found()
                    return
                if not self._is_allowed_method():
                    self._method_not_allowed()
                    return
                if not self._check_optional_token():
                    self._forbidden("Forbidden")
                    return

                raw_length = self.headers.get("Content-Length", "0")
                try:
                    content_length = int(raw_length)
                except Exception:
                    content_length = 0
                if content_length > self.server.gateway_service.MAX_BODY_BYTES:
                    self._too_large()
                    return

                body = self.rfile.read(content_length) if content_length > 0 else None
                self._forward(body)

            def log_message(self, fmt: str, *args):
                logger.debug("api-gateway: " + fmt, *args)

        return GatewayHandler

    def start(self, target_base_url: str = "http://127.0.0.1:8000") -> bool:
        """Start the gateway server on localhost random port."""
        if self._server and self._thread and self._thread.is_alive():
            return True

        self.target_base_url = target_base_url.rstrip("/")
        handler_cls = self._build_handler()
        self._server = _GatewayHTTPServer(("127.0.0.1", 0), handler_cls, self)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("API gateway started on http://127.0.0.1:%s -> %s", self.port, self.target_base_url)
        return True

    def shutdown(self) -> None:
        """Shutdown gateway server."""
        if not self._server:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None
        self.port = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self.port)

    def get_origin_url(self) -> Optional[str]:
        if not self.port:
            return None
        return f"http://127.0.0.1:{self.port}"

    def get_status(self) -> dict:
        return {
            "running": self.is_running(),
            "port": self.port,
            "target_base_url": self.target_base_url,
            "mode": "api_only_proxy",
            "circuit_breaker": self._breaker.snapshot(),
        }


api_gateway_service = ApiGatewayService()
