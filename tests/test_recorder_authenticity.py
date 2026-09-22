"""Optional signing, checkpoints, and tail-truncation detection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aeris.core.enums import EventType
from aeris.recorder.integrity import ChainStatus, SignatureStatus, TailStatus, verify_flight
from aeris.recorder.memory import InMemoryRecorder
from aeris.recorder.signing import Ed25519Signer, HmacSha256Signer, NoOpSigner, Signer
from aeris.recorder.sqlite import SqliteFlightRecorder

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _fill(recorder, flight_id: str = "flt", count: int = 4) -> None:
    for index in range(count):
        await recorder.append(flight_id, EventType.TELEMETRY, {"i": index}, timestamp=NOW)


def test_signers_satisfy_the_protocol():
    assert isinstance(NoOpSigner(), Signer)
    assert isinstance(HmacSha256Signer(b"k"), Signer)
    with pytest.raises(ValueError):
        HmacSha256Signer(b"")


async def test_unsigned_checkpoint_reports_signature_not_configured():
    recorder = InMemoryRecorder()
    await _fill(recorder)
    await recorder.checkpoint("flt", timestamp=NOW)
    report = verify_flight(await recorder.timeline("flt"), await recorder.checkpoints("flt"))
    assert report.chain is ChainStatus.CHAIN_VALID
    assert report.signature is SignatureStatus.SIGNATURE_NOT_CONFIGURED
    assert report.tail is TailStatus.TAIL_INTACT and report.ok


async def test_hmac_signature_valid_then_invalid_with_the_wrong_key():
    signer = HmacSha256Signer(b"secret", key_id="k1")
    recorder = InMemoryRecorder(signer=signer)
    await _fill(recorder)
    await recorder.checkpoint("flt", timestamp=NOW)
    events, checkpoints = await recorder.timeline("flt"), await recorder.checkpoints("flt")
    assert verify_flight(events, checkpoints, signer).signature is SignatureStatus.SIGNATURE_VALID
    wrong = verify_flight(events, checkpoints, HmacSha256Signer(b"other"))
    assert wrong.signature is SignatureStatus.SIGNATURE_INVALID and not wrong.ok


async def test_forged_checkpoint_fails_signature_verification():
    signer = HmacSha256Signer(b"secret")
    recorder = InMemoryRecorder(signer=signer)
    await _fill(recorder)
    checkpoint = await recorder.checkpoint("flt", timestamp=NOW)
    forged = checkpoint.model_copy(update={"last_sequence": 2})
    report = verify_flight((await recorder.timeline("flt"))[:2], [forged], signer)
    assert report.signature is SignatureStatus.SIGNATURE_INVALID


async def test_tail_truncation_is_detected_with_a_checkpoint_and_invisible_without():
    recorder = InMemoryRecorder()
    await _fill(recorder)
    await recorder.checkpoint("flt", timestamp=NOW)
    recorder._drop_tail_for_test("flt", 2)
    events = await recorder.timeline("flt")
    assert verify_flight(events, []).chain is ChainStatus.CHAIN_VALID
    assert verify_flight(events, []).tail is TailStatus.NO_CHECKPOINT
    report = verify_flight(events, await recorder.checkpoints("flt"))
    assert report.tail is TailStatus.TAIL_TRUNCATED and not report.ok


async def test_events_after_the_last_checkpoint_are_reported_unanchored():
    recorder = InMemoryRecorder()
    await _fill(recorder, count=2)
    await recorder.checkpoint("flt", timestamp=NOW)
    await _fill(recorder, count=3)
    report = verify_flight(await recorder.timeline("flt"), await recorder.checkpoints("flt"))
    assert report.tail is TailStatus.TAIL_INTACT and report.unanchored_events == 3


async def test_sqlite_persists_checkpoints_apart_from_events(tmp_path: Path):
    signer = HmacSha256Signer(b"secret")
    recorder = SqliteFlightRecorder(tmp_path / "rec.db", signer=signer)
    await _fill(recorder)
    await recorder.checkpoint("flt", timestamp=NOW)
    reopened = SqliteFlightRecorder(tmp_path / "rec.db", signer=signer)
    report = verify_flight(await reopened.timeline("flt"), await reopened.checkpoints("flt"), signer)
    assert report.ok and report.signature is SignatureStatus.SIGNATURE_VALID


def test_ed25519_signer_when_cryptography_is_installed():
    pytest.importorskip("cryptography")
    signer = Ed25519Signer.generate()
    verifier = signer.verifier()
    signature = signer.sign(b"payload")
    assert verifier.verify(b"payload", signature)
    assert not verifier.verify(b"tampered", signature)
    with pytest.raises(RuntimeError):
        verifier.sign(b"x")
