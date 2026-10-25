import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { getToken, usePoll } from "./api";
import { useNavIndicator, useScrolled, useTheme } from "./components/motion";
import "./theme.css";
import { IncidentDetail } from "./views/IncidentDetail";
import { Incidents } from "./views/Incidents";
import { Contracts, Experiments, Safety, Settings } from "./views/Other";
import { Copilot } from "./views/Copilot";
import type { IncidentSummary } from "./types";

type Route = { page: "incidents" | "incident" | "contracts" | "experiments" | "safety" | "settings" | "copilot"; id?: string };

function parse(hash: string): Route {
  const [page, id] = hash.replace(/^#\/?/, "").split("/");
  if (page === "incident" && id) return { page: "incident", id };
  if (page === "contracts" || page === "experiments" || page === "safety" || page === "settings" || page === "copilot") return { page, id };
  return { page: "incidents" };
}

function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  const scrolled = useScrolled(16);
  const { theme, toggle: toggleTheme } = useTheme();
  const navRef = useRef<HTMLElement>(null);

  // Poll incidents to display active badge count in navigation
  const { data: incData } = usePoll<{ incidents: IncidentSummary[] }>("/api/incidents?limit=100", 3500);
  const pendingApprovals = (incData?.incidents ?? []).filter((i) => i.status === "awaiting_approval").length;

  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const go = (h: string) => {
    window.location.hash = h;
  };

  const current = route.page === "incident" ? "incidents" : route.page;
  useNavIndicator(navRef, current);

  const nav: [Route["page"], string, number?][] = [
    ["incidents", "Incidents", pendingApprovals],
    ["copilot", "Granite Copilot"],
    ["contracts", "Standing Contracts"],
    ["experiments", "Experiments"],
    ["safety", "Safety & Proof"],
    ["settings", "Credentials"],
  ];

  const hasToken = !!getToken();

  return (
    <div className={`shell ${scrolled ? "scrolled" : ""}`}>
      <header className="topbar">
        {/* Brand */}
        <a className="brand" href="#/">
          <div className="brand-badge">✓</div>
          <div className="brand-title">
            <span>OnCallify</span>
            <span className="sub">autonomous engineer</span>
          </div>
        </a>

        {/* Sliding Navigation */}
        <nav ref={navRef} className="nav">
          {nav.map(([p, label, count]) => (
            <a key={p} href={`#/${p}`} className={current === p ? "active" : ""}>
              <span>{label}</span>
              {typeof count === "number" && count > 0 && <span className="count-badge">{count}</span>}
            </a>
          ))}
        </nav>

        <span className="spacer" />

        {/* Live Service Pill */}
        <div className="telemetry-pill live" title="Connected to Tomcat 10.1 Ledgerline at localhost:8080/ledgerline">
          <span className="pulse-dot" />
          <span>Ledgerline Node Active</span>
        </div>

        {/* Approver Token Pill */}
        <a
          href="#/settings"
          className="telemetry-pill"
          style={{ cursor: "pointer" }}
          title={hasToken ? "Approver token configured" : "No approver token set"}
        >
          <span>{hasToken ? "🔑 Token Armed" : "🔒 Read Only"}</span>
        </a>

        {/* Theme Toggle Button */}
        <button
          type="button"
          className="theme-toggle"
          onClick={toggleTheme}
          title={`Switch to ${theme === "light" ? "obsidian dark" : "editorial light"} mode`}
          aria-label="Toggle visual theme"
        >
          {theme === "light" ? "🌙" : "☀️"}
        </button>
      </header>

      <main style={{ flex: 1 }}>
        {route.page === "incidents" && <Incidents open={(id) => go(`/incident/${id}`)} />}
        {route.page === "incident" && route.id && <IncidentDetail id={route.id} back={() => go("/incidents")} />}
        {route.page === "copilot" && <Copilot activeIncidentId={route.id} />}
        {route.page === "contracts" && <Contracts />}
        {route.page === "experiments" && <Experiments />}
        {route.page === "safety" && <Safety />}
        {route.page === "settings" && <Settings />}
      </main>

      <footer
        style={{
          marginTop: 64,
          paddingTop: 24,
          borderTop: "1px solid var(--line)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          fontSize: 12.5,
          color: "var(--faint)",
        }}
      >
        <div className="row" style={{ gap: 14 }}>
          <span>
            <b>OnCallify</b> · High-Assurance AI On-Call for Java/Tomcat Payments Infrastructure
          </span>
          <span>·</span>
          <span>Zero Unsafe Actions Proved by Allowlist</span>
        </div>
        <div className="mono small">SHA-256 Atomic Approval Gateways · Paired McNemar Gates</div>
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
