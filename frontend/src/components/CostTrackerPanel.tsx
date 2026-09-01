import { useCallback, useEffect, useState } from "react";
import { api, ClientCostPayload, CostTrackerPayload } from "../api";

function fmtUsd(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(4)}`;
}

type Props = {
  token: string | null;
  clientId?: string;
  clientName?: string;
  onError?: (msg: string) => void;
};

const DAY_OPTIONS = [7, 14, 30, 90] as const;

export default function CostTrackerPanel({ token, clientId, clientName, onError }: Props) {
  const [platformDays, setPlatformDays] = useState(7);
  const [clientDays, setClientDays] = useState(30);
  const [platform, setPlatform] = useState<CostTrackerPayload | null>(null);
  const [clientCosts, setClientCosts] = useState<ClientCostPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [platformData, clientData] = await Promise.all([
        api.costTracker(token, platformDays),
        clientId ? api.clientCosts(token, clientId, clientDays) : Promise.resolve(null),
      ]);
      setPlatform(platformData);
      setClientCosts(clientData);
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "Failed to load cost tracker");
    } finally {
      setLoading(false);
    }
  }, [token, platformDays, clientDays, clientId, onError]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !platform) {
    return <p className="cc-empty-note">Loading cost tracker…</p>;
  }

  if (!platform) {
    return <p className="cc-empty-note">Cost data unavailable.</p>;
  }

  return (
    <div className="cc-cost-tracker">
      <div className="cc-card-head" style={{ marginBottom: 12 }}>
        <div>
          <p className="cc-kicker">Finance</p>
          <h2 style={{ margin: 0, fontSize: 20 }}>API cost tracker</h2>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text-secondary)" }}>
            Per-call spend across LLM and data providers — platform-wide and per client.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            Window
            <select
              value={platformDays}
              onChange={(e) => setPlatformDays(Number(e.target.value))}
              style={{ marginLeft: 6 }}
            >
              {DAY_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  {d}d
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn btn-ghost" onClick={load} disabled={loading}>
            Refresh
          </button>
        </div>
      </div>

      <div className="cc-kpi-strip" style={{ marginBottom: 16 }}>
        <article className="cc-kpi">
          <span className="cc-kpi-label">Est. spend ({platformDays}d)</span>
          <div className="cc-kpi-metric">
            <span className="cc-kpi-value">{fmtUsd(platform.summary.estimated_cost_usd)}</span>
          </div>
        </article>
        <article className="cc-kpi">
          <span className="cc-kpi-label">API calls</span>
          <div className="cc-kpi-metric">
            <span className="cc-kpi-value">{platform.summary.api_calls}</span>
          </div>
        </article>
        <article className="cc-kpi">
          <span className="cc-kpi-label">Clients with usage</span>
          <div className="cc-kpi-metric">
            <span className="cc-kpi-value">{platform.summary.clients_with_usage}</span>
          </div>
        </article>
        {clientCosts ? (
          <article className="cc-kpi">
            <span className="cc-kpi-label">{clientName || "This client"} ({clientDays}d)</span>
            <div className="cc-kpi-metric">
              <span className="cc-kpi-value">{fmtUsd(clientCosts.estimated_cost_usd)}</span>
            </div>
          </article>
        ) : null}
      </div>

      {clientId && clientCosts ? (
        <section className="cc-card" style={{ marginBottom: 16 }}>
          <div className="cc-card-head">
            <div>
              <h3>{clientName || "Current client"}</h3>
              <p style={{ margin: 0, fontSize: 12, color: "var(--text-secondary)" }}>
                Spend attributed to this client
              </p>
            </div>
            <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              Window
              <select
                value={clientDays}
                onChange={(e) => setClientDays(Number(e.target.value))}
                style={{ marginLeft: 6 }}
              >
                {DAY_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    {d}d
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="cc-grid-7-5">
            <div className="cc-card-body" style={{ overflowX: "auto" }}>
              <h4 style={{ margin: "0 0 8px", fontSize: 13 }}>By vendor</h4>
              <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>Calls</th>
                    <th>USD</th>
                  </tr>
                </thead>
                <tbody>
                  {(clientCosts.cost_by_vendor || []).length ? (
                    clientCosts.cost_by_vendor.map((row) => (
                      <tr key={row.vendor}>
                        <td>{row.vendor}</td>
                        <td>{row.calls}</td>
                        <td>{fmtUsd(row.cost_usd)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={3}>No recorded calls for this client yet.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="cc-card-body" style={{ overflowX: "auto" }}>
              <h4 style={{ margin: "0 0 8px", fontSize: 13 }}>By model / API</h4>
              <table className="kw-report-table" style={{ width: "100%", fontSize: 11 }}>
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>Model</th>
                    <th>Calls</th>
                    <th>USD</th>
                  </tr>
                </thead>
                <tbody>
                  {(clientCosts.cost_by_model || []).length ? (
                    clientCosts.cost_by_model.map((row) => (
                      <tr key={`${row.provider}-${row.model}`}>
                        <td>{row.vendor}</td>
                        <td>{row.model}</td>
                        <td>{row.calls}</td>
                        <td>{fmtUsd(row.cost_usd)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={4}>—</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="cc-grid-7-5" style={{ marginTop: 12 }}>
            <div className="cc-card-body" style={{ overflowX: "auto" }}>
              <h4 style={{ margin: "0 0 8px", fontSize: 13 }}>By provider</h4>
              <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Provider</th>
                    <th>Calls</th>
                    <th>USD</th>
                  </tr>
                </thead>
                <tbody>
                  {clientCosts.cost_by_provider.length ? (
                    clientCosts.cost_by_provider.map((row) => (
                      <tr key={row.provider}>
                        <td>{row.provider}</td>
                        <td>{row.calls}</td>
                        <td>{fmtUsd(row.cost_usd)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={3}>No recorded calls for this client yet.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="cc-card-body" style={{ overflowX: "auto" }}>
              <h4 style={{ margin: "0 0 8px", fontSize: 13 }}>By agent</h4>
              <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Calls</th>
                    <th>USD</th>
                  </tr>
                </thead>
                <tbody>
                  {clientCosts.cost_by_agent.length ? (
                    clientCosts.cost_by_agent.map((row) => (
                      <tr key={row.agent_key}>
                        <td>{row.agent_key}</td>
                        <td>{row.calls}</td>
                        <td>{fmtUsd(row.cost_usd)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={3}>—</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      ) : null}

      <div className="cc-grid-7-5" style={{ marginBottom: 16 }}>
        <section className="cc-card">
          <div className="cc-card-head">
            <h3>By vendor (Claude, Ahrefs, …)</h3>
          </div>
          <div className="cc-card-body" style={{ overflowX: "auto" }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Calls</th>
                  <th>USD</th>
                </tr>
              </thead>
              <tbody>
                {(platform.cost_by_vendor || []).length ? (
                  platform.cost_by_vendor.map((row) => (
                    <tr key={row.vendor}>
                      <td>{row.vendor}</td>
                      <td>{row.calls}</td>
                      <td>{fmtUsd(row.cost_usd)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={3}>No API calls yet — run a phase to populate.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
        <section className="cc-card">
          <div className="cc-card-head">
            <h3>By model / API</h3>
          </div>
          <div className="cc-card-body" style={{ overflowX: "auto" }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 11 }}>
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Model / operation</th>
                  <th>Calls</th>
                  <th>Tokens</th>
                  <th>USD</th>
                </tr>
              </thead>
              <tbody>
                {(platform.cost_by_model || []).length ? (
                  platform.cost_by_model.map((row) => (
                    <tr key={`${row.provider}-${row.model}`}>
                      <td>{row.vendor}</td>
                      <td style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}>
                        {row.model}
                      </td>
                      <td>{row.calls}</td>
                      <td>
                        {row.prompt_tokens != null || row.completion_tokens != null
                          ? `${row.prompt_tokens ?? 0}/${row.completion_tokens ?? 0}`
                          : "—"}
                      </td>
                      <td>{fmtUsd(row.cost_usd)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={5}>—</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div className="cc-grid-7-5" style={{ marginBottom: 16 }}>
        <section className="cc-card">
          <div className="cc-card-head">
            <h3>Platform — by provider</h3>
          </div>
          <div className="cc-card-body" style={{ overflowX: "auto" }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Calls</th>
                  <th>USD</th>
                </tr>
              </thead>
              <tbody>
                {platform.cost_by_provider.length ? (
                  platform.cost_by_provider.map((row) => (
                    <tr key={row.provider}>
                      <td>{row.provider}</td>
                      <td>{row.calls}</td>
                      <td>{fmtUsd(row.cost_usd)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={3}>No API calls yet — run a phase to populate.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
        <section className="cc-card">
          <div className="cc-card-head">
            <h3>Platform — by agent</h3>
          </div>
          <div className="cc-card-body" style={{ overflowX: "auto" }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Calls</th>
                  <th>USD</th>
                </tr>
              </thead>
              <tbody>
                {platform.cost_by_agent.length ? (
                  platform.cost_by_agent.map((row) => (
                    <tr key={row.agent_key}>
                      <td>{row.agent_key}</td>
                      <td>{row.calls}</td>
                      <td>{fmtUsd(row.cost_usd)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={3}>—</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="cc-card" style={{ marginBottom: 16 }}>
        <div className="cc-card-head">
          <h3>Platform — by client</h3>
        </div>
        <div className="cc-card-body" style={{ overflowX: "auto" }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr>
                <th>Client</th>
                <th>Calls</th>
                <th>USD</th>
              </tr>
            </thead>
            <tbody>
              {platform.cost_by_client.length ? (
                platform.cost_by_client.map((row) => (
                  <tr key={row.client_id || row.client_name}>
                    <td>{row.client_name}</td>
                    <td>{row.calls}</td>
                    <td>{fmtUsd(row.cost_usd)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={3}>—</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="cc-card">
        <div className="cc-card-head">
          <h3>Recent API calls</h3>
        </div>
        <div className="cc-card-body" style={{ overflowX: "auto" }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 11 }}>
            <thead>
              <tr>
                <th>When</th>
                <th>Vendor</th>
                <th>Model</th>
                <th>Operation</th>
                <th>Agent</th>
                <th>Client</th>
                <th>Tokens</th>
                <th>Cost</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {platform.recent_api_calls.slice(0, 50).map((c) => (
                <tr key={String(c.id)}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {c.created_at ? new Date(String(c.created_at)).toLocaleString() : "—"}
                  </td>
                  <td>{String(c.vendor || c.provider || "—")}</td>
                  <td style={{ maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {String(c.model || "—")}
                  </td>
                  <td style={{ maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {String(c.operation || "—")}
                  </td>
                  <td>{String(c.agent_key || "—")}</td>
                  <td style={{ fontSize: 10 }}>
                    {c.client_id ? String(c.client_id).slice(0, 8) : "—"}
                  </td>
                  <td>
                    {c.prompt_tokens != null || c.completion_tokens != null
                      ? `${c.prompt_tokens ?? 0}/${c.completion_tokens ?? 0}`
                      : "—"}
                  </td>
                  <td>{fmtUsd(Number(c.estimated_cost_usd || 0))}</td>
                  <td>{String(c.status || "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
