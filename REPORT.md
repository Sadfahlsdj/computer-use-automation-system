# Architecture

The system is a Python 3.12 modular monolith. `DiscoveryEngine` asks an OpenRouter model for one schema-validated action at a time; `ReplayEngine` interprets saved actions without a model. Both operate through a `Surface` protocol and pass every action through the same `PolicyEngine`. `PlaywrightSurface` is the implemented browser adapter. `EvidenceRecorder` writes redacted JSONL events, while Playwright supplies screenshots, DOM snapshots, and traces. FastAPI serves the synthetic target and a small React operator console.

Discovery accepts a natural-language goal and invocation inputs, observes the live page, asks the model to decide, applies the validated action, and repeats until the checkpoint and required outputs are satisfied, a known business outcome is observed, or a stopping condition is reached. Model requests have explicit timeouts and heartbeats, and the loop has a policy-controlled step limit. The current command fixes the target URL, checkpoint, and output contract to the bundled demo; making that discovery specification externally configurable is a documented cut.

The target is a local, synthetic back-office application with an iframe, table layouts, inconsistent semantics, and injected not-found, permission, and session-expiry states. It exercises legacy-web constraints without using real credentials or PII. The CLI executor owns its browser context. On intervention, it registers references to that exact page and context with a lease-based handoff manager and lazily serves the operator UI in the executor process. `cua serve` remains a separate process for the target application. This small-process design avoids queues and distributed session infrastructure while preserving the important ownership and control-transfer seams.

# Artifact schema

Pydantic models define a versioned capability contract with typed inputs and outputs, ordered discriminated-union steps, risk labels, locator bundles, known business outcomes, and final checkpoints. YAML is the review format; loading validates the same schema used by Python. Invocation data is referenced as `{{ inputs.name }}` rather than copied into the artifact, keeping one flow reusable across calls.

A target has an optional frame plus ordered locator candidates. Semantic role/name and label locators are preferred because they represent user-facing intent; scoped text and CSS are fallbacks for legacy markup. Replay requires declared visibility and uniqueness, rejecting ambiguous matches instead of guessing. Discovery reasons are retained in structured evidence for review, while the artifact keeps only the reusable contract and ordered robustness strategy rather than a brittle model transcript or raw coordinate sequence. Capability and schema versions make later migration and compatibility decisions explicit.

# Determinism & error handling

Replay validates the artifact and invocation, authorizes each step, resolves a unique target, performs the fixed action, extracts declared outputs, and verifies the final checkpoint. There is no LLM call on this path. The structured result distinguishes success with outputs, a known business outcome, and failure with a category, message, step identifier, retryability flag, and evidence ID. The demonstration treats “member not found” as a legitimate business classification rather than a crash. Invalid input, disallowed policy, application errors, and checkpoints that still fail after intervention are hard failures.

Waits are attached to locator visibility and explicit checkpoints instead of fixed sleeps. Locator candidates are tried in order, and retries are bounded by both artifact and policy. Known not-found, permission-denied, and session-expired screens can be declared as explicit business outcomes. An unresolved or ambiguous target and a failed checkpoint route to manual recovery when an intervention handler is present; replay retries the blocked step or rechecks the checkpoint after control returns. If the condition remains unresolved, replay returns a hard failure rather than proceeding blindly. Each run records structured events, a final screenshot, and a Playwright trace; discovery additionally records per-observation screenshots and model reasons.

This slice does not yet implement automatic reauthentication, unexpected-dialog classification, or a general backoff strategy for slow loads. Those states therefore time out, escalate where the typed surface error permits it, or return an application failure. The deliberate boundary is bounded deterministic execution plus human recovery, not open-ended model repair during production replay.

# Heterogeneity & multi-tenant

The execution verbs are surface-neutral and Playwright-specific resolution lives behind `Surface`. The present locator variants are web-oriented, so a desktop extension would add typed accessibility and visual-anchor locator variants rather than pretending CSS applies everywhere. A desktop adapter could resolve semantic candidates against an OS accessibility tree and use screenshot anchors or coordinates as bounded fallbacks. Legacy web frames are already represented explicitly.

Capabilities identify a vendor product independently of a tenant. A production catalog would store an approved base artifact per vendor and supported version range, plus small tenant overlays for origins, route templates, branding text, feature flags, and locator candidates. Compatibility probes and replay telemetry would select an applicable version. Repeated checkpoint or locator failures would quarantine only the drifting tenant/version and require review instead of silently changing a globally approved flow.

# Escalation & handoff

An intervention contains the session ID, capability, goal, current step, live screenshot, reason, control owner, and audit trail. Discovery routes an explicit model `escalate` decision to the operator. Replay routes an unresolved target or checkpoint and pauses before a policy-gated irreversible step. Because a CLI executor and `cua serve` are separate processes, the CLI lazily starts a small operator bridge on port 8001 in the executor process; that bridge receives references to the executor's exact Playwright page and context rather than attempting to reconstruct them elsewhere.

The operator can act on the live page through screenshot coordinates and keyboard commands while automation awaits a bounded resume event. Human and control-transfer actions are appended to the same evidence recorder used by the run. The lease identifies a single owner, and the server rejects human commands unless the human holds it. Approval and manual recovery are distinct: **Approve and return control** authorizes one named irreversible step, while **Return control** after manual recovery causes discovery to re-observe or replay to retry/recheck the blocked operation. This avoids treating arbitrary human interaction as blanket authorization.

The genuine LLM discovery evidence and curated handoff replay are intentionally separate demonstrations. A discovery run is still wired to the same live-session handoff path whenever the model requests escalation. The manually authored `member-open-subaccount.yaml` fixture makes the risky-action path reproducible without claiming that it was model-generated. A standalone handoff button remains as a quick UI-only demonstration.

The demo does not attempt production co-browsing. A deployment would add authenticated operators, expiring leases, WebRTC streaming, durable intervention routing, and stronger reconnect semantics without changing the executor/session seam.

# Safety

Policy permits specific origins, path prefixes, action kinds, step counts, retry bounds, and risk levels. It is checked immediately before execution, not only when an artifact is created. Steps distinguish read-only, reversible, and irreversible risk. Irreversible actions require explicit human approval of the named step before automation executes it. Artifacts contain parameter references, and the evidence recorder redacts supplied invocation values and bearer tokens. API keys are loaded from an ignored environment file. All bundled people, identifiers, and balances are synthetic.

Screenshots and traces can themselves contain regulated data in a real deployment. They would need encrypted short-retention storage, access controls, tenant isolation, and content-aware masking; string redaction alone is not sufficient outside this synthetic demonstration.

# Cuts

The implementation omits desktop control, distributed execution, durable browser recovery, real authentication, production operator streaming, tenant overlays, and open-ended LLM recovery. Discovery currently fixes the target URL, expected outputs, business outcomes, and checkpoint to the bundled member workflow rather than accepting them as an external discovery specification. Replay does not yet classify unexpected dialogs or automatically reauthenticate after session expiry. Intervention state is in memory and is lost if the executor exits.

These cuts keep the submission focused on a complete vertical slice: genuine LLM discovery, a typed capability, deterministic replay, explicit business outcomes, policy enforcement, and same-session human control transfer. Next work would make the discovery contract configurable, add typed session-expiry and dialog recovery, persist intervention state, and demonstrate a base capability against two tenant variants. Scaling infrastructure would follow only after replay stability and policy behavior are measured.
