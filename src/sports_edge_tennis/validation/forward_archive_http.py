"""Explicit opt-in transport for the existing original-evidence dispatcher.

HTTP is restricted to literal loopback; remote requests require verified TLS.
No environment discovery, redirects, automatic retries or production wiring.
Transport success does not establish independent archival control.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import math
import ssl
from urllib.parse import urlsplit

from sports_edge_tennis.validation.forward_original_collector import (
    LIMIT, _unique, _reject_constant,
)

RESPONSE_LIMIT = 16384
PATH = "/v1/original-receipts"


class ArchiveTransportError(RuntimeError):
    """Delivery unconfirmed; the existing outbox retains uncertainty."""


class ArchiveHTTPTransport:
    def __init__(self, endpoint: str, token: str, *, timeout: float = 10):
        url = urlsplit(endpoint)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path != PATH):
            raise ValueError("explicit archive receipt endpoint required")
        if url.scheme == "http" and url.hostname not in ("127.0.0.1", "::1"):
            raise ValueError("cleartext archive transport restricted to loopback")
        if (not isinstance(token, str) or len(token) < 32 or not token.isascii()
                or any(not 33 <= ord(c) <= 126 for c in token)):
            raise ValueError("invalid archive credential")
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("invalid archive timeout")
        self._scheme, self._host, self._port = url.scheme, url.hostname, url.port
        self._token, self._timeout = token, timeout

    def __call__(self, payload: bytes, sha256: str) -> dict:
        if (not isinstance(payload, bytes) or not 0 < len(payload) <= LIMIT
                or not isinstance(sha256, str)
                or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), sha256)):
            raise ValueError("invalid original payload or digest")
        if self._scheme == "https":
            conn = http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            conn.request("POST", PATH, body=payload, headers={
                "Authorization": "Bearer " + self._token,
                "X-Payload-SHA256": sha256,
                "Content-Type": "application/json",
                "Connection": "close",
            })
            reply = conn.getresponse()
            if reply.status != 200:
                # Never follow redirects or log response bodies/credentials.
                raise ArchiveTransportError("archive HTTP status " + str(reply.status))
            if reply.headers.get_content_type() != "application/json":
                raise ArchiveTransportError("archive response is not JSON")
            lengths = reply.headers.get_all("Content-Length") or []
            if len(lengths) != 1 or reply.headers.get("Transfer-Encoding"):
                raise ArchiveTransportError("ambiguous archive response length")
            length = int(lengths[0])
            if not 0 < length <= RESPONSE_LIMIT:
                raise ArchiveTransportError("invalid archive response size")
            raw = reply.read(length)
            if len(raw) != length:
                raise ArchiveTransportError("truncated archive response")
            receipt = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant)
            if not isinstance(receipt, dict):
                raise ArchiveTransportError("invalid receipt response")
            return receipt
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise ArchiveTransportError("archive transport or decoding failed") from exc
        finally:
            conn.close()
