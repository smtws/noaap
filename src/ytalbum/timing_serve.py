"""`ytalbum timing-serve` — the local provider, on the machine that has the hardware (§9.36).

Fifty lines around code that exists anyway: the same `LocalTiming` the `local` provider uses, behind
two HTTP endpoints, so the laptop can ask a different box to do the arithmetic and inherit none of
the dependencies. Nothing leaves the network, there is no authentication and none is pretended —
run it on a LAN you trust, exactly as the web UI says of itself.

    GET  /capabilities -> {"capabilities": ["align"], "device": "cuda"}
    POST /align         multipart: audio=<file>, lines=<one per line>, language=<"de"|"en"|"">
                       -> the Timed structure as JSON
"""

from __future__ import annotations

import json
import logging
import tempfile
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import load
from .timing import ALIGN, TimingUnavailable
from .timing_local import LocalTiming

log = logging.getLogger(__name__)
MAX_AUDIO = 200 * 1024 * 1024  # a long track in Opus is a few MB; this is only a sanity bound


def handler_for(engine: LocalTiming) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ytalbum-timing"

        def log_message(self, fmt: str, *args: object) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.rstrip("/") in ("/capabilities", ""):
                return self._json(200, {"capabilities": sorted(engine.capabilities()),
                                        "device": engine.resolved_device(), "provider": "local",
                                        # what this machine can do, which is the point of the command
                                        "verifies": bool(getattr(engine, "verifying", bool)())})
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = self.path.rstrip("/")
            if path not in ("/align", "/transcribe"):
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_AUDIO:
                return self._json(413, {"error": "too large"})
            parts = _multipart(self.headers.get("Content-Type", ""), self.rfile.read(length))
            text, _ = parts.get("lines", (b"", ""))
            language = parts.get("language", (b"", ""))[0].decode("utf-8", "replace").strip() or None
            lines = [line for line in text.decode("utf-8", "replace").splitlines() if line.strip()]
            audio, filename = parts.get("audio", (b"", ""))
            if not audio or (path == "/align" and not lines):
                return self._json(400, {"error": "need an audio file" + (" and some lines" if path == "/align" else "")})
            with tempfile.NamedTemporaryFile(suffix=Path(filename or "a.opus").suffix or ".opus") as tmp:
                tmp.write(audio)
                tmp.flush()
                try:
                    timed = (engine.align(Path(tmp.name), lines, language=language) if path == "/align"
                             else engine.transcribe(Path(tmp.name), language=language))
                except TimingUnavailable as e:
                    return self._json(400, {"error": str(e)})
                except Exception as e:
                    log.warning("align failed", exc_info=True)
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return self._json(200, timed.to_dict())

    return Handler


def _multipart(content_type: str, body: bytes) -> dict[str, tuple[bytes, str]]:
    """{name: (bytes, filename)} from a multipart body. `cgi` went away in 3.13; `email` did not."""
    raw = b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = BytesParser(policy=email_policy).parsebytes(raw)
    out: dict[str, tuple[bytes, str]] = {}
    for part in message.iter_parts() if message.is_multipart() else []:
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_param("filename", header="content-disposition") or ""
        payload = part.get_payload(decode=True) or b""
        out[str(name)] = (payload, str(filename))
    return out


def serve(host: str = "0.0.0.0", port: int = 8770, device: str = "auto") -> int:
    """Run until interrupted. Binds to every interface by default: that is the point of it."""
    # the serving machine's own config decides its widths of agreement: it is the machine doing the
    # checking, and the app on the other end cannot know what this one has installed
    cfg = load()
    engine = LocalTiming(device=device, log=lambda s: print(s, flush=True),
                         verify=cfg.timing_verify,
                         threshold=cfg.timing_verify_threshold, lost=cfg.timing_verify_lost)
    if ALIGN not in engine.capabilities():
        print("the timing extra is not installed here: uv pip install \"ytalbum[timing]\"")
        return 2
    server = ThreadingHTTPServer((host, port), handler_for(engine))
    print(f"ytalbum timing: http://{host}:{port}/  (device {engine.resolved_device()}) — Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
