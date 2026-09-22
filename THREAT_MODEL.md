# Threat model

AERIS sits on the control path of an agent. That makes AERIS a trust
boundary. This document says what V0 trusts, what it does not, and what
it deliberately leaves open.

V0 does not solve these threats. Where a mitigation exists, it is named.
Where it does not, the V0 decision is to document the gap rather than
pretend a local prototype closes it.

## Trust boundaries

Untrusted, unless a later control proves otherwise:

- user prompt
- LLM-generated text
- LLM tool arguments
- tool responses
- external APIs and web content
- agent-generated metadata
- model confidence, progress, and success claims

More trusted, because AERIS owns the code:

- radar normalization
- hazard thresholds
- the ATC policy engine
- operator role checks
- recorder hash logic

Agent-reported confidence, progress, route advice, and hazard severity
are not ground truth. Radar marks them `RUNTIME_REPORTED` or
`TOOL_REPORTED`. Latency that AERIS advances on the clock is `MEASURED`.
Counters AERIS derives are `DERIVED`.

## Threats

| Threat | Trust boundary | Potential impact | Existing mitigation | Missing mitigation | V0 decision |
| --- | --- | --- | --- | --- | --- |
| Prompt injection | user prompt, untrusted | agent takes an action the operator did not ask for | AERIS does not execute the prompt; it only sees runtime observations | no prompt firewall | Out of scope. AERIS is not a prompt filter |
| Malicious tool output | tool response, untrusted | false progress, poisoned data, induced reroute | side-effect class is declared by the waypoint, not by the tool text; stale-data and tool-error rules are explicit | no content scanner | Record provenance. Do not add a classifier |
| Fabricated telemetry | runtime-reported fields, untrusted | false confidence hides a failure, or false low confidence forces a diversion | tool errors still raise `TOOL_FAILURE` when the runtime sets `tool_error`; `false_low_confidence` measures the diversion cost | runtime can still lie about `tool_error` | Trust the runtime's error flag more than its confidence. A compromised runtime is not solved here |
| Replayed events | recorder / adapter | an old failure looks current | per-flight hash chain orders events; clock is injected in tests | no nonce or session binding on live adapters | Accept for the simulator. Do not claim replay protection in production |
| Tampering with decisions | recorder or API caller | history no longer matches what ATC did | decisions are appended, not updated; hash check fails if the payload changes and hashes are left stale | an attacker who rewrites the chain from the edit to the tail forges a consistent log | Tamper-evident, not tamper-proof. See recorder section below |
| Unauthorized intervention | human/API caller | abort, retry, or reroute without authority | role is required on every control path and never defaults; `OBSERVER` cannot steer; `CONTROLLER` cannot abort or cancel; irreversible retry is rejected even for `ADMIN` | no OAuth, no identity behind the role string, per-mission ACL, read endpoints are open on the local API | Role enum is the V1 check. The caller still asserts its own role; replace `authorize` with real identity before exposing the API |
| Concurrent or duplicated control | two operators, retried HTTP calls | one decision applied twice, e.g. two retries of a write | per-flight lock; optimistic `control_version`; stale `expected_version` is 409 | lock is per process | Single API process per flight log |
| Baseline poisoning | calibration traces | contextual radar learns a degraded baseline and stops flagging | baselines come only from calibration seeds and are frozen with a fingerprint; validation reports false alarms | no drift detection, no protection if calibration itself is degraded | Record the baseline fingerprint with every result |
| Duplicate execution | retry of a write | email, payment, or ticket applied twice | idempotency key suppresses a second simulated commit; irreversible retry is blocked | key store is in-process only | Required for the simulator. Not a payments system |
| Recorder modification | database file | postmortem lies | append-only API; SQLite has no update method on the recorder; `verify_events` detects naive edits; optional signed checkpoints detect rewrites and tail deletion up to the last checkpoint | no external anchor; an attacker holding the signing key, or deleting checkpoints too, is not detected | Document the limit. Do not add a blockchain |
| Denial of service via escalation | hazards or a caller | every flight sticks in `WAITING_HUMAN` | critical-human policy is explicit; experiment aborts waiting flights when no operator exists | no rate limit on escalations | Accept. A forced escalation is visible on the timeline |
| Induced reroute | untrusted confidence or repeated-action signal | healthy work is diverted onto a worse route | `false_low_confidence` is measured; intervention precision counts useless diversions | reported signals can still move ATC | Keep the fixture. Do not hide a negative result |
| Compromised adapter | `AgentRuntime` | false observations, skipped compensation, ignored cancel | capabilities are declared and checked; compensation failure ends the flight | AERIS cannot detect a lying adapter | The adapter is inside the trust boundary of whoever deploys it |
| Compromised runtime | agent process | same as a compromised adapter, plus real side effects | side-effect policy refuses blind retry of irreversible writes | no attestation of the runtime | Out of scope for V0 beyond the policy gate |

## Recorder chain

```
event[n].hash = SHA256(canonical_json(event[n]) + event[n].previous_hash)
```

`previous_hash` of the first event is 64 zero hex characters.

This provides:

- detection of an in-place payload, timestamp, or linkage edit
- detection of a deleted or reordered middle event

The chain alone does not provide:

- authentication (there is no secret)
- detection of a truncated tail
- detection of a full rewrite that recomputes every later hash
- distributed consensus

### Optional checkpoints and signatures

A checkpoint stores `flight_id`, `last_sequence`, `last_hash`, a
timestamp, and optionally a signature over those fields. Checkpoints
live in their own table, not in the event rows.

```
checkpoint_signature = Sign(key, "flight_id|last_sequence|last_hash|timestamp")
```

Signers are pluggable: `NoOpSigner`, `HmacSha256Signer` (shared secret,
stdlib), `Ed25519Signer` (needs the optional `cryptography` extra). No
key is created or committed by this repository; the caller supplies it.

`verify_flight` reports three things separately:

| Field | Values |
| --- | --- |
| chain | `CHAIN_VALID`, `CHAIN_BROKEN` |
| signature | `SIGNATURE_VALID`, `SIGNATURE_INVALID`, `SIGNATURE_NOT_CONFIGURED` |
| tail | `TAIL_INTACT`, `TAIL_TRUNCATED`, `CHECKPOINT_MISMATCH`, `NO_CHECKPOINT` |

The report also counts `unanchored_events`, the events written after the
last checkpoint.

Limits: unanchored events can be deleted undetected. Anyone who can
delete the checkpoint rows as well as the events can hide a truncation.
Anyone holding the signing key can forge a checkpoint. HMAC verification
needs the same secret as signing, so the verifier can also forge.

## Live adapter

The live adapter sends the system prompt, mission, tool schemas, and
tool results to a third-party model API. Tool fixtures are synthetic, so
no customer data leaves the machine in this repository. Pointing the
adapter at real tools would change that. The API key is read from the
environment and never written to results or logs.

## Non-goals

No claim is made that AERIS prevents prompt injection, exfiltration,
or a hostile model. The control layer can only be as honest as the
runtime adapter that feeds it.
