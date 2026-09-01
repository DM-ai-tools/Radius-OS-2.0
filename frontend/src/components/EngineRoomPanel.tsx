import { useCallback, useEffect, useState } from "react";
import { api, EngineRoomPayload } from "../api";

function severityTone(s: string): string {
  if (s === "error") return "bad";
  if (s === "warning") return "warn";
  return "";
}

type Props = {
  token: string | null;
  onError?: (msg: string) => void;
};

export default function EngineRoomPanel({ token, onError }: Props) {
  const [data, setData] = useState<EngineRoomPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      setData(await api.engineRoom(token, 7));
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "Failed to load engine room");
    } finally {
      setLoading(false);
    }
  }, [token, onError]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !data) {
    return <p className="cc-empty-note">Loading engine room…</p>;
  }

  if (!data) {
    return <p className="cc-empty-note">Engine room data unavailable.</p>;
  }

  return (
    <div className="cc-engine-room">
      <div className="cc-card-head" style={{ marginBottom: 12 }}>
        <div>
          <p className="cc-kicker">Platform ops</p>
          <h2 style={{ margin: 0, fontSize: 20 }}>Engine room</h2>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text-secondary)" }}>
            Agents, integrations, and job health (7-day window).
          </p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      <div className="cc-kpi-strip" style={{ marginBottom: 16 }}>
        <article className="cc-kpi">
          <span className="cc-kpi-label">Agents OK</span>
          <div className="cc-kpi-metric">
            <span className="cc-kpi-value">
              {data.summary.agents_ok}/{data.summary.agents_total}
            </span>
          </div>
        </article>
        <article className="cc-kpi">
          <span className="cc-kpi-label">Open issues</span>
          <div className="cc-kpi-metric">
            <span className={`cc-kpi-value${data.summary.open_issues ? " cc-kpi-status warn" : ""}`}>
              {data.summary.open_issues}
            </span>
          </div>
        </article>
        <article className="cc-kpi">
          <span className="cc-kpi-label">Clients</span>
          <div className="cc-kpi-metric">
            <span className="cc-kpi-value">{data.summary.clients}</span>
          </div>
        </article>
      </div>

      {data.issues.length > 0 ? (
        <section className="cc-card" style={{ marginBottom: 16 }}>
          <div className="cc-card-head">
            <h3>Active issues</h3>
          </div>
          <div className="cc-card-body" style={{ overflowX: "auto" }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Source</th>
                  <th>Agent</th>
                  <th>Message</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.issues.slice(0, 25).map((issue, i) => (
                  <tr key={i}>
                    <td>
                      <span className={`cc-kpi-status ${severityTone(issue.severity)}`}>
                        {issue.severity}
                      </span>
                    </td>
                    <td>{issue.source}</td>
                    <td>{issue.agent_key || "—"}</td>
                    <td>{issue.message}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {issue.at ? new Date(issue.at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section className="cc-card" style={{ marginBottom: 16 }}>
        <div className="cc-card-head">
          <h3>Agents &amp; functions</h3>
        </div>
        <div className="cc-card-body" style={{ overflowX: "auto" }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr>
                <th>Phase</th>
                <th>Agent</th>
                <th>Key</th>
                <th>Registered</th>
                <th>Flag</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.agents.map((a) => (
                <tr key={a.agent_key}>
                  <td>{a.phase ?? "—"}</td>
                  <td>{a.label}</td>
                  <td style={{ fontFamily: "monospace", fontSize: 11 }}>{a.agent_key}</td>
                  <td>{a.registered ? "Yes" : "No"}</td>
                  <td>{a.feature_enabled ? "On" : "Off"}</td>
                  <td>
                    <span className={`cc-kpi-status ${a.status === "ok" ? "good" : "warn"}`}>
                      {a.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="cc-card" style={{ marginBottom: 16 }}>
        <div className="cc-card-head">
          <h3>Integrations</h3>
        </div>
        <div className="cc-card-body">
          <div className="cc-kpi-strip" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
            {data.integrations.map((integ) => (
              <article key={integ.provider} className="cc-kpi">
                <span className="cc-kpi-label">{integ.provider}</span>
                <span className={`cc-kpi-status ${integ.configured && !integ.mock ? "good" : "warn"}`}>
                  {integ.mock ? "mock" : integ.configured ? "live" : "missing keys"}
                </span>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="cc-card">
        <div className="cc-card-head">
          <h3>Recent agent jobs</h3>
        </div>
        <div className="cc-card-body" style={{ overflowX: "auto" }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr>
                <th>When</th>
                <th>Agent</th>
                <th>Type</th>
                <th>Status</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_jobs.slice(0, 20).map((j) => (
                <tr key={String(j.id)}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {j.created_at ? new Date(String(j.created_at)).toLocaleString() : "—"}
                  </td>
                  <td>{String(j.agent_key || "—")}</td>
                  <td>{String(j.job_type || "—")}</td>
                  <td>{String(j.status || "—")}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: 11 }}>
                    {String(j.error_detail || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
