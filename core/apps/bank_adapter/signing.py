"""Thin RFC 8785 / Ed25519 wrappers shared by the bank and intel clients. Human review required."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PROBLEM_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,47}$")


class RemoteError(Exception):
    """A sanitized transport/protocol error; remote bodies can contain private information.

    Only the typed RFC 9457 `code` of a remote problem is kept (pattern-checked), never its body.
    """

    def __init__(self, code: str, status: int = 503, problem_code: str | None = None) -> None:
        self.code, self.status, self.problem_code = code, status, problem_code
        super().__init__(code)


def _problem_code(error: HTTPError) -> str | None:
    try:
        code = json.loads(error.read(4096)).get("code")
    except (ValueError, AttributeError, OSError):
        return None
    return code if isinstance(code, str) and PROBLEM_CODE.match(code) else None


def canonical(value: Any) -> bytes:
    return rfc8785.dumps(value)


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Ed25519 private key required")
    return key


def load_public(path: Path) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(path.read_text().strip(), validate=True))


def sign_object(value: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    body = {k: v for k, v in value.items() if k != "sig"}
    return {**body, "sig": base64.b64encode(key.sign(canonical(body))).decode("ascii")}


def verify_object(value: dict[str, Any], key: Ed25519PublicKey, key_id: str) -> None:
    try:
        if value.get("key_id") != key_id:
            raise ValueError("untrusted key id")
        signature = base64.b64decode(value["sig"], validate=True)
        key.verify(signature, canonical({k: v for k, v in value.items() if k != "sig"}))
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        raise RemoteError("SIGNATURE_INVALID", 401) from exc


class SignedTransport:
    """Signs exact method, path/query and bytes sent; bounded blocking HTTP with fresh nonces."""

    def __init__(self, base_url: str, key: Ed25519PrivateKey, key_id: str, timeout: float = 5) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("invalid service URL")
        self.base_url, self.key, self.key_id, self.timeout = base_url.rstrip("/"), key, key_id, timeout

    def request(self, method: str, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not target.startswith("/") or target.startswith("//"):
            raise ValueError("absolute local path required")
        body = b"" if payload is None else canonical(payload)
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        message = "\n".join((method, target, timestamp, nonce, hashlib.sha256(body).hexdigest())).encode()
        headers = {
            "X-SafePay-Key-Id": self.key_id,
            "X-SafePay-Timestamp": timestamp,
            "X-SafePay-Nonce": nonce,
            "X-SafePay-Signature": base64.b64encode(self.key.sign(message)).decode("ascii"),
            "Content-Type": "application/json",
        }
        request = Request(  # noqa: S310 (base URL scheme checked in constructor)
            self.base_url + target, data=body if payload is not None else None, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 (URL scheme checked)
                data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise RemoteError("RESPONSE_TOO_LARGE")
                result: Any = json.loads(data) if data else {}
        except HTTPError as exc:
            raise RemoteError("REMOTE_REJECTED", exc.code, _problem_code(exc)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RemoteError("REMOTE_UNAVAILABLE") from exc
        except (ValueError, UnicodeError) as exc:
            raise RemoteError("RESPONSE_INVALID") from exc
        if not isinstance(result, dict):
            raise RemoteError("RESPONSE_INVALID")
        return result
