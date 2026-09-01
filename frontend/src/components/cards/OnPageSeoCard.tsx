import { PresentableValue } from "./PresentableValue";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function ba(v: unknown): { before?: string; after?: string } {
  if (v && typeof v === "object") return v as { before?: string; after?: string };
  return {};
}

export default function OnPageSeoCard({ payload, canAct, onAction }: Props) {
  const pagesRaw = asRows(payload.pages);
  const pages = pagesRaw.length ? pagesRaw : asRows(payload.queue);
  const multi = pages.length > 0;
  const title = ba(typeof payload.title === "object" ? payload.title : undefined);
  const meta = ba(payload.meta_description);
  const headings = (payload.headings || {}) as { h1?: string; h2s?: string[]; notes?: string };
  const gaps = (payload.content_gaps || []) as string[];
  const links = asRows(payload.internal_links);
  const schemaItems = asRows(payload.schema_items);
  const schema = payload.schema_json_ld;
  const firstSchema =
    schema ??
    (schemaItems[0]?.json_ld != null ? schemaItems[0].json_ld : pages[0]?.schema_json_ld);
  const heading =
    (typeof payload.card_title === "string" && payload.card_title) ||
    (typeof payload.title === "string" && payload.title) ||
    (typeof payload.page_name === "string" && payload.page_name) ||
    "On-Page SEO Report";

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{heading}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client_name ? `${String(payload.client_name)} · ` : ""}
        {payload.primary_url ? String(payload.primary_url) : ""}
        {payload.target_keyword ? (
          <>
            {" "}
            · Target keyword: <strong>{String(payload.target_keyword)}</strong>
          </>
        ) : null}
        {payload.search_intent ? ` · Intent: ${String(payload.search_intent)}` : ""}
        {multi ? ` · ${pages.length} pages` : ""}
      </p>
      {payload.note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.note)}</p>
      ) : null}

      {!multi && (payload.current_score != null || payload.optimized_score != null) ? (
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
      ) : null}

      {multi ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Page queue</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table
              className="kw-report-table"
              style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
            >
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                  <th style={{ padding: "6px 8px" }}>Keyword</th>
                  <th style={{ padding: "6px 8px" }}>Title before → after</th>
                  <th style={{ padding: "6px 8px" }}>H1</th>
                  <th style={{ padding: "6px 8px" }}>Meta after</th>
                  <th style={{ padding: "6px 8px" }}>Schema</th>
                </tr>
              </thead>
              <tbody>
                {pages.slice(0, 20).map((p, i) => {
                  const t = ba(p.title);
                  const m = ba(p.meta_description);
                  const h =
                    p.headings && typeof p.headings === "object"
                      ? (p.headings as Record<string, unknown>)
                      : {};
                  const types = Array.isArray(p.schema_types)
                    ? (p.schema_types as string[]).join(", ")
                    : "";
                  return (
                    <tr key={`${String(p.url || p.path)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>
                        {String(p.url || p.path || "—")}
                      </td>
                      <td style={{ padding: "6px 8px" }}>{String(p.keyword || "—")}</td>
                      <td style={{ padding: "6px 8px" }}>
                        <div style={{ color: "var(--muted)", fontSize: 11 }}>{t.before || "—"}</div>
                        <div style={{ fontWeight: 600 }}>{t.after || "—"}</div>
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        {String(p.h1 || h.h1 || "—")}
                      </td>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>{m.after || "—"}</td>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>{types || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
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
        </>
      )}

      <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Internal linking</h4>
      {payload.internal_linking &&
      typeof payload.internal_linking === "object" &&
      (payload.internal_linking as Record<string, unknown>).note ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
          {String((payload.internal_linking as Record<string, unknown>).note)}
        </p>
      ) : null}
      {links.length > 0 ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table
            className="kw-report-table"
            style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
          >
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>From</th>
                <th style={{ padding: "6px 8px" }}>To</th>
                <th style={{ padding: "6px 8px" }}>Anchor</th>
                <th style={{ padding: "6px 8px" }}>Status</th>
                <th style={{ padding: "6px 8px" }}>Reason</th>
              </tr>
            </thead>
            <tbody>
              {links.slice(0, 30).map((l, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {String(l.from || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {String(l.to || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{String(l.anchor || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {String(l.target_status || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(l.reason || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          No internal link suggestions yet — ensure Site Architecture has a URL tree and
          Content Planning is locked, then re-run On-Page SEO.
        </p>
      )}
      {asRows(payload.orphans).length > 0 ? (
        <>
          <h4 style={{ margin: "8px 0 4px", fontSize: 13 }}>Orphan pages</h4>
          <ul style={{ margin: "0 0 12px", paddingLeft: 18, fontSize: 12 }}>
            {asRows(payload.orphans)
              .slice(0, 8)
              .map((o, i) => (
                <li key={i}>
                  {String(o.url || "—")} ← suggest from {String(o.suggested_from || "/")}
                </li>
              ))}
          </ul>
        </>
      ) : null}

      {(firstSchema != null || schemaItems.length > 0) && (
        <>
          <h4 style={{ margin: "12px 0 4px", fontSize: 13 }}>Schema markup (JSON-LD)</h4>
          {schemaItems.length > 1 ? (
            <div style={{ overflowX: "auto", marginBottom: 8 }}>
              <table
                className="kw-report-table"
                style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
              >
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                    <th style={{ padding: "6px 8px" }}>URL</th>
                    <th style={{ padding: "6px 8px" }}>Types</th>
                  </tr>
                </thead>
                <tbody>
                  {schemaItems.slice(0, 12).map((s, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(s.url || "—")}</td>
                      <td style={{ padding: "6px 8px" }}>
                        {Array.isArray(s.types) ? (s.types as string[]).join(", ") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {firstSchema != null && (
            <div className="schema-block">
              <PresentableValue value={firstSchema} />
            </div>
          )}
        </>
      )}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
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
