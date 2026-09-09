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
cua replay evidence/capabilities/member-read-savings.yaml --input member_id=10001
```

Exercise the legitimate not-found outcome:

```bash
cua replay evidence/capabilities/member-read-savings.yaml --input member_id=99999
```

Run genuine LLM-driven discovery:

```bash
export OPENAI_API_KEY='your-key'
cua discover \
  --goal 'Look up member 10001 and return the savings balance and member status' \
  --input member_id=10001 \
  --output evidence/capabilities/discovered.yaml
```

Discovery refuses to start without `OPENAI_API_KEY`; there is no scripted provider presented as real evidence. Set `--model` if the default model is unavailable to your account.

To demonstrate handoff, open the operator console and select **Start demo handoff**. Automation navigates to a confirmation screen, releases its control lease, and exposes the same live Playwright page. Click a field in the screenshot and continue typing while the screenshot has the yellow focus outline; mouse and keyboard input are routed to that page and audited. **Return control** transfers the lease back to automation and disables further operator input.

## Evidence

Every run creates `evidence/runs/<evidence-id>/` containing:

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

- The repository includes a real OpenAI discovery adapter, but a genuine discovery artifact must be generated with the submitter's API key.
- Browser sessions are in process and intentionally non-durable.
- The operator console uses screenshot polling rather than video streaming.
- Desktop adapters and tenant infrastructure are design seams, not implementations.
