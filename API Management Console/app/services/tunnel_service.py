"""
Cloudflare tunnel lifecycle service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import queue
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any


logger = logging.getLogger(__name__)


class TunnelService:
    """Starts and tracks public tunnel providers for local server exposure.

    Supported providers:
    - cloudflare (quick tunnel or named tunnel)
    - localtunnel
    - ngrok (including reserved/static domain mode)
    """

    HOSTNAME_REGEX = re.compile(r'userHostname="(https://[-a-z0-9]+\.trycloudflare\.com)"')
    URL_REGEX = re.compile(r"(https://[a-zA-Z0-9.-]+)")

    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.metrics_port: Optional[int] = None
        self.public_base_url: Optional[str] = None
        self.last_error: Optional[str] = None
        self.source: str = "none"
        self.provider: str = "cloudflare"
        self._output_queue: "queue.Queue[str]" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None

    def _find_cloudflared(self) -> Optional[str]:
        env_path = os.getenv("CLOUDFLARED_PATH", "").strip()
        if env_path and os.path.exists(env_path):
            return env_path

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        local_bin = os.path.join(project_root, "tools", "cloudflared.exe")
        if os.path.exists(local_bin):
            return local_bin

        return shutil.which("cloudflared")

    def _find_ngrok(self) -> Optional[str]:
        env_path = os.getenv("NGROK_PATH", "").strip()
        if env_path and os.path.exists(env_path):
            return env_path
        return shutil.which("ngrok")

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _read_metrics(self) -> Optional[str]:
        if not self.metrics_port:
            return None
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.metrics_port}/metrics",
                timeout=1.5,
            ) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception:
            return None

    async def ensure_started(self, origin_url: str = "http://localhost:8000") -> bool:
        """
        Ensure a public base URL is available.
        Priority:
        1) Named Cloudflare tunnel (CLOUDFLARE_TUNNEL_TOKEN + CLOUDFLARE_PUBLIC_BASE_URL)
        2) PUBLIC_BASE_URL static override
        3) Quick tunnel (random trycloudflare URL)
        """
        enabled = os.getenv("AUTO_CLOUDFLARE_TUNNEL", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        if not enabled:
            self.public_base_url = None
            self.last_error = "AUTO_CLOUDFLARE_TUNNEL disabled"
            self.source = "disabled"
            return False

        if self.process and self.process.poll() is None and self.public_base_url:
            self.last_error = None
            return True

        self.provider = os.getenv("TUNNEL_PROVIDER", "cloudflare").strip().lower() or "cloudflare"
        if self.provider == "localtunnel":
            return await self._ensure_localtunnel_started(origin_url)
        if self.provider == "ngrok":
            return await self._ensure_ngrok_started(origin_url)

        named_tunnel_token = os.getenv("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
        named_public_base = (
            os.getenv("CLOUDFLARE_PUBLIC_BASE_URL", "").strip()
            or os.getenv("PUBLIC_BASE_URL", "").strip()
        )

        if named_tunnel_token:
            if not named_public_base:
                self.public_base_url = None
                self.last_error = (
                    "CLOUDFLARE_TUNNEL_TOKEN is set but CLOUDFLARE_PUBLIC_BASE_URL is missing"
                )
                self.source = "error"
                return False

            executable = self._find_cloudflared()
            if not executable:
                self.public_base_url = None
                self.last_error = "cloudflared binary not found"
                self.source = "unavailable"
                return False

            self.metrics_port = self._pick_free_port()
            args = [
                executable,
                "tunnel",
                "run",
                "--token",
                named_tunnel_token,
                "--no-autoupdate",
                "--metrics",
                f"127.0.0.1:{self.metrics_port}",
            ]

            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

            try:
                self.process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception as exc:
                self.process = None
                self.public_base_url = None
                self.last_error = f"failed to start named tunnel: {exc}"
                self.source = "error"
                return False

            for _ in range(40):
                if self.process.poll() is not None:
                    self.public_base_url = None
                    self.last_error = "named tunnel process exited during startup"
                    self.source = "error"
                    return False
                await asyncio.sleep(0.25)

            self.public_base_url = named_public_base.rstrip("/")
            self.last_error = None
            self.source = "cloudflared_named"
            return True

        manual_url = os.getenv("PUBLIC_BASE_URL", "").strip()
        if manual_url:
            self.public_base_url = manual_url.rstrip("/")
            self.last_error = None
            self.source = "env"
            return True

        executable = self._find_cloudflared()
        if not executable:
            self.public_base_url = None
            self.last_error = "cloudflared binary not found"
            self.source = "unavailable"
            return False

        self.metrics_port = self._pick_free_port()
        args = [
            executable,
            "tunnel",
            "--url",
            origin_url,
            "--no-autoupdate",
            "--metrics",
            f"127.0.0.1:{self.metrics_port}",
        ]

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.process = None
            self.public_base_url = None
            self.last_error = f"failed to start cloudflared: {exc}"
            self.source = "error"
            return False

        for _ in range(60):
            if self.process.poll() is not None:
                break
            metrics = self._read_metrics()
            if metrics:
                match = self.HOSTNAME_REGEX.search(metrics)
                if match:
                    self.public_base_url = match.group(1).rstrip("/")
                    self.last_error = None
                    self.source = "cloudflared"
                    return True
            await asyncio.sleep(0.5)

        self.public_base_url = None
        self.last_error = "cloudflared started but did not expose a public URL in time"
        self.source = "error"
        return False

    def _start_reader(self, proc: subprocess.Popen) -> None:
        self._output_queue = queue.Queue()

        def _reader():
            if not proc.stdout:
                return
            try:
                for line in proc.stdout:
                    self._output_queue.put(line.strip())
            except Exception:
                return

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    @staticmethod
    def _parse_origin(origin_url: str) -> tuple[str, int]:
        parsed = urllib.parse.urlparse(origin_url)
        host = parsed.hostname or "127.0.0.1"
        if parsed.port:
            port = parsed.port
        else:
            port = 443 if parsed.scheme == "https" else 80
        return host, int(port)

    async def _ensure_localtunnel_started(self, origin_url: str) -> bool:
        executable = shutil.which("npx")
        if not executable:
            self.public_base_url = None
            self.last_error = "npx not found (Node.js is required for localtunnel)"
            self.source = "unavailable"
            return False

        host, port = self._parse_origin(origin_url)
        localtunnel_host = os.getenv("LOCALTUNNEL_HOST", "https://localtunnel.me").strip()
        subdomain = os.getenv("LOCALTUNNEL_SUBDOMAIN", "").strip()

        args = [
            executable,
            "--yes",
            "localtunnel",
            "--port",
            str(port),
            "--local-host",
            host,
            "--host",
            localtunnel_host,
        ]
        if subdomain:
            args.extend(["--subdomain", subdomain])

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
            self._start_reader(self.process)
        except Exception as exc:
            self.process = None
            self.public_base_url = None
            self.last_error = f"failed to start localtunnel: {exc}"
            self.source = "error"
            return False

        for _ in range(120):
            if self.process.poll() is not None:
                break
            try:
                line = self._output_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.5)
                continue

            match = self.URL_REGEX.search(line or "")
            if match:
                self.public_base_url = match.group(1).rstrip("/")
                self.last_error = None
                self.source = "localtunnel_subdomain" if subdomain else "localtunnel"
                return True

            await asyncio.sleep(0.2)

        self.public_base_url = None
        self.last_error = "localtunnel started but did not expose a public URL in time"
        self.source = "error"
        return False

    async def _ensure_ngrok_started(self, origin_url: str) -> bool:
        executable = self._find_ngrok()
        if not executable:
            self.public_base_url = None
            self.last_error = "ngrok binary not found"
            self.source = "unavailable"
            return False

        host, port = self._parse_origin(origin_url)
        static_domain = (
            os.getenv("NGROK_STATIC_DOMAIN", "").strip()
            or os.getenv("NGROK_DOMAIN", "").strip()
        )
        authtoken = os.getenv("NGROK_AUTHTOKEN", "").strip()
        api_url = os.getenv("NGROK_API_URL", "http://127.0.0.1:4040/api/tunnels").strip()

        if authtoken:
            try:
                subprocess.run(
                    [executable, "config", "add-authtoken", authtoken],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                # Continue; user may already have token configured globally.
                pass

        args = [executable, "http", str(port), "--host-header", f"{host}:{port}"]
        if static_domain:
            args.extend(["--domain", static_domain])

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.process = None
            self.public_base_url = None
            self.last_error = f"failed to start ngrok: {exc}"
            self.source = "error"
            return False

        for _ in range(80):
            if self.process.poll() is not None:
                self.public_base_url = None
                self.last_error = "ngrok process exited during startup"
                self.source = "error"
                return False
            try:
                with urllib.request.urlopen(api_url, timeout=1.5) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="ignore"))
                tunnels = payload.get("tunnels", []) if isinstance(payload, dict) else []
                public_urls = []
                for tunnel in tunnels:
                    if isinstance(tunnel, dict):
                        url = str(tunnel.get("public_url", "")).strip()
                        if url.startswith("https://"):
                            public_urls.append(url.rstrip("/"))
                if public_urls:
                    selected = public_urls[0]
                    if static_domain:
                        normalized_domain = static_domain
                        if normalized_domain.startswith("http://") or normalized_domain.startswith("https://"):
                            parsed_domain = urllib.parse.urlparse(normalized_domain).hostname or ""
                            normalized_domain = parsed_domain
                        normalized_domain = normalized_domain.strip().strip("/")
                        static_url = f"https://{normalized_domain}".rstrip("/")
                        if any(normalized_domain in u for u in public_urls):
                            selected = next((u for u in public_urls if normalized_domain in u), static_url)
                        else:
                            selected = static_url
                    self.public_base_url = selected
                    self.last_error = None
                    self.source = "ngrok_static_domain" if static_domain else "ngrok"
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)

        self.public_base_url = None
        self.last_error = "ngrok started but did not expose a public URL in time"
        self.source = "error"
        return False

    async def shutdown(self) -> None:
        """Stop managed cloudflared process."""
        if not self.process:
            return
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        except Exception as exc:
            logger.warning("Failed to stop cloudflared process cleanly: %s", exc)
        finally:
            self.process = None
            self.metrics_port = None
            if self.source in {
                "cloudflared",
                "localtunnel",
                "localtunnel_subdomain",
                "cloudflared_named",
                "ngrok",
                "ngrok_static_domain",
            }:
                self.public_base_url = None
            self._reader_thread = None

    def get_status(self) -> Dict[str, Any]:
        running = bool(self.process and self.process.poll() is None)
        return {
            "enabled": self.source not in {"disabled", "unavailable"},
            "running": running or self.source == "env",
            "public_base_url": self.public_base_url,
            "source": self.source,
            "provider": self.provider,
            "stable": self.source in {"cloudflared_named", "env", "localtunnel_subdomain", "ngrok_static_domain"},
            "last_error": self.last_error,
            "metrics_port": self.metrics_port,
        }

    @staticmethod
    def _with_optional_token(url: str) -> str:
        token = os.getenv("TUNNEL_SHARED_SECRET", "").strip()
        if not token:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}token={urllib.parse.quote(token, safe='')}"

    def get_chat_endpoint(self, bot_id: str) -> Optional[str]:
        if not self.public_base_url:
            return None
        endpoint = f"{self.public_base_url}/api/bot/{bot_id}/chat"
        return self._with_optional_token(endpoint)

    def get_embed_endpoint(self, bot_id: str) -> Optional[str]:
        if not self.public_base_url:
            return None
        endpoint = f"{self.public_base_url}/embed/{bot_id}"
        return self._with_optional_token(endpoint)


tunnel_service = TunnelService()
