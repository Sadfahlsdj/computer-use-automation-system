import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Event = { actor: string; action: string; [key: string]: unknown };
type Session = {
  id: string;
  owner: "automation" | "human" | "closed";
  reason: string;
  url: string;
  screenshot: string;
  events: Event[];
};

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const imageRef = useRef<HTMLImageElement>(null);
  const commandQueue = useRef<Promise<void>>(Promise.resolve());

  async function refresh(id = session?.id) {
    if (!id) return;
    const response = await fetch(`/api/handoffs/${id}`);
    if (response.status === 404) {
      setSession((current) => current?.id === id ? null : current);
      setError("The previous handoff session expired when the server restarted.");
      return;
    }
    if (!response.ok) throw new Error(await response.text());
    const nextSession: Session = await response.json();
    if (!nextSession.id || nextSession.owner === "closed") {
      setSession(null);
      setError(nextSession.reason || "The previous handoff session expired.");
      return;
    }
    setSession(nextSession);
  }

  async function start() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/handoffs", { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      const created = await response.json();
      await refresh(created.id);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function clickScreenshot(event: React.MouseEvent<HTMLImageElement>) {
    if (!session || session.owner !== "human" || !imageRef.current) return;
    imageRef.current.focus();
    const rect = imageRef.current.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * imageRef.current.naturalWidth;
    const y = ((event.clientY - rect.top) / rect.height) * imageRef.current.naturalHeight;
    await fetch(`/api/handoffs/${session.id}/click`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y }),
    });
    await refresh();
  }

  function sendKeyboardCommand(path: "type" | "key", body: object) {
    if (!session || session.owner !== "human") return;
    const sessionId = session.id;
    commandQueue.current = commandQueue.current
      .catch(() => undefined)
      .then(async () => {
        const response = await fetch(`/api/handoffs/${sessionId}/${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(await response.text());
      })
      .catch((caught) => setError(String(caught)));
  }

  function keyScreenshot(event: React.KeyboardEvent<HTMLImageElement>) {
    if (!session || session.owner !== "human" || event.metaKey || event.ctrlKey || event.altKey) return;
    const supportedKeys = new Set([
      "Backspace", "Delete", "Enter", "Escape", "Tab",
      "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End",
    ]);
    if (event.key.length === 1) {
      event.preventDefault();
      sendKeyboardCommand("type", { text: event.key });
    } else if (supportedKeys.has(event.key)) {
      event.preventDefault();
      sendKeyboardCommand("key", { key: event.key });
    }
  }

  async function resume() {
    if (!session) return;
    await fetch(`/api/handoffs/${session.id}/resume`, { method: "POST" });
    await refresh();
  }

  useEffect(() => {
    if (!session?.id || session.owner !== "human") return;
    const sessionId = session.id;
    let timer: number | undefined;

    const poll = () => {
      void refresh(sessionId).catch((caught) => setError(String(caught)));
    };
    const updatePolling = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
      if (document.visibilityState === "visible") {
        poll();
        timer = window.setInterval(poll, 1500);
      }
    };

    updatePolling();
    document.addEventListener("visibilitychange", updatePolling);
    return () => {
      if (timer !== undefined) window.clearInterval(timer);
      document.removeEventListener("visibilitychange", updatePolling);
    };
  }, [session?.id, session?.owner]);

  return (
    <main>
      <header>
        <div><span className="eyebrow">INTERVENTION DESK</span><h1>Operator Console</h1></div>
        <button onClick={start} disabled={busy}>{busy ? "Starting…" : "Start demo handoff"}</button>
      </header>
      {error && <div className="error">{error}</div>}
      {!session ? (
        <section className="empty"><h2>No live intervention</h2><p>Start the demo to let automation reach a guarded action and cede its browser session.</p></section>
      ) : (
        <div className="grid">
          <section className="viewer">
            <div className="session-bar"><span className={`owner ${session.owner}`}>{session.owner}</span><code>{session.url}</code></div>
            <img
              ref={imageRef}
              onClick={clickScreenshot}
              onKeyDown={keyScreenshot}
              src={`data:image/png;base64,${session.screenshot}`}
              alt="Live automated browser"
              aria-label="Interactive live browser. Click a field, then type while this image is focused."
              role="application"
              tabIndex={session.owner === "human" ? 0 : -1}
            />
          </section>
          <aside>
            <h2>Intervention</h2><p>{session.reason}</p>
            <dl><dt>Session</dt><dd>{session.id}</dd><dt>Control owner</dt><dd>{session.owner}</dd></dl>
            <p className="hint">
              {session.owner === "human"
                ? "Click a field in the browser image, then type. Mouse and keyboard input are applied to the same Playwright page."
                : "Automation owns this session. Start a new handoff to interact as the human operator."}
            </p>
            <button className="resume" onClick={resume} disabled={session.owner !== "human"}>Return control</button>
            <h3>Audit trail</h3>
            <ol>{session.events.map((item, index) => <li key={index}><strong>{item.actor}</strong> · {item.action}</li>)}</ol>
          </aside>
        </div>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
