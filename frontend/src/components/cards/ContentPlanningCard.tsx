type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

function asStringList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).filter(Boolean);
}

export default function ContentPlanningCard({ payload, canAct, onAction }: Props) {
  const pages = asRows(payload.pages).length ? asRows(payload.pages) : asRows(payload.roadmap);
  const excluded = asRows(payload.excluded);
  const locked = payload.locked === true;

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Content Planning")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client_name ? `${String(payload.client_name)} · ` : ""}
        {payload.primary_url ? String(payload.primary_url) : ""}
      </p>
      <div className="cs-funnel-strip">
        <span>
          {locked ? <strong>Locked</strong> : <strong>Unlocked</strong>}
        </span>
        <span>
          Merged <strong>{fmtNum(payload.planned_count ?? pages.length)}</strong>
        </span>
        <span>
          Create <strong>{fmtNum(payload.create_count)}</strong>
        </span>
        <span>
          Refresh <strong>{fmtNum(payload.refresh_count)}</strong>
        </span>
        <span>
          Retire <strong>{fmtNum(payload.retire_count)}</strong>
        </span>
        <span>
          Keep <strong>{fmtNum(payload.no_action_count)}</strong>
        </span>
        <span>
          Excluded <strong>{fmtNum(payload.excluded_count ?? excluded.length)}</strong>
        </span>
      </div>
      {payload.lock_reason ? (
        <p style={{ fontSize: 13, color: "var(--coral, #b42318)" }}>{String(payload.lock_reason)}</p>
      ) : null}
      {payload.note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.note)}</p>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Merged pages (brief create/refresh in rank order)</h4>
      {pages.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>#</th>
                <th style={{ padding: "6px 8px" }}>URL</th>
                <th style={{ padding: "6px 8px" }}>Keyword</th>
                <th style={{ padding: "6px 8px" }}>Action</th>
                <th style={{ padding: "6px 8px" }}>Disposition</th>
                <th style={{ padding: "6px 8px" }}>Tier</th>
                <th style={{ padding: "6px 8px" }}>Flags</th>
              </tr>
            </thead>
            <tbody>
              {pages.slice(0, 40).map((r, i) => (
                <tr key={`${String(r.url_n || r.url || r.path)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(r.priority_rank ?? i + 1)}</td>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                    {String(r.title || r.url_n || r.url || "—")}
                    <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>
                      {String(r.url_n || r.path || r.url || "")}
                    </div>
                  </td>
                  <td style={{ padding: "6px 8px" }}>{String(r.primary_keyword || r.keyword || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="cs-chip">{String(r.action || "—")}</span>
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {String(r.disposition || "—")}
                    {r.disposition_reason ? (
                      <div style={{ color: "var(--muted)" }}>{String(r.disposition_reason).slice(0, 80)}</div>
                    ) : null}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{String(r.priority_tier || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {r.cannibal_conflict ? "cannibal " : ""}
                    {asStringList(r.flags).join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No merged pages — check excluded rows</p>
      )}

      {excluded.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Excluded — route upstream, do not drop</h4>
          <ul className="missing-list">
            {excluded.slice(0, 20).map((s, i) => (
              <li key={`${String(s.url_n)}-${i}`}>
                <strong>{String(s.source_pack || "pack")}</strong>
                {s.url_n ? ` · ${String(s.url_n)}` : s.keyword ? ` · ${String(s.keyword)}` : ""} —{" "}
                {String(s.reason || "")}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onAction("approve")}
            disabled={!locked}
          >
            Approve locked roadmap
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
