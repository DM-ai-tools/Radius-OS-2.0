import { useState } from "react";

type ParamScore = { label?: string; score: number; evidence?: string };
type Scorecard = {
  name: string;
  url: string;
  composite: number;
  tier: number;
  tier_name: string;
  parameters: Record<string, ParamScore>;
  derived: {
    similarity: number;
    maturity: number;
    future_threat: number;
    aspirational: number;
  };
  strengths?: string[];
  weaknesses?: string[];
  recommendation?: string;
};

type TierRow = {
  rank: number;
  name: string;
  tier: number;
  tier_name: string;
  composite: number;
  similarity: number;
  maturity: number;
  future_threat: number;
  aspirational: number;
};

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
  onAddManual?: (name: string, url: string) => void;
};

const TIER_LABELS: Record<string, string> = {
  "1": "TIER 1 — ASPIRATIONAL LEADERS (Study & Adapt)",
  "2": "TIER 2 — DIRECT COMPETITORS (Differentiate & Outperform)",
  "3": "TIER 3 — EMERGING CHALLENGERS (Monitor & Preempt)",
  "4": "TIER 4 — LOCAL COMPETITORS (Aware, Not Primary Focus)",
};

export default function CompetitorCard({ payload, canAct, onAction, onAddManual }: Props) {
  const tierOverview = (payload.tier_overview || []) as TierRow[];
  const scorecards = (payload.scorecards || []) as Scorecard[];
  const recommendations = (payload.recommendations || {}) as {
    benchmark?: string[];
    differentiate?: string[];
    monitor?: string[];
    top_emerging_threat?: { name: string; future_threat: number; note: string } | null;
  };
  const baseline = (payload.client_baseline || {}) as {
    maturity_score?: number;
    name?: string;
    url?: string;
    parameters?: Record<string, ParamScore>;
    service_scores?: Record<string, number>;
  };
  const serviceLevel = (payload.service_level_comparison || {}) as {
    categories?: string[];
    client?: Record<string, number>;
    best_by_service?: Record<string, { competitor?: string; score?: number }>;
    note?: string;
  };
  const heatmap = (payload.heatmap || {}) as {
    labels?: string[];
    parameters?: string[];
    client?: Record<string, number>;
    competitors?: Record<string, Record<string, number>>;
  };
  const tierMap = (payload.tier_map || {}) as Record<
    string,
    { name: string; composite: number; note?: string }[]
  >;
  const gaps = (payload.parameter_gaps || []) as {
    priority: string;
    parameter: string;
    client_score: number;
    top_competitor: string | null;
    top_competitor_score: number;
    gap: number;
    action: string;
  }[];
  const monitoring = (payload.monitoring_plan || {}) as {
    quarterly_checklist?: string[];
    tools?: { tool: string; purpose: string; frequency: string }[];
  };
  const excluded = (payload.excluded_tier5 || []) as { name: string; composite: number }[];

  const [openScorecard, setOpenScorecard] = useState<string | null>(
    scorecards[0]?.name || null
  );
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [showMonitoring, setShowMonitoring] = useState(true);
  const [manualName, setManualName] = useState("");
  const [manualUrl, setManualUrl] = useState("");

  const target = (payload.target_business || {}) as { name?: string; url?: string };
  const sources = (payload.discovery_sources || []) as string[];
  const inviteManual = Boolean(payload.invite_manual) || Boolean(onAddManual);

  if (payload.empty) {
    return (
      <div className="structured-card checkpoint competitor">
        <h3 className="card-title">Tiered Competitor Parameter Analysis</h3>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          No competitors found automatically yet. Add Discovery competitors or re-run after
          industry context is set (Architecture v1.9 operator override).
        </p>
        {inviteManual && onAddManual ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
            <input
              placeholder="Competitor name"
              value={manualName}
              onChange={(e) => setManualName(e.target.value)}
              style={{ flex: 1, minWidth: 120 }}
            />
            <input
              placeholder="https://…"
              value={manualUrl}
              onChange={(e) => setManualUrl(e.target.value)}
              style={{ flex: 2, minWidth: 160 }}
            />
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                if (manualName.trim() && manualUrl.trim()) {
                  onAddManual(manualName.trim(), manualUrl.trim());
                  setManualName("");
                  setManualUrl("");
                }
              }}
            >
              Add competitor
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="structured-card checkpoint competitor skill-report">
      <h3 className="card-title">
        {String(payload.title || "Tiered Competitor Parameter Analysis")}
        {target.name ? `: ${target.name}` : ""}
      </h3>
      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
        Generated: {String(payload.generated || "—")} · Target:{" "}
        {target.url || baseline.url || "—"} · Industry: {String(payload.industry || "—")} ·
        Scored {String(payload.competitors_scored ?? "—")} →{" "}
        {String(payload.competitors_in_report ?? tierOverview.length)} in report
        {sources.length ? ` · Sources: ${sources.join(", ")}` : ""} · Sign-off:{" "}
        <strong>{String(payload.required_role || "seo_strategist")}</strong>
      </p>

      {payload.executive_summary ? (
        <section className="report-section">
          <h4>Executive Summary</h4>
          <p style={{ fontSize: 13, lineHeight: 1.5, margin: 0 }}>
            {String(payload.executive_summary)}
          </p>
        </section>
      ) : null}

      {serviceLevel.best_by_service && Object.keys(serviceLevel.best_by_service).length ? (
        <section className="report-section">
          <h4>Service-level comparison (v1.9)</h4>
          {serviceLevel.note ? (
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>{serviceLevel.note}</p>
          ) : null}
          <ul className="missing-list">
            {Object.entries(serviceLevel.best_by_service).map(([svc, row]) => (
              <li key={svc}>
                <strong>{svc}</strong>: best = {String(row.competitor || "—")} (
                {String(row.score ?? "—")}/10)
                {serviceLevel.client?.[svc] != null
                  ? ` · client ${serviceLevel.client[svc]}/10`
                  : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {baseline.parameters && (
        <section className="report-section">
          <h4>Client Baseline Profile</h4>
          <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 8px" }}>
            {baseline.name} · Maturity{" "}
            <strong>{Math.round(Number(baseline.maturity_score || 0))}/100</strong>
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Parameter</th>
                  <th>Score</th>
                  <th>Assessment</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(baseline.parameters).map(([key, p]) => (
                  <tr key={key}>
                    <td>{p.label || key}</td>
                    <td>{p.score}/10</td>
                    <td style={{ fontSize: 12 }}>{p.evidence || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tierOverview.length > 0 && (
        <section className="report-section">
          <h4>Competitor Tier Overview</h4>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Competitor</th>
                  <th>Tier</th>
                  <th>Composite</th>
                  <th>Similarity</th>
                  <th>Maturity</th>
                  <th>Future Threat</th>
                  <th>Aspirational</th>
                </tr>
              </thead>
              <tbody>
                {tierOverview.map((r) => (
                  <tr key={r.name}>
                    <td>{r.rank}</td>
                    <td>{r.name}</td>
                    <td>
                      T{r.tier}: {r.tier_name}
                    </td>
                    <td>{r.composite}</td>
                    <td>{r.similarity}</td>
                    <td>{r.maturity}</td>
                    <td>{r.future_threat}</td>
                    <td>{r.aspirational}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {Object.keys(tierMap).length > 0 && (
        <section className="report-section">
          <h4>Visual Tier Map</h4>
          {(["1", "2", "3", "4"] as const).map((tier) =>
            (tierMap[tier] || []).length ? (
              <div key={tier} style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 4 }}>
                  {TIER_LABELS[tier]}
                </div>
                {(tierMap[tier] || []).map((c) => (
                  <div key={c.name} className="field-row" style={{ fontSize: 12 }}>
                    <strong>{c.name}</strong> — Score {c.composite}
                    {c.note ? ` — ${c.note}` : ""}
                  </div>
                ))}
              </div>
            ) : null
          )}
        </section>
      )}

      {recommendations.top_emerging_threat && (
        <div className="field-row" style={{ marginTop: 4, borderColor: "var(--coral)" }}>
          <div className="key">#1 emerging threat</div>
          <div>
            <strong>{recommendations.top_emerging_threat.name}</strong> — Future Threat{" "}
            {Math.round(recommendations.top_emerging_threat.future_threat)}/100.{" "}
            {recommendations.top_emerging_threat.note}
          </div>
        </div>
      )}

      {scorecards.length > 0 && (
        <section className="report-section">
          <h4>Detailed Competitor Scorecards</h4>
          {scorecards.map((s) => {
            const open = openScorecard === s.name;
            return (
              <div key={s.name} className="scorecard-block">
                <button
                  type="button"
                  className="scorecard-toggle"
                  onClick={() => setOpenScorecard(open ? null : s.name)}
                >
                  <span>
                    [Tier {s.tier}] {s.name} — {s.composite}/100 · {s.tier_name}
                  </span>
                  <span>{open ? "▾" : "▸"}</span>
                </button>
                {open && (
                  <div className="scorecard-body">
                    <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 8px" }}>
                      URL: {s.url}
                    </p>
                    <div className="table-wrap">
                      <table className="data">
                        <thead>
                          <tr>
                            <th>Parameter</th>
                            <th>Score</th>
                            <th>Evidence</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(s.parameters || {}).map(([key, p]) => (
                            <tr key={key}>
                              <td>{p.label || key}</td>
                              <td>{p.score}/10</td>
                              <td style={{ fontSize: 12 }}>{p.evidence || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="table-wrap" style={{ marginTop: 8 }}>
                      <table className="data">
                        <thead>
                          <tr>
                            <th>Derived score</th>
                            <th>Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>Similarity</td>
                            <td>{s.derived?.similarity}/100</td>
                          </tr>
                          <tr>
                            <td>Maturity</td>
                            <td>{s.derived?.maturity}/100</td>
                          </tr>
                          <tr>
                            <td>Future Threat</td>
                            <td>{s.derived?.future_threat}/100</td>
                          </tr>
                          <tr>
                            <td>Aspirational Benchmark</td>
                            <td>{s.derived?.aspirational}/100</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    {s.strengths?.length ? (
                      <div style={{ marginTop: 8, fontSize: 12 }}>
                        <strong>Key strengths</strong>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                          {s.strengths.map((x) => (
                            <li key={x}>{x}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {s.weaknesses?.length ? (
                      <div style={{ marginTop: 8, fontSize: 12 }}>
                        <strong>Key weaknesses / exploitable gaps</strong>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                          {s.weaknesses.map((x) => (
                            <li key={x}>{x}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {s.recommendation ? (
                      <p style={{ fontSize: 12, margin: "8px 0 0" }}>
                        <strong>Strategic recommendation:</strong> {s.recommendation}
                      </p>
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      )}

      {heatmap.labels && (
        <section className="report-section">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h4 style={{ margin: 0 }}>Comparative Parameter Heatmap</h4>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ fontSize: 12 }}
              onClick={() => setShowHeatmap((v) => !v)}
            >
              {showHeatmap ? "Hide" : "Show"}
            </button>
          </div>
          {showHeatmap && (
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Client</th>
                    {Object.keys(heatmap.competitors || {}).map((n) => (
                      <th key={n}>{n}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(heatmap.labels || []).map((label, i) => {
                    const key = heatmap.parameters?.[i];
                    if (!key) return null;
                    return (
                      <tr key={label}>
                        <td>{label}</td>
                        <td>{heatmap.client?.[key] ?? "—"}</td>
                        {Object.values(heatmap.competitors || {}).map((scores, j) => (
                          <td key={j}>{scores[key] ?? "—"}</td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section className="report-section">
        <h4>Strategic Recommendations</h4>
        {recommendations.benchmark?.length ? (
          <>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
              1. Benchmark actions (Tier 1)
            </div>
            {recommendations.benchmark.map((b) => (
              <div key={b} className="field-row" style={{ fontSize: 12 }}>
                {b}
              </div>
            ))}
          </>
        ) : null}
        {recommendations.differentiate?.length ? (
          <>
            <div style={{ fontSize: 12, fontWeight: 700, margin: "8px 0 4px" }}>
              2. Competitive differentiation (Tier 2)
            </div>
            {recommendations.differentiate.map((b) => (
              <div key={b} className="field-row" style={{ fontSize: 12 }}>
                {b}
              </div>
            ))}
          </>
        ) : null}
        {recommendations.monitor?.length ? (
          <>
            <div style={{ fontSize: 12, fontWeight: 700, margin: "8px 0 4px" }}>
              3. Threat monitoring (Tier 3)
            </div>
            {recommendations.monitor.map((b) => (
              <div key={b} className="field-row" style={{ fontSize: 12 }}>
                {b}
              </div>
            ))}
          </>
        ) : null}
      </section>

      {gaps.length > 0 && (
        <section className="report-section">
          <h4>Parameter Gap Priorities</h4>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Priority</th>
                  <th>Parameter</th>
                  <th>Client</th>
                  <th>Top competitor</th>
                  <th>Gap</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {gaps.slice(0, 10).map((g) => (
                  <tr key={g.parameter}>
                    <td>
                      {g.priority === "critical"
                        ? "Critical"
                        : g.priority === "important"
                          ? "Important"
                          : g.priority === "strength"
                            ? "Strength"
                            : "Watch"}
                    </td>
                    <td>{g.parameter}</td>
                    <td>{g.client_score}</td>
                    <td>
                      {g.top_competitor || "—"} ({g.top_competitor_score})
                    </td>
                    <td>{g.gap}</td>
                    <td style={{ fontSize: 12 }}>{g.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {monitoring.quarterly_checklist && (
        <section className="report-section">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h4 style={{ margin: 0 }}>Monitoring Plan</h4>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ fontSize: 12 }}
              onClick={() => setShowMonitoring((v) => !v)}
            >
              {showMonitoring ? "Hide" : "Show"}
            </button>
          </div>
          {showMonitoring && (
            <>
              <div style={{ fontSize: 12, fontWeight: 700, margin: "8px 0 4px" }}>
                Quarterly review checklist
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.45 }}>
                {monitoring.quarterly_checklist.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              {monitoring.tools?.length ? (
                <div className="table-wrap" style={{ marginTop: 10 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Tool</th>
                        <th>Purpose</th>
                        <th>Frequency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monitoring.tools.map((t) => (
                        <tr key={t.tool}>
                          <td>{t.tool}</td>
                          <td>{t.purpose}</td>
                          <td>{t.frequency}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          )}
        </section>
      )}

      {excluded.length > 0 && (
        <p style={{ fontSize: 11, color: "var(--muted)" }}>
          Tier 5 excluded from detail: {excluded.map((e) => e.name).join(", ")}
        </p>
      )}

      {inviteManual && onAddManual ? (
        <section className="report-section">
          <h4>Operator override — add competitor</h4>
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            Architecture v1.9: Discovery-listed competitors are scored first; auto-discovery
            supplements when fewer than six are on file. Add more here for the next scan.
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              placeholder="Competitor name"
              value={manualName}
              onChange={(e) => setManualName(e.target.value)}
              style={{ flex: 1, minWidth: 120 }}
            />
            <input
              placeholder="https://…"
              value={manualUrl}
              onChange={(e) => setManualUrl(e.target.value)}
              style={{ flex: 2, minWidth: 160 }}
            />
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                if (manualName.trim() && manualUrl.trim()) {
                  onAddManual(manualName.trim(), manualUrl.trim());
                  setManualName("");
                  setManualUrl("");
                }
              }}
            >
              Add for next run
            </button>
          </div>
        </section>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve landscape
          </button>
        </div>
      )}
    </div>
  );
}
