# Architecture

The system is a Python 3.12 modular monolith. `DiscoveryEngine` asks a model for one validated action at a time; `ReplayEngine` interprets saved actions without a model. Both operate through a `Surface` protocol and pass every action through the same `PolicyEngine`. `PlaywrightSurface` is the implemented browser adapter. `EvidenceRecorder` writes redacted JSONL events and Playwright supplies screenshots and traces. FastAPI serves the synthetic target and a small React operator console.

The browser context belongs to a session manager rather than either executor. That ownership boundary allows automation to pause and transfer an exclusive lease without replacing the live session. A single process is intentional for this exercise: queues, distributed workers, and durable session recovery would obscure the core contracts.

# Artifact schema

Pydantic models define a versioned capability with typed inputs and outputs, ordered discriminated-union steps, risk labels, locator bundles, known business outcomes, and final checkpoints. YAML is the review format; loading always validates the same schema used by Python. Invocation data is referenced as `{{ inputs.name }}` rather than copied into the artifact.

A target has an optional frame plus ordered candidates. Semantic role/name and label locators are preferred; scoped text and CSS are fallbacks. Replay requires uniqueness and visibility. This records intent and corroborating targeting information instead of preserving a brittle model transcript or raw coordinate sequence.

# Determinism & error handling

Replay validates the artifact and invocation, authorizes each step, resolves a unique target, performs the fixed action, and verifies declared conditions. There is no LLM call on this path. Results distinguish success, a business outcome, intervention, and failure. The demonstration treats “member not found” as a successful business classification, while invalid input, blocked policy, ambiguous target, missing target, application error, and failed checkpoint remain failures with a step identifier and evidence ID.

Waits are attached to locator visibility and explicit checkpoints instead of sleeps. Retries are bounded by both artifact and policy. Failure evidence includes a full-page screenshot, structured events, and Playwright trace. In a production extension, expected permission and session-expiry screens would become explicit recovery or reauthentication transitions rather than generic application errors.

# Heterogeneity & multi-tenant

The artifact refers to surface-neutral actions and targets; Playwright-specific resolution lives behind `Surface`. A desktop adapter could resolve the same semantic target candidates against an OS accessibility tree, with screenshot anchors as another locator variant. Legacy web frames are already represented explicitly.

Capabilities identify a vendor product independently of a tenant. A production catalog would store an approved base artifact per vendor/version range and small tenant overlays for origins, route templates, branding text, and locator candidates. Compatibility probes and replay telemetry would select a version and quarantine a drifting tenant instead of silently changing a globally approved flow.

# Escalation & handoff

An intervention contains the session, current state, reason, and audit trail. The demo automates through account lookup to a risky confirmation, changes the lease from automation to human, and preserves the same browser context. The operator can act on the live page through screenshot coordinates; actions are recorded with `actor=human`. Returning control changes the lease back to automation. The server rejects human commands when the human does not hold the lease.

The demo does not attempt production co-browsing. A deployment would add authenticated operators, expiring leases, WebRTC streaming, durable intervention routing, and stronger reconnect semantics without changing the executor/session seam.

# Safety

Policy permits specific origins, path prefixes, action kinds, step counts, retry bounds, and risk levels. It is checked immediately before execution, not only when an artifact is approved. Irreversible actions require a named step approval. Artifacts contain parameter references, and the evidence recorder redacts supplied values and bearer tokens. All bundled people and balances are synthetic.

Screenshots and traces can themselves contain regulated data in a real deployment. They would need encrypted short-retention storage, access controls, tenant isolation, and content-aware masking; string redaction alone is not sufficient outside this synthetic demonstration.

# Cuts

The implementation omits desktop control, distributed execution, durable browser recovery, real authentication, production operator streaming, tenant overlays, and open-ended LLM recovery. It also leaves real discovery evidence to the submitter because it requires their model API access. Next work would first add explicit session-expiry recovery and persisted intervention state, then demonstrate a base capability against two tenant variants. Scaling infrastructure would follow only after replay stability and policy behavior are measured.
