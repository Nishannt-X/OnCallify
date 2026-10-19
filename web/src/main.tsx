import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import { IncidentDetail } from "./views/IncidentDetail";
import { Incidents } from "./views/Incidents";
import { Contracts, Experiments, Safety, Settings } from "./views/Other";

type Route = { page: "incidents" | "incident" | "contracts" | "experiments" | "safety" | "settings"; id?: string };

function parse(hash: string): Route {
  const [page, id] = hash.replace(/^#\/?/, "").split("/");
  if (page === "incident" && id) return { page: "incident", id };
  if (page === "contracts" || page === "experiments" || page === "safety" || page === "settings") return { page };
  return { page: "incidents" };
}

function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (h: string) => {
    window.location.hash = h;
  };
  const nav: [Route["page"], string][] = [
    ["incidents", "Incidents"],
    ["contracts", "Contracts"],
    ["experiments", "Experiments"],
    ["safety", "Safety"],
    ["settings", "Settings"],
  ];
  const current = route.page === "incident" ? "incidents" : route.page;
  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">✓</span> OnCallify
        </a>
        <nav className="nav">
          {nav.map(([p, label]) => (
            <a key={p} href={`#/${p}`} className={current === p ? "active" : ""}>
              {label}
            </a>
          ))}
        </nav>
        <span className="spacer" />
        <span className="small faint">watching ledgerline</span>
      </header>
      <main>
        {route.page === "incidents" && <Incidents open={(id) => go(`/incident/${id}`)} />}
        {route.page === "incident" && route.id && <IncidentDetail id={route.id} back={() => go("/incidents")} />}
        {route.page === "contracts" && <Contracts />}
        {route.page === "experiments" && <Experiments />}
        {route.page === "safety" && <Safety />}
        {route.page === "settings" && <Settings />}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
