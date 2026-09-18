"""Minimal HTTP helper built on the standard library.

Deliberately dependency-free: the MCP server is frequently launched as a stdio
subprocess inside someone else's Python environment, and every extra hard
dependency is one more way for that launch to fail. ``requests``/``httpx`` are
used automatically when present, but nothing here requires them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:400]}")
        self.status = status
        self.body = body
        self.url = url


def request_json(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    """Perform a request and decode a JSON response.

    ``data`` is form-encoded (what the 1688 open platform expects), while
    ``json_body`` sends a JSON payload (what most aggregator APIs expect).
    """
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{query}"

    body: bytes | None = None
    send_headers = {"Accept": "application/json", "User-Agent": "sourcing1688/1.0"}
    send_headers.update(headers or {})

    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        send_headers.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = urllib.parse.urlencode({k: v for k, v in data.items() if v is not None}).encode("utf-8")
        send_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(url, data=body, headers=send_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - need the body for diagnostics
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise HttpError(exc.code, detail, url) from exc
    except urllib.error.URLError as exc:
        raise HttpError(0, str(exc.reason), url) from exc

    if not payload.strip():
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HttpError(200, f"non-JSON response: {payload[:400]}", url) from exc


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of a nested payload, tolerating lists.

    Aggregator vendors bury results at wildly different depths
    ("result.items", "data.data.list", "items"), so the path is configuration.
    """
    current = payload
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
        if current is None:
            return default
    return current
