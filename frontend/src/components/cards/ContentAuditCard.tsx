type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

const COUNT_KEYS = [
  "keep",
  "refresh",
  "retitle",
  "optimise",
  "consolidate",
  "noindex",
  "delete_candidate",
] as const;

export default function ContentAuditCard({ payload, canAct, onAction }: Props) {
  const inventory = asRows(payload.inventory);
  const cannibalization = asRows(payload.cannibalization || payload.cannibalisation);
  const refreshQueue = asRows(payload.refresh_queue);
  const quickWins = asRows(payload.quick_wins);
  const reviewQueue = asRows(payload.review_queue);
  const handoffs = asRows(payload.handoffs);
  const combinedQueue = asRows(payload.combined_priority_queue);
  const couldNot = Array.isArray(payload.could_not_assess)
    ? (payload.could_not_assess as string[])
    : [];
  const counts =
    payload.summary_counts && typeof payload.summary_counts === "object"
      ? (payload.summary_counts as Record<string, number>)
      : null;
  const unmatched = Array.isArray(payload.gap_keywords_unmatched)
    ? (payload.gap_keywords_unmatched as string[])
    : [];
  const qualitative = Boolean(payload.qualitative);

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Content Audit")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client_name ? `${String(payload.client_name)} · ` : ""}
        {payload.primary_url ? String(payload.primary_url) : ""}
        {inventory.length ? ` · ${inventory.length} pages` : ""}
        {qualitative ? " · qualitative (no GSC)" : " · GSC-backed"}
      </p>
      {payload.executive_summary ? (
        <p style={{ fontSize: 14, lineHeight: 1.5 }}>{String(payload.executive_summary)}</p>
      ) : payload.note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.note)}</p>
      ) : null}

      {counts ? (
        <div className="cs-funnel-strip">
          {COUNT_KEYS.map((k) =>
            (counts[k] ?? 0) > 0 || k === "keep" || k === "refresh" ? (
              <span key={k}>
                {k.replace("_", " ")} <strong>{counts[k] ?? 0}</strong>
              </span>
            ) : null,
          )}
        </div>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Inventory & disposition</h4>
      {inventory.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Title</th>
                <th style={{ padding: "6px 8px" }}>Path</th>
                <th style={{ padding: "6px 8px" }}>Disposition</th>
                <th style={{ padding: "6px 8px" }}>Reason</th>
                <th style={{ padding: "6px 8px" }}>IA</th>
              </tr>
            </thead>
            <tbody>
              {inventory.slice(0, 40).map((row, i) => (
                <tr key={`${String(row.path || row.url)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(row.title || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(row.path || row.url || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="cs-chip">{String(row.disposition || "—")}</span>
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(row.reason || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{row.in_ia ? "Yes" : "No"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No inventory rows yet</p>
      )}

      <h4 style={{ marginBottom: 8 }}>Cannibalisation — fix first</h4>
      {cannibalization.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Query</th>
                <th style={{ padding: "6px 8px" }}>Keep</th>
                <th style={{ padding: "6px 8px" }}>Merge in</th>
                <th style={{ padding: "6px 8px" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {cannibalization.slice(0, 15).map((c, i) => (
                <tr key={`${String(c.keyword || c.query)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                    {String(c.keyword || c.query || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(c.keep || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(c.merge_in || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{String(c.action || "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No cannibalisation clusters</p>
      )}

      {refreshQueue.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Refresh queue — highest ROI</h4>
          <ul className="missing-list">
            {refreshQueue.slice(0, 10).map((r, i) => (
              <li key={i}>
                {String(r.path || r.url || "—")}
                {r.reason ? (
                  <span style={{ color: "var(--muted)", fontSize: 11 }}> — {String(r.reason)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {quickWins.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Quick wins (retitle / optimise)</h4>
          <ul className="missing-list">
            {quickWins.slice(0, 10).map((r, i) => (
              <li key={i}>
                <span className="cs-chip">{String(r.disposition)}</span>{" "}
                {String(r.path || r.url || "—")}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {reviewQueue.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Review queue — do not action without sign-off</h4>
          <ul className="missing-list">
            {reviewQueue.slice(0, 10).map((r, i) => (
              <li key={i}>
                <span className="cs-chip">{String(r.disposition)}</span>{" "}
                {String(r.path || r.url || "—")}
                {r.reason ? (
                  <span style={{ color: "var(--muted)", fontSize: 11 }}> — {String(r.reason)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {combinedQueue.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>
            Combined priority queue
            {payload.both_skills_present ? " (audit + strategy)" : " (audit-led)"}
          </h4>
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            {String(payload.routing_rule || "cannibalisation → refresh → quick wins → gaps → cleanup")}
          </p>
          <ol style={{ margin: "0 0 14px", paddingLeft: 18, fontSize: 13 }}>
            {combinedQueue.slice(0, 20).map((p, i) => (
              <li key={i} style={{ marginBottom: 4 }}>
                <span className="cs-chip">{String(p.action || "—")}</span>{" "}
                {String(p.title || p.keyword || p.url || "—")}
                <span style={{ color: "var(--muted)", fontSize: 11 }}>
                  {" "}
                  · {String(p.source || "")}
                </span>
              </li>
            ))}
          </ol>
        </>
      ) : null}

      {unmatched.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Unmatched gap keywords</h4>
          <ul className="missing-list">
            {unmatched.slice(0, 12).map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </>
      ) : null}

      {couldNot.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>What this audit could not assess</h4>
          <ul className="missing-list">
            {couldNot.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </>
      ) : null}

      {handoffs.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Handoffs</h4>
          <ul className="missing-list">
            {handoffs.map((h, i) => (
              <li key={i}>
                {String(h.concern || "—")} → <strong>{String(h.route_to || "")}</strong>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve audit
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
