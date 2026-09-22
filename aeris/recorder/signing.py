"""Optional authenticity for recorder checkpoints.

The hash chain is tamper-evident but anyone can recompute it. A signer
turns a checkpoint (flight, event count, last hash) into something only a
key holder can produce. Keys are never generated into the repository;
``Ed25519Signer.generate`` is for tests and local demos only.

Signing is off by default (``NoOpSigner``). Verification then reports
SIGNATURE_NOT_CONFIGURED instead of pretending to be valid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Protocol, runtime_checkable


@runtime_checkable
class Signer(Protocol):
    key_id: str
    algorithm: str
    configured: bool

    def sign(self, data: bytes) -> str: ...

    def verify(self, data: bytes, signature: str) -> bool: ...


class NoOpSigner:
    key_id = "none"
    algorithm = "none"
    configured = False

    def sign(self, data: bytes) -> str:
        return ""

    def verify(self, data: bytes, signature: str) -> bool:
        return False


class HmacSha256Signer:
    """Shared-secret MAC. Authenticates against outsiders, not against the key holder."""

    algorithm = "hmac-sha256"
    configured = True

    def __init__(self, secret: bytes, key_id: str = "hmac") -> None:
        if not secret:
            raise ValueError("HMAC secret must not be empty")
        self._secret = secret
        self.key_id = key_id

    def sign(self, data: bytes) -> str:
        return hmac.new(self._secret, data, hashlib.sha256).hexdigest()

    def verify(self, data: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(data), signature or "")


class Ed25519Signer:
    """Asymmetric signatures via the optional ``cryptography`` package.

    Construct with a private key to sign, or with only a public key to verify.
    """

    algorithm = "ed25519"
    configured = True

    def __init__(self, *, private_key=None, public_key=None, key_id: str = "ed25519") -> None:
        if private_key is None and public_key is None:
            raise ValueError("Ed25519Signer needs a private or a public key")
        self._private = private_key
        self._public = public_key or private_key.public_key()
        self.key_id = key_id

    @classmethod
    def generate(cls, key_id: str = "ed25519-ephemeral") -> Ed25519Signer:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return cls(private_key=Ed25519PrivateKey.generate(), key_id=key_id)

    def verifier(self) -> Ed25519Signer:
        return Ed25519Signer(public_key=self._public, key_id=self.key_id)

    def sign(self, data: bytes) -> str:
        if self._private is None:
            raise RuntimeError("verification-only Ed25519Signer cannot sign")
        return base64.b64encode(self._private.sign(data)).decode()

    def verify(self, data: bytes, signature: str) -> bool:
        from cryptography.exceptions import InvalidSignature

        try:
            self._public.verify(base64.b64decode(signature or ""), data)
        except (InvalidSignature, ValueError):
            return False
        return True
