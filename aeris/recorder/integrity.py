"""Tamper-evident hash chain, checkpoints, and optional signatures.

Guarantees:
- Given an unmodified timeline, verification succeeds.
- Changing a stored payload, timestamp, or previous_hash without
  recomputing every later hash makes verification fail.
- Deleting or reordering an event in the middle breaks the chain.
- With a checkpoint, deleting events from the tail is detected, as long
  as the checkpoint itself survives (it is stored apart from events).
- With a configured signer, a rewritten chain plus a forged checkpoint is
  detected unless the attacker also holds the key.

Non-guarantees:
- Without a signer, anyone who can rewrite events *and* checkpoints can
  forge a consistent history.
- Events appended after the last checkpoint are not anchored.
- Deleting the checkpoint rows removes truncation detection; the report
  then says NO_CHECKPOINT rather than TAIL_INTACT.
- This is not distributed consensus and not a blockchain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from aeris.core.enums import EventType

GENESIS_HASH = "0" * 64


def canonical_event(
    *,
    flight_id: str,
    event_type: EventType | str,
    timestamp: datetime,
    payload: dict[str, Any],
    mission_id: str | None,
    route_id: str | None,
    waypoint_id: str | None,
) -> str:
    body = {
        "event_type": EventType(event_type).value,
        "flight_id": flight_id,
        "mission_id": mission_id,
        "payload": payload,
        "route_id": route_id,
        "timestamp": timestamp.isoformat(),
        "waypoint_id": waypoint_id,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def chain_hash(previous_hash: str, canonical: str) -> str:
    material = f"{canonical}{previous_hash}".encode()
    return hashlib.sha256(material).hexdigest()


class IntegrityReport(BaseModel):
    ok: bool
    checked: int
    broken_seq: int | None = None
    reason: str | None = None


def verify_events(events: list[Any]) -> IntegrityReport:
    """Verify a per-flight timeline. ``events`` must already be ordered."""

    previous = GENESIS_HASH
    for event in events:
        if event.previous_hash != previous:
            return IntegrityReport(
                ok=False,
                checked=event.seq,
                broken_seq=event.seq,
                reason="previous_hash does not match the prior event",
            )
        canonical = canonical_event(
            flight_id=event.flight_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            payload=event.payload,
            mission_id=event.mission_id,
            route_id=event.route_id,
            waypoint_id=event.waypoint_id,
        )
        expected = chain_hash(previous, canonical)
        if event.event_hash != expected:
            return IntegrityReport(
                ok=False,
                checked=event.seq,
                broken_seq=event.seq,
                reason="event_hash does not match canonical payload",
            )
        previous = event.event_hash
    return IntegrityReport(ok=True, checked=len(events))


class ChainStatus(StrEnum):
    CHAIN_VALID = "CHAIN_VALID"
    CHAIN_BROKEN = "CHAIN_BROKEN"


class SignatureStatus(StrEnum):
    SIGNATURE_VALID = "SIGNATURE_VALID"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    SIGNATURE_NOT_CONFIGURED = "SIGNATURE_NOT_CONFIGURED"


class TailStatus(StrEnum):
    TAIL_INTACT = "TAIL_INTACT"
    TAIL_TRUNCATED = "TAIL_TRUNCATED"
    CHECKPOINT_MISMATCH = "CHECKPOINT_MISMATCH"
    NO_CHECKPOINT = "NO_CHECKPOINT"


class Checkpoint(BaseModel):
    """Anchor for the tail of one flight's chain. Stored apart from events."""

    flight_id: str
    last_sequence: int
    last_hash: str
    timestamp: datetime
    key_id: str = "none"
    algorithm: str = "none"
    signature: str = ""

    def material(self) -> bytes:
        return f"{self.flight_id}|{self.last_sequence}|{self.last_hash}|{self.timestamp.isoformat()}".encode()


def make_checkpoint(flight_id: str, events: list[Any], timestamp: datetime, signer=None) -> Checkpoint:
    last_hash = events[-1].event_hash if events else GENESIS_HASH
    checkpoint = Checkpoint(
        flight_id=flight_id,
        last_sequence=len(events),
        last_hash=last_hash,
        timestamp=timestamp,
    )
    if signer is not None and getattr(signer, "configured", False):
        checkpoint.key_id = signer.key_id
        checkpoint.algorithm = signer.algorithm
        checkpoint.signature = signer.sign(checkpoint.material())
    return checkpoint


class VerificationReport(BaseModel):
    chain: ChainStatus
    signature: SignatureStatus
    tail: TailStatus
    checked_events: int
    checkpoints: int
    unanchored_events: int = 0
    broken_seq: int | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.chain is ChainStatus.CHAIN_VALID
            and self.signature is not SignatureStatus.SIGNATURE_INVALID
            and self.tail in {TailStatus.TAIL_INTACT, TailStatus.NO_CHECKPOINT}
        )


def verify_flight(events: list[Any], checkpoints: list[Checkpoint], signer=None) -> VerificationReport:
    chain = verify_events(events)
    chain_status = ChainStatus.CHAIN_VALID if chain.ok else ChainStatus.CHAIN_BROKEN
    signature = SignatureStatus.SIGNATURE_NOT_CONFIGURED
    signed = [checkpoint for checkpoint in checkpoints if checkpoint.signature]
    if signed and signer is not None and getattr(signer, "configured", False):
        valid = all(signer.verify(checkpoint.material(), checkpoint.signature) for checkpoint in signed)
        signature = SignatureStatus.SIGNATURE_VALID if valid else SignatureStatus.SIGNATURE_INVALID
    elif signed:
        signature = SignatureStatus.SIGNATURE_NOT_CONFIGURED

    tail = TailStatus.NO_CHECKPOINT
    reason = chain.reason
    unanchored = len(events)
    if checkpoints:
        latest = max(checkpoints, key=lambda checkpoint: checkpoint.last_sequence)
        if latest.last_sequence > len(events):
            tail = TailStatus.TAIL_TRUNCATED
            reason = f"checkpoint covers {latest.last_sequence} events, only {len(events)} remain"
        elif latest.last_sequence > 0 and events[latest.last_sequence - 1].event_hash != latest.last_hash:
            tail = TailStatus.CHECKPOINT_MISMATCH
            reason = "event at the checkpoint position does not carry the checkpointed hash"
        else:
            tail = TailStatus.TAIL_INTACT
            unanchored = len(events) - latest.last_sequence
    return VerificationReport(
        chain=chain_status,
        signature=signature,
        tail=tail,
        checked_events=len(events),
        checkpoints=len(checkpoints),
        unanchored_events=unanchored,
        broken_seq=chain.broken_seq,
        reason=reason,
    )
