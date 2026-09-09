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

  async function refresh(id = session?.id) {
    if (!id) return;
    const response = await fetch(`/api/handoffs/${id}`);
    if (!response.ok) throw new Error(await response.text());
    setSession(await response.json());
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

  async function resume() {
    if (!session) return;
    await fetch(`/api/handoffs/${session.id}/resume`, { method: "POST" });
    await refresh();
  }

  useEffect(() => {
    if (!session?.id) return;
    const timer = window.setInterval(() => void refresh(session.id), 1500);
    return () => window.clearInterval(timer);
  }, [session?.id]);

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
            <img ref={imageRef} onClick={clickScreenshot} src={`data:image/png;base64,${session.screenshot}`} alt="Live automated browser" />
          </section>
          <aside>
            <h2>Intervention</h2><p>{session.reason}</p>
            <dl><dt>Session</dt><dd>{session.id}</dd><dt>Control owner</dt><dd>{session.owner}</dd></dl>
            <p className="hint">Clicks on the browser image are applied to the same Playwright page while you hold the lease.</p>
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

