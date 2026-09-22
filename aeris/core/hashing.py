"""Stable content hashes for configuration identity.

A result without the hash of the policy, routes, and prompt that produced
it cannot be reproduced, so experiments stamp these on every trial.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any, *, length: int = 16) -> str:
    digest = hashlib.sha256(canonical_json(value).encode()).hexdigest()
    return digest[:length]
