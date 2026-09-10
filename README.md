# Computer-Use Automation System

A Python 3.12 vertical slice that uses an LLM to discover a browser workflow, saves it as a typed capability, and replays it deterministically with Playwright. It includes policy enforcement, redacted evidence, explicit business outcomes, and a same-session human handoff.

The bundled target is synthetic. It uses an iframe, old-fashioned tables, inconsistent semantics, and injected error states without involving a real institution or real PII.

## Setup

Requirements: Python 3.12 and Node.js 20.19 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
playwright install chromium
cd operator-ui && npm install && npm run build && cd ..
```

Start the application:

```bash
cua serve
```

Open <http://127.0.0.1:8000> for the operator console or <http://127.0.0.1:8000/demo> for the synthetic legacy application.

## Demo path

In one terminal, keep `cua serve` running. In another, activate the virtual environment.

Run deterministic replay with a known synthetic member:

```bash
cua replay \
  evidence/capabilities/discovered-success-2026-09-10T19-43-39.311729Z.yaml \
  --input member_id=10001
```

Exercise the legitimate not-found outcome:

```bash
cua replay evidence/capabilities/member-read-savings.yaml --input member_id=99999
```

Run genuine LLM-driven discovery:

```bash
cp .env.example .env
# Edit .env and set OPENROUTER_API_KEY.
# With the current testing site, member 99999 will always return member not found
cua discover \
  --goal 'Look up member 10001 and return the savings balance and member status' \
  --input member_id=10001 \
  --output evidence/capabilities/discovered.yaml
```

The CLI automatically loads `.env`. It reads `OPENROUTER_API_KEY`, `OPENROUTER_PROVIDER`, and `OPENROUTER_MODEL`; command-line options override those values. The provider and model are combined into OpenRouter's `provider/model` ID. The real `.env` is gitignored while `.env.example` is safe to commit. `--api-key` is also supported, but the environment file avoids storing the key in shell history.

For example, `OPENROUTER_PROVIDER=nex-agi` and `OPENROUTER_MODEL=nex-n2.5-pro:free` produce `nex-agi/nex-n2.5-pro:free`. A fully qualified value such as `OPENROUTER_MODEL=openrouter/free` is also accepted and takes precedence over the separate provider. The free router selects an available free model that supports the request, although pinning a model makes evidence runs more repeatable.

Discovery prints timestamped progress for browser startup, page observations, OpenRouter request attempts and elapsed time, validation retries, browser actions, checkpoint verification, and artifact saving. Input values and API keys are not included in these messages.

Each OpenRouter request is limited to 120 seconds by default and reports a heartbeat every 15 seconds while waiting. The SDK's hidden automatic retries are disabled. Set `OPENROUTER_REQUEST_TIMEOUT_SECONDS` in `.env` or pass `--request-timeout` to choose a different limit.

Discovery checks known terminal business outcomes before requesting another model decision. The demo recognizes member-not-found, permission-denied, and session-expired pages and records those conditions in the generated capability. For example, discovering with member `99999` stops at `MEMBER_NOT_FOUND` instead of retrying the search until the step limit.

The requested `--output` is treated as a filename base. Discovery appends the outcome and evidence timestamp so runs do not overwrite one another, for example `discovered-success-2026-09-10T19-20-00.000000Z.yaml` and `discovered-business-member-not-found-2026-09-10T19-21-00.000000Z.yaml`. Successful discovery also requires both `balance` and `member_status` extraction steps before accepting model completion.

To demonstrate an integrated replay handoff, keep `cua serve` running and execute:

```bash
cua replay evidence/capabilities/member-open-subaccount.yaml \
  --input member_id=10001 \
  --input nickname=Vacation
```

Replay stops before the synthetic irreversible `create-account` step and prints a direct operator-console URL on port 8001. Open that URL, inspect the same live Playwright page, and select **Approve and return control**; that explicit action authorizes replay to execute the named step. For stuck-state interventions, the operator may manipulate the page before selecting **Return control**, after which automation retries or rechecks the blocked operation. All human actions are recorded in the run's `events.jsonl`. The operator bridge starts only when needed and stops when the CLI run finishes. The default wait is ten minutes; `--handoff-timeout` and `--handoff-port` are configurable.

`evidence/capabilities/member-open-subaccount.yaml` is a manually authored, curated test fixture. It exists to make the irreversible-action handoff reproducible without relying on an LLM to choose that workflow during a demonstration. It is not presented as a discovery output. Genuine LLM-generated artifacts use timestamped names such as `discovered-success-2026-09-10T19-43-39.311729Z.yaml` and are paired with their discovery evidence under `evidence/runs/`.

The **Start demo handoff** button at <http://127.0.0.1:8000> remains available as a standalone UI demonstration. Click a field in the screenshot and continue typing while the screenshot has the yellow focus outline; mouse and keyboard input are routed to that page and audited.

The console polls only while a human-owned session is visible. Background tabs pause polling and returning control stops it. After a server restart, an unknown session returns a closed-session tombstone with an empty ID so both current and previously loaded frontends discard the stale ID and stop polling after one request.

## Evidence

Every run creates a UTC timestamped directory such as
`evidence/runs/2026-09-09T18-42-31.123456Z/` containing:

- `events.jsonl`, with invocation values redacted;
- a final screenshot for replay;
- discovery screenshots when applicable; and
- `trace.zip`, a Playwright trace with screenshots and DOM snapshots.

Generated run evidence is gitignored by default so a reviewer can choose which synthetic runs to commit. The example artifact is stored in `evidence/capabilities/`.

## Tests and code quality

```bash
ruff check .
pytest
```

## Important limitations

- The repository includes a real OpenRouter discovery adapter, but a genuine discovery artifact must be generated with the submitter's API key.
- Browser sessions are in process and intentionally non-durable.
- The operator console uses screenshot polling rather than video streaming.
- Desktop adapters and tenant infrastructure are design seams, not implementations.
