import { WordPressStatus } from "../../api";
import { PresentableValue } from "./PresentableValue";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
  /** Live status, fetched by ChatPage — not the last-run snapshot in the payload. */
  wordpressStatus?: WordPressStatus | null;
  onConnectWordPress?: () => void;
  onDisconnectWordPress?: () => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

export default function PublishingCard({
  payload,
  canAct,
  onAction,
  wordpressStatus,
  onConnectWordPress,
  onDisconnectWordPress,
}: Props) {
  const queue = asRows(payload.publish_queue);
  const checklist = Array.isArray(payload.publish_checklist)
    ? (payload.publish_checklist as string[])
    : [];
  const qa = asRows(payload.qa_checklist);
  const gsc = asRows(payload.gsc_recrawl);
  const indexnow =
    payload.indexnow_preview || payload.indexnow_payload || payload.indexnow;
  const indexnowObj =
    indexnow && typeof indexnow === "object" ? (indexnow as Record<string, unknown>) : null;

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Publishing & Indexation")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client_name ? `${String(payload.client_name)} · ` : ""}
        {payload.primary_url ? String(payload.primary_url) : ""}
        {payload.cms_mode ? ` · CMS: ${String(payload.cms_mode)}` : ""}
      </p>
      {payload.note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.note)}</p>
      ) : null}

      {wordpressStatus !== undefined ? (
        <div className="card-actions" style={{ marginTop: 0, marginBottom: 14 }}>
          {wordpressStatus?.connected ? (
            <>
              <span className="cs-chip" style={{ background: "var(--secondary-bg)" }}>
                Connected as {wordpressStatus.wp_user || wordpressStatus.username} to{" "}
                {wordpressStatus.base_url}
              </span>
              {onDisconnectWordPress ? (
                <button type="button" className="btn btn-ghost" onClick={onDisconnectWordPress}>
                  Disconnect
                </button>
              ) : null}
            </>
          ) : (
            <>
              <span className="cs-chip">WordPress not connected</span>
              {onConnectWordPress ? (
                <button type="button" className="btn btn-primary" onClick={onConnectWordPress}>
                  Connect WordPress
                </button>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Publish queue (simulated)</h4>
      {queue.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>URL</th>
                <th style={{ padding: "6px 8px" }}>Title</th>
                <th style={{ padding: "6px 8px" }}>Keyword</th>
                <th style={{ padding: "6px 8px" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {queue.slice(0, 25).map((q, i) => (
                <tr key={`${String(q.url)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(q.url || "—")}</td>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(q.title || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{String(q.keyword || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="cs-chip">{String(q.status || "simulated")}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No publish queue items</p>
      )}

      {indexnowObj ? (
        <>
          <h4 style={{ marginBottom: 8 }}>IndexNow payload (preview)</h4>
          <div className="schema-block" style={{ marginBottom: 14 }}>
            <PresentableValue value={indexnowObj} />
          </div>
        </>
      ) : null}

      {gsc.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>GSC recrawl queue</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                  <th style={{ padding: "6px 8px" }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {gsc.slice(0, 20).map((g, i) => (
                  <tr key={`${String(g.url)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(g.url || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(g.action || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(g.status || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {checklist.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Publish checklist</h4>
          <ul className="missing-list">
            {checklist.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}

      {qa.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>QA checklist</h4>
          <ul className="missing-list">
            {qa.map((q, i) => (
              <li key={i}>
                {String(q.item || q.check || "—")}
                {q.status ? (
                  <span style={{ color: "var(--muted)" }}> — {String(q.status)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve publishing package
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
