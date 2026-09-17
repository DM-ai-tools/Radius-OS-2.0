import { WordPressStatus } from "../api";

type Props = {
  clientName?: string;
  status: WordPressStatus | null;
  checking: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onRecheck: () => void;
};

function gateClass(state: string | undefined): string {
  if (state === "ready") return "good";
  if (state === "limited" || state === "failed") return "warn";
  return "info";
}

export default function WordPressWorkspace({
  clientName,
  status,
  checking,
  onConnect,
  onDisconnect,
  onRecheck,
}: Props) {
  const gate = status?.gate;
  const state = gate?.state || (status?.connected ? "ready" : "not_connected");
  const label = checking
    ? "Checking…"
    : gate?.label || (status?.connected ? "Connected" : "Not connected");

  return (
    <div className="cc-workspace">
      <p className="cc-kicker">Workspace · Connections</p>
      <h2 style={{ margin: "0 0 6px", fontSize: 20 }}>WordPress</h2>
      <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--text-secondary)" }}>
        Connect this client&apos;s own site. Radius OS tests the Application Password
        before saving it, then rechecks it whenever you open this section.
      </p>

      <section className="cc-card">
        <div className="cc-card-head">
          <div>
            <h2>{clientName || "This client"}</h2>
            <p>{gate?.detail || "No live check has completed yet."}</p>
          </div>
          <span className={`cc-kpi-status ${gateClass(state)}`}>{label}</span>
        </div>
        <div className="cc-card-body">
          <div className="cc-kpi-strip" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
            <article className="cc-kpi">
              <span className="cc-kpi-label">Site</span>
              <span className="cc-kpi-status">{status?.base_url || "—"}</span>
            </article>
            <article className="cc-kpi">
              <span className="cc-kpi-label">WordPress user</span>
              <span className="cc-kpi-status">{status?.wp_user || status?.username || "—"}</span>
            </article>
            <article className="cc-kpi">
              <span className="cc-kpi-label">Publish rights</span>
              <span className={`cc-kpi-status ${status?.can_publish ? "good" : "warn"}`}>
                {status?.can_publish == null ? "—" : status.can_publish ? "Can publish" : "Draft only"}
              </span>
            </article>
          </div>

          {status?.error ? (
            <p className="error-banner" style={{ marginTop: 12 }}>
              Live check failed: {status.error}
            </p>
          ) : null}

          <div className="card-actions" style={{ marginTop: 16 }}>
            {status?.connected || status?.base_url ? (
              <button type="button" className="btn btn-primary" onClick={onConnect}>
                Reconnect
              </button>
            ) : (
              <button type="button" className="btn btn-primary" onClick={onConnect}>
                Connect WordPress
              </button>
            )}
            <button type="button" className="btn btn-ghost" onClick={onRecheck} disabled={checking}>
              {checking ? "Checking…" : "Recheck status"}
            </button>
            {status?.base_url ? (
              <button type="button" className="btn btn-ghost" onClick={onDisconnect}>
                Disconnect
              </button>
            ) : null}
          </div>
          <p className="field-hint" style={{ marginTop: 12 }}>
            Use an Application Password from WordPress → Users → Profile, not the login password.
            Publishing still creates drafts unless live publish is explicitly enabled.
          </p>
        </div>
      </section>
    </div>
  );
}
