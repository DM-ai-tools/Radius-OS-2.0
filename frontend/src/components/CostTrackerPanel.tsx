import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ClientCostPayload,
  CostCall,
  CostDay,
  CostPhaseRow,
  CostTrackerPayload,
  CostModelRow,
  CostVendorRow,
} from "../api";

type Props = {
  token: string | null;
  clientId?: string;
  clientName?: string;
  onError?: (msg: string) => void;
};

const WINDOWS = [7, 30, 90] as const;
const ALL = "__all__";
const UNLINKED = "__unlinked__";
const RECENT_LIMIT = 12;

/* ------------------------------------------------------------------ format */

function fmtUsd(n: number | null | undefined): string {
  const v = Number(n || 0);
  if (v === 0) return "$0.00";
  if (v < 0.01) return "<$0.01";
  if (v >= 1000) return `$${Math.round(v).toLocaleString()}`;
  return `$${v.toFixed(2)}`;
}

function fmtCount(n: number | null | undefined): string {
  return Number(n || 0).toLocaleString();
}

function share(part: number, whole: number): number {
  return whole > 0 ? Math.max(0, Math.min(100, (part / whole) * 100)) : 0;
}

function fmtShare(part: number, whole: number): string {
  const s = share(part, whole);
  if (s === 0) return "0%";
  if (s < 1) return "<1%";
  return `${Math.round(s)}%`;
}

function fmtDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso);
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function host(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url.startsWith("http") ? url : `https://${url}`).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

const VENDOR_TONES: Record<string, string> = {
  Claude: "var(--brand-blue)",
  DataForSEO: "var(--info)",
  Gemini: "var(--ai)",
  Perplexity: "var(--warning)",
  GPT: "var(--success)",
  Ahrefs: "var(--critical-bright)",
};

function vendorTone(vendor: string): string {
  return VENDOR_TONES[vendor] || "var(--text-muted)";
}

/* ----------------------------------------------------------------- pieces */

function DailyChart({ days, label }: { days: CostDay[]; label: string }) {
  const max = Math.max(0, ...days.map((d) => d.cost_usd));
  const peak = days.find((d) => d.cost_usd === max && max > 0);
  if (!days.length) return null;
  return (
    <figure className="ct-chart">
      <div
        className="ct-chart-bars"
        role="img"
        aria-label={
          peak
            ? `${label}: highest ${fmtUsd(peak.cost_usd)} on ${fmtDay(peak.date)}`
            : `${label}: no spend`
        }
      >
        {days.map((d) => (
          <span
            key={d.date}
            className={`ct-bar${d.cost_usd === 0 ? " is-zero" : ""}${d === peak ? " is-peak" : ""}`}
            style={{ height: `${max > 0 ? Math.max(3, (d.cost_usd / max) * 100) : 3}%` }}
            title={`${fmtDay(d.date)}: ${fmtUsd(d.cost_usd)} across ${fmtCount(d.calls)} calls`}
          />
        ))}
      </div>
      <figcaption className="ct-chart-axis">
        <span>{fmtDay(days[0].date)}</span>
        {peak ? (
          <span className="ct-chart-peak">
            Highest day {fmtDay(peak.date)}, {fmtUsd(peak.cost_usd)}
          </span>
        ) : null}
        <span>{fmtDay(days[days.length - 1].date)}</span>
      </figcaption>
    </figure>
  );
}

function PhaseBreakdown({ rows, total }: { rows: CostPhaseRow[]; total: number }) {
  const max = Math.max(0, ...rows.map((r) => r.cost_usd));
  if (!rows.length) return <p className="ct-muted">No phase has spent anything in this period.</p>;
  return (
    <ul className="ct-phases">
      {rows.map((r) => (
        <li key={r.agent_key}>
          <span className="ct-phase-name">{r.label || r.agent_key}</span>
          <span className="ct-phase-track" aria-hidden="true">
            <span style={{ width: `${max > 0 ? (r.cost_usd / max) * 100 : 0}%` }} />
          </span>
          <span className="ct-num">{fmtUsd(r.cost_usd)}</span>
          <span className="ct-num ct-muted">{fmtShare(r.cost_usd, total)}</span>
        </li>
      ))}
    </ul>
  );
}

function costLabel(cost: number, unpriced?: number, calls?: number): string {
  if ((unpriced || 0) > 0 && (unpriced || 0) === (calls || 0)) return "Not priced";
  if ((unpriced || 0) > 0) return `${fmtUsd(cost)} + ${fmtCount(unpriced)} unpriced`;
  return fmtUsd(cost);
}

function modelTitle(model: string): string {
  const tail = model.split("/").filter(Boolean).pop() || model;
  return tail.replace(/[_-]+/g, " ").replace(/\blive\b/i, "").trim();
}

function VendorSplit({ rows, total }: { rows: CostVendorRow[]; total: number }) {
  const paid = rows.filter((r) => r.cost_usd > 0 || (r.unpriced_calls || 0) > 0 || r.calls > 0);
  if (!paid.length) return <p className="ct-muted">No provider has billed anything in this period.</p>;
  return (
    <div className="ct-vendors">
      <div className="ct-vendor-bar" aria-hidden="true">
        {paid.filter((r) => r.cost_usd > 0).map((r) => (
          <span
            key={r.vendor}
            style={{ width: `${share(r.cost_usd, total)}%`, background: vendorTone(r.vendor) }}
          />
        ))}
      </div>
      <ul className="ct-vendor-legend">
        {paid.map((r) => (
          <li key={r.vendor}>
            <span className="ct-swatch" style={{ background: vendorTone(r.vendor) }} />
            <span className="ct-vendor-name">{r.vendor}</span>
            <span className="ct-num">{costLabel(r.cost_usd, r.unpriced_calls, r.calls)}</span>
            <span className="ct-num ct-muted">{fmtCount(r.calls)} calls</span>
            <span className="ct-num ct-muted">{fmtShare(r.cost_usd, total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ModelBreakdown({ rows }: { rows: CostModelRow[] }) {
  if (!rows.length) return <p className="ct-muted">No model or endpoint was called in this period.</p>;
  return (
    <ul className="ct-models">
      {rows.map((r) => {
        const units = r.provider === "ahrefs" ? r.prompt_tokens : null;
        const tokens =
          r.provider === "ahrefs"
            ? null
            : (r.prompt_tokens || 0) + (r.completion_tokens || 0);
        return (
          <li key={`${r.provider}:${r.model}`}>
            <span className="ct-model-name" title={r.model}>
              {modelTitle(r.model)}
              <span className="ct-muted">{r.vendor}</span>
            </span>
            <span className="ct-num ct-muted">
              {fmtCount(r.calls)} {r.calls === 1 ? "call" : "calls"}
              {units ? ` · ${fmtCount(units)} units` : ""}
              {tokens ? ` · ${fmtCount(tokens)} tokens` : ""}
            </span>
            <span className="ct-num">{costLabel(r.cost_usd, r.unpriced_calls, r.calls)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function RecentCalls({ calls }: { calls: CostCall[] }) {
  const shown = calls.slice(0, RECENT_LIMIT);
  if (!shown.length) return null;
  return (
    <details className="ct-recent">
      <summary>
        Latest {shown.length} calls
        <span className="ct-muted">
          {" "}
          {calls.some((c) => c.status && c.status !== "success") ? "including failures" : ""}
        </span>
      </summary>
      <ul>
        {shown.map((c) => {
          const failed = c.status && c.status !== "success";
          return (
            <li key={c.id} className={failed ? "is-failed" : undefined}>
              <span className="ct-muted">{fmtWhen(c.created_at)}</span>
              <span>
                {modelTitle(c.model || c.operation || c.vendor || c.provider || "Call")}
                <span className="ct-muted"> · {c.phase || c.vendor || c.provider}</span>
              </span>
              <span className="ct-num">
                {failed ? "Failed" : fmtUsd(c.cost_usd ?? c.estimated_cost_usd)}
              </span>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function Delta({ now, before, days }: { now: number; before: number | undefined; days: number }) {
  if (before == null) return null;
  if (before === 0) {
    return <span className="ct-delta ct-muted">Nothing spent in the {days} days before</span>;
  }
  const change = ((now - before) / before) * 100;
  const up = change >= 0;
  return (
    <span className={up ? "ct-delta is-up" : "ct-delta is-down"}>
      {up ? "Up" : "Down"} {Math.abs(Math.round(change))}% on the {days} days before ({fmtUsd(before)})
    </span>
  );
}

/* ------------------------------------------------------------------- panel */

export default function CostTrackerPanel({ token, clientId, clientName, onError }: Props) {
  const [days, setDays] = useState<number>(30);
  const [platform, setPlatform] = useState<CostTrackerPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<string>(clientId || ALL);
  const [clientDetail, setClientDetail] = useState<Record<string, ClientCostPayload>>({});
  const [detailLoading, setDetailLoading] = useState(false);
  const scopeTouched = useRef(false);

  const loadPlatform = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      setPlatform(await api.costTracker(token, days));
      setClientDetail({});
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "The cost tracker could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [token, days, onError]);

  useEffect(() => {
    void loadPlatform();
  }, [loadPlatform]);

  // Open on the client being worked on when it has spend; otherwise all clients.
  useEffect(() => {
    if (!platform || scopeTouched.current) return;
    const hasCurrent = platform.cost_by_client.some((c) => c.client_id === clientId);
    setScope(clientId && hasCurrent ? clientId : ALL);
  }, [platform, clientId]);

  const scopedClientId = scope !== ALL && scope !== UNLINKED ? scope : null;
  const detailKey = scopedClientId ? `${scopedClientId}:${days}` : "";

  useEffect(() => {
    if (!token || !scopedClientId || clientDetail[detailKey]) return;
    let cancelled = false;
    setDetailLoading(true);
    api
      .clientCosts(token, scopedClientId, days)
      .then((data) => {
        if (!cancelled) setClientDetail((prev) => ({ ...prev, [detailKey]: data }));
      })
      .catch((e) => onError?.(e instanceof Error ? e.message : "Client costs could not be loaded."))
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, scopedClientId, days, detailKey, clientDetail, onError]);

  const clients = platform?.cost_by_client || [];
  const total = platform?.summary.estimated_cost_usd || 0;
  const nameCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const c of clients) counts[c.client_name] = (counts[c.client_name] || 0) + 1;
    return counts;
  }, [clients]);

  const choose = (next: string) => {
    scopeTouched.current = true;
    setScope(next);
  };

  if (loading && !platform) {
    return (
      <div className="ct" aria-busy="true">
        <div className="ct-skeleton" />
      </div>
    );
  }

  if (!platform) {
    return (
      <div className="ct">
        <p className="ct-empty">
          The cost tracker could not be loaded.{" "}
          <button type="button" className="btn btn-ghost" onClick={() => void loadPlatform()}>
            Try again
          </button>
        </p>
      </div>
    );
  }

  const failed = platform.summary.failed_calls || 0;
  const selected = clients.find((c) => (c.client_id || UNLINKED) === scope);
  const detail = scopedClientId ? clientDetail[detailKey] : undefined;

  return (
    <div className="ct">
      <header className="ct-head">
        <div>
          <h2>Cost tracker</h2>
          <p className="ct-muted">Estimated API spend by client, phase and provider.</p>
        </div>
        <div className="ct-controls">
          <div className="ct-seg" role="radiogroup" aria-label="Period">
            {WINDOWS.map((w) => (
              <button
                key={w}
                type="button"
                role="radio"
                aria-checked={days === w}
                className={days === w ? "is-on" : undefined}
                onClick={() => setDays(w)}
              >
                {w} days
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void loadPlatform()}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      <section className="ct-hero" aria-label="Spend summary">
        <div className="ct-hero-figures">
          <p className="ct-total">
            <span className="ct-total-amount">{fmtUsd(total)}</span>
            <span className="ct-total-caption">spent in the last {days} days</span>
          </p>
          <Delta now={total} before={platform.summary.previous_cost_usd} days={days} />
          <p className="ct-facts">
            <span>
              <strong>{fmtCount(platform.summary.api_calls)}</strong> calls
            </span>
            <span>
              <strong>{fmtCount(platform.summary.clients_with_usage)}</strong>{" "}
              {platform.summary.clients_with_usage === 1 ? "client" : "clients"}
            </span>
            {failed > 0 ? (
              <span className="ct-warn" title="Failed calls are not counted in spend">
                <strong>{fmtCount(failed)}</strong> failed
              </span>
            ) : null}
          </p>
        </div>
        <DailyChart days={platform.daily || []} label="Daily spend, all clients" />
      </section>

      {total === 0 && !clients.length ? (
        <p className="ct-empty">
          Nothing was spent in the last {days} days. Spend appears here as soon as a phase runs.
        </p>
      ) : (
        <div className="ct-body">
          <section className="ct-ledger" aria-label="Spend by client">
            <h3>Clients</h3>
            <ul>
              <li>
                <button
                  type="button"
                  className={`ct-row is-total${scope === ALL ? " is-selected" : ""}`}
                  aria-pressed={scope === ALL}
                  onClick={() => choose(ALL)}
                >
                  <span className="ct-row-name">All clients</span>
                  <span className="ct-num">{fmtUsd(total)}</span>
                </button>
              </li>
              {clients.map((c) => {
                const id = c.client_id || UNLINKED;
                const site = host(c.client_url);
                const showSite = Boolean(site) && (nameCounts[c.client_name] || 0) > 1;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      className={`ct-row${scope === id ? " is-selected" : ""}${
                        c.client_id ? "" : " is-unlinked"
                      }`}
                      aria-pressed={scope === id}
                      onClick={() => choose(id)}
                    >
                      <span className="ct-row-name">
                        {c.client_name}
                        <span className="ct-row-site">
                          {showSite ? `${site}, ` : ""}
                          {c.client_id ? `active ${fmtWhen(c.last_used_at)}` : "scripts and tests"}
                        </span>
                      </span>
                      <span className="ct-num">{fmtUsd(c.cost_usd)}</span>
                      <span className="ct-num ct-muted">{fmtShare(c.cost_usd, total)}</span>
                      <span className="ct-row-share" aria-hidden="true">
                        <span style={{ width: `${share(c.cost_usd, total)}%` }} />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="ct-detail" aria-live="polite">
            {scope === ALL ? (
              <>
                <h3>All clients</h3>
                <div className="ct-block">
                  <h4>By phase</h4>
                  <PhaseBreakdown rows={platform.cost_by_agent} total={total} />
                </div>
                <div className="ct-block">
                  <h4>By provider</h4>
                  <VendorSplit rows={platform.cost_by_vendor} total={total} />
                </div>
                <div className="ct-block">
                  <h4>By model and endpoint</h4>
                  <ModelBreakdown rows={platform.cost_by_model} />
                </div>
                <RecentCalls calls={platform.recent_api_calls || []} />
              </>
            ) : scope === UNLINKED ? (
              <>
                <h3>Not linked to a client</h3>
                <div>
                  <p className="ct-detail-total">{fmtUsd(selected?.cost_usd)}</p>
                  <p className="ct-detail-meta ct-muted">{fmtCount(selected?.calls)} calls</p>
                </div>
                <p className="ct-muted ct-note">
                  Calls made outside a client workspace: test scripts, and the website
                  auto-fill on the new-client form, which runs before the client exists.
                  Chat runs and client screens are linked to their client automatically.
                </p>
              </>
            ) : (
              <>
                <h3>
                  {selected?.client_name || clientName || "Client"}
                  {selected?.client_url ? (
                    <span className="ct-detail-site">{host(selected.client_url)}</span>
                  ) : null}
                </h3>
                {!detail ? (
                  <div className="ct-skeleton is-short" aria-busy={detailLoading} />
                ) : (
                  <>
                    <div>
                      <p className="ct-detail-total">{fmtUsd(detail.estimated_cost_usd)}</p>
                      <p className="ct-detail-meta ct-muted">
                        {fmtCount(detail.call_count)} calls, last activity{" "}
                        {fmtWhen(detail.last_used_at)}
                      </p>
                    </div>
                    <Delta
                      now={detail.estimated_cost_usd}
                      before={detail.previous_cost_usd}
                      days={days}
                    />
                    <DailyChart days={detail.daily || []} label="Daily spend for this client" />
                    <div className="ct-block">
                      <h4>By phase</h4>
                      <PhaseBreakdown rows={detail.cost_by_agent} total={detail.estimated_cost_usd} />
                    </div>
                    <div className="ct-block">
                      <h4>By provider</h4>
                      <VendorSplit rows={detail.cost_by_vendor} total={detail.estimated_cost_usd} />
                    </div>
                    <div className="ct-block">
                      <h4>By model and endpoint</h4>
                      <ModelBreakdown rows={detail.cost_by_model} />
                    </div>
                    {(detail.failed_calls || 0) > 0 ? (
                      <p className="ct-warn ct-note">
                        {fmtCount(detail.failed_calls)} calls failed in this period. They are not
                        counted in spend.
                      </p>
                    ) : null}
                    <RecentCalls calls={detail.calls || []} />
                  </>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
