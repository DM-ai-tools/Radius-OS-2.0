import { httpStatusLabel } from "../../lib/httpStatusLabel";

type LinkRow = {
  source_page: string;
  broken_url: string;
  status: string | number;
  suggested_fix?: string;
};

type ChainRow = {
  start_url: string;
  chain: string | string[];
  final_url: string;
  hops?: number;
};

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

export default function BrokenLinkCard({ payload, canAct, onAction }: Props) {
  const internal = (payload.internal || []) as LinkRow[];
  const external = (payload.external || []) as LinkRow[];
  const chains = (payload.redirect_chains || []) as ChainRow[];
  const fixes = (payload.quick_fixes || []) as string[];
  const showActions = canAct && Array.isArray(payload.actions) && payload.actions.length > 0;

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">{String(payload.title || "Broken Link Report")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Pages scanned: {String(payload.pages_scanned ?? "—")} · Links checked:{" "}
        {String(payload.total_links_checked ?? "—")} · Broken:{" "}
        <strong>{String(payload.broken_count ?? 0)}</strong> · Redirect chains:{" "}
        {String(payload.redirect_chain_count ?? 0)}
      </p>

      <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Internal broken links</h4>
      <LinkTable rows={internal} />

      <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>External broken links</h4>
      <LinkTable rows={external} />

      <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Redirect chains (3+ hops)</h4>
      {chains.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>None flagged.</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Start</th>
                <th>Chain</th>
                <th>Final</th>
              </tr>
            </thead>
            <tbody>
              {chains.map((c, i) => (
                <tr key={i}>
                  <td>{c.start_url}</td>
                  <td>{Array.isArray(c.chain) ? c.chain.join(" ") : c.chain}</td>
                  <td>{c.final_url}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {fixes.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 6px", fontSize: 13 }}>Quick fixes</h4>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {fixes.map((f, i) => (
              <li key={`${i}-${f}`}>{f}</li>
            ))}
          </ol>
        </>
      )}

      {showActions && (
        <div className="card-actions">
          <button className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve
          </button>
          <button className="btn btn-amber" onClick={() => onAction("edit")}>
            Edit
          </button>
          <button className="btn btn-danger" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

function LinkTable({ rows }: { rows: LinkRow[] }) {
  if (!rows.length) {
    return <p style={{ fontSize: 13, color: "var(--muted)" }}>None found.</p>;
  }
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Source page</th>
            <th>Broken URL</th>
            <th>Result</th>
            <th>Suggested fix</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.broken_url}-${i}`}>
              <td>{r.source_page}</td>
              <td style={{ wordBreak: "break-all" }}>{r.broken_url}</td>
              <td>
                <span className="status-pill fail">{httpStatusLabel(r.status)}</span>
              </td>
              <td>{r.suggested_fix || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
