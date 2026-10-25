import { useEffect, useRef, useState } from "react";
import { api, usePoll } from "../api";
import type { IncidentSummary } from "../types";

interface Message {
  id: string;
  sender: "user" | "copilot";
  text: string;
  source?: string;
  elapsed_ms?: number;
  fingerprint?: string | null;
  incident_id?: string | null;
}

const QUICK_PROMPTS = [
  "Triage latest Ledgerline incident",
  "Why connection leak vs undersized pool?",
  "Explain blast radius of proposed fix",
  "What is the required approval fingerprint?",
  "Check emergency kill switch status",
  "List the 5 allowlisted runbooks",
];

export function Copilot({ activeIncidentId }: { activeIncidentId?: string }) {
  const { data: incData } = usePoll<{ incidents: IncidentSummary[] }>("/api/incidents?limit=20", 5000);
  const incidents = incData?.incidents ?? [];
  const [selectedIncident, setSelectedIncident] = useState<string>(activeIncidentId ?? "");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "m0",
      sender: "copilot",
      text: "👋 **Hello! I am your IBM Granite On-Call Copilot.**\n\nI have real-time access to the live Tomcat 10.1 Ledgerline metrics, PostgreSQL connection pool telemetry, Git config diffs, and the 5 allowlisted runbooks. You can ask me to explain a root cause, verify a blast radius, inspect error logs, or find the approval fingerprint.",
      source: "ibm-granite-engine",
    },
  ]);

  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async (msgText: string) => {
    const text = msgText.trim();
    if (!text || busy) return;

    const userMsg: Message = {
      id: `u-${Date.now()}`,
      sender: "user",
      text,
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setBusy(true);

    try {
      const res = await api.post<{
        reply: string;
        source: string;
        elapsed_ms: number;
        fingerprint?: string | null;
        incident_id?: string | null;
      }>("/api/chat", {
        message: text,
        incident_id: selectedIncident || undefined,
      });

      const copilotMsg: Message = {
        id: `c-${Date.now()}`,
        sender: "copilot",
        text: res.reply,
        source: res.source,
        elapsed_ms: res.elapsed_ms,
        fingerprint: res.fingerprint,
        incident_id: res.incident_id,
      };

      setMessages((prev) => [...prev, copilotMsg]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          sender: "copilot",
          text: `⚠️ **Error communicating with copilot backend:** ${e instanceof Error ? e.message : String(e)}`,
          source: "system-error",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" style={{ maxWidth: 960, margin: "0 auto" }}>
      {/* Header */}
      <div className="row-between">
        <div>
          <div className="row" style={{ gap: 10, marginBottom: 4 }}>
            <h1>IBM Granite <em>Copilot</em></h1>
            <span className="chip violet" style={{ fontSize: 11, padding: "2px 8px" }}>
              Enterprise SRE Copilot
            </span>
          </div>
          <p className="lede">
            Conversational incident assistant grounded in live Tomcat / PostgreSQL telemetry, allowlisted runbooks,
            and deterministic dry-run verification.
          </p>
        </div>

        {/* Incident Context Selector */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label className="small faint" htmlFor="inc-sel" style={{ fontWeight: 600 }}>
            Incident Context
          </label>
          <select
            id="inc-sel"
            value={selectedIncident}
            onChange={(e) => setSelectedIncident(e.target.value)}
            style={{ fontSize: 13, padding: "6px 10px" }}
          >
            <option value="">Latest Active Incident (Auto)</option>
            {incidents.map((i) => (
              <option key={i.id} value={i.id}>
                {i.id} · {i.title.slice(0, 36)} ({i.status})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Chat Area */}
      <div
        className="card stack"
        style={{
          minHeight: 480,
          maxHeight: 640,
          display: "flex",
          flexDirection: "column",
          padding: 0,
          overflow: "hidden",
        }}
      >
        {/* Messages Feed */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 20,
            display: "flex",
            flexDirection: "column",
            gap: 16,
          }}
        >
          {messages.map((m) => (
            <div
              key={m.id}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: m.sender === "user" ? "flex-end" : "flex-start",
                gap: 4,
              }}
            >
              <div
                style={{
                  maxWidth: "82%",
                  padding: m.sender === "user" ? "10px 16px" : "16px 20px",
                  borderRadius: "var(--r-card)",
                  background: m.sender === "user" ? "var(--emerald)" : "var(--surface-subtle)",
                  color: m.sender === "user" ? "var(--brand-ink)" : "var(--text)",
                  border: m.sender === "user" ? "none" : "1px solid var(--line)",
                  boxShadow: "var(--shadow-sm)",
                  fontSize: 14,
                  lineHeight: 1.55,
                  whiteSpace: "pre-wrap",
                }}
              >
                {m.text}

                {/* Fingerprint action pill if present */}
                {m.fingerprint && (
                  <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px dashed var(--line)" }}>
                    <div className="small faint" style={{ marginBottom: 4 }}>
                      Authorizing Fingerprint:
                    </div>
                    <div className="row" style={{ gap: 8 }}>
                      <code style={{ fontSize: 15, fontWeight: 700, letterSpacing: "0.1em" }}>
                        {m.fingerprint}
                      </code>
                      <button
                        className="btn"
                        style={{ padding: "3px 10px", fontSize: 11 }}
                        onClick={() => {
                          navigator.clipboard?.writeText(m.fingerprint!);
                          alert(`Copied fingerprint ${m.fingerprint} to clipboard!`);
                        }}
                      >
                        Copy Fingerprint
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {m.source && (
                <span className="small faint" style={{ fontSize: 11, padding: "0 6px" }}>
                  via {m.source} {m.elapsed_ms ? `· ${m.elapsed_ms}ms` : ""}
                </span>
              )}
            </div>
          ))}

          {busy && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 16px" }}>
              <span className="pulse-dot" style={{ background: "var(--emerald)" }} />
              <span className="small muted">IBM Granite is analyzing live incident telemetry...</span>
            </div>
          )}

          <div ref={endRef} />
        </div>

        {/* Quick Suggestion Chips */}
        <div
          style={{
            padding: "8px 16px",
            background: "var(--surface)",
            borderTop: "1px solid var(--line)",
            display: "flex",
            gap: 6,
            overflowX: "auto",
            whiteSpace: "nowrap",
          }}
        >
          {QUICK_PROMPTS.map((p) => (
            <button
              key={p}
              className="filter-btn"
              disabled={busy}
              onClick={() => send(p)}
              style={{ fontSize: 12, padding: "4px 10px" }}
            >
              {p}
            </button>
          ))}
        </div>

        {/* Chat Input Bar */}
        <div
          style={{
            padding: 14,
            background: "var(--surface-subtle)",
            borderTop: "1px solid var(--line)",
            display: "flex",
            gap: 10,
          }}
        >
          <input
            style={{ flex: 1, fontSize: 14 }}
            placeholder="Ask about this incident, root cause, blast radius, or approval..."
            value={input}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          <button
            className="primary"
            disabled={!input.trim() || busy}
            onClick={() => send(input)}
            style={{ padding: "8px 20px" }}
          >
            {busy ? "Thinking..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
