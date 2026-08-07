import { PresentableValue } from "./PresentableValue";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

export default function OnPageSeoCard({ payload, canAct, onAction }: Props) {
  const title = (payload.title || {}) as { before?: string; after?: string };
  const meta = (payload.meta_description || {}) as { before?: string; after?: string };
  const headings = (payload.headings || {}) as { h1?: string; h2s?: string[]; notes?: string };
  const gaps = (payload.content_gaps || []) as string[];
  const links = (payload.internal_links || []) as { anchor?: string; to?: string }[];
  const schema = payload.schema_json_ld;
  const showActions = canAct && Array.isArray(payload.actions) && payload.actions.length > 0;

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">
        {String(payload.card_title || payload.page_name || "On-Page SEO Report")}
      </h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Target keyword: <strong>{String(payload.target_keyword || "—")}</strong>
        {payload.search_intent ? ` · Intent: ${String(payload.search_intent)}` : ""}
      </p>
      <div className="two-col" style={{ marginBottom: 12 }}>
        <div className="field-row">
          <div className="key">Current score</div>
          <div style={{ fontSize: 22, fontWeight: 700 }}>{String(payload.current_score ?? "—")}</div>
        </div>
        <div className="field-row">
          <div className="key">Optimized (est.)</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "var(--primary)" }}>
            {String(payload.optimized_score ?? "—")}
          </div>
        </div>
      </div>

      <h4 style={{ margin: "8px 0 4px", fontSize: 13 }}>Title tag</h4>
      <BeforeAfter before={title.before} after={title.after} />

      <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Meta description</h4>
      <BeforeAfter before={meta.before} after={meta.after} />

      <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Headings</h4>
      <div className="field-row">
        <div className="key">H1</div>
        <div>{headings.h1 || "—"}</div>
      </div>
      {(headings.h2s || []).map((h) => (
        <div key={h} className="field-row" style={{ fontSize: 13 }}>
          H2: {h}
        </div>
      ))}
      {headings.notes && (
        <p style={{ fontSize: 12, color: "var(--muted)" }}>{headings.notes}</p>
      )}

      {gaps.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Content gaps</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {gaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </>
      )}

      {links.length > 0 && (
        <>
          <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Internal linking</h4>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Anchor</th>
                  <th>To</th>
                </tr>
              </thead>
              <tbody>
                {links.map((l, i) => (
                  <tr key={i}>
                    <td>{l.anchor}</td>
                    <td>{l.to}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {schema != null && (
        <>
          <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Schema markup (JSON-LD)</h4>
          <div className="schema-block">
            <PresentableValue value={schema} />
          </div>
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

function BeforeAfter({ before, after }: { before?: string; after?: string }) {
  return (
    <div className="two-col">
      <div className="col-box">
        <div className="key">Before</div>
        <div style={{ fontSize: 13 }}>{before || "—"}</div>
      </div>
      <div className="col-box">
        <div className="key">After</div>
        <div style={{ fontSize: 13 }}>{after || "—"}</div>
      </div>
    </div>
  );
}
