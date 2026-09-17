import { useState } from "react";
import { api } from "../../api";
import { useAuth } from "../../auth";
import DraftDocument, { type DraftImage } from "./DraftDocument";

type Props = {
  clientId: string;
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string, edits?: Record<string, unknown>) => void;
  onDraftTopic?: (prompt: string) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

function sameTopic(a: unknown, b: unknown): boolean {
  const na = String(a || "")
    .replace(/^https?:\/\/[^/]+/i, "")
    .replace(/\/$/, "")
    .toLowerCase();
  const nb = String(b || "")
    .replace(/^https?:\/\/[^/]+/i, "")
    .replace(/\/$/, "")
    .toLowerCase();
  return Boolean(na) && Boolean(nb) && (na === nb || na.endsWith(nb) || nb.endsWith(na));
}

export default function ContentProductionCard({
  clientId,
  payload,
  canAct,
  onAction,
  onDraftTopic,
}: Props) {
  const { token } = useAuth();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewHtml, setPreviewHtml] = useState("");
  const [previewMeta, setPreviewMeta] = useState<Record<string, unknown> | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const briefs = asRows(payload.briefs);
  const drafts = asRows(payload.drafts);
  const held = asRows(payload.held_briefs);
  const queued = asRows(payload.queued_for_next_write);
  const choices = asRows(payload.topic_choices);
  const refusal = payload.write_refusal as Record<string, unknown> | undefined;
  const fallbackMd = String(payload.draft_markdown || "");
  const article =
    drafts[0] ||
    (fallbackMd
      ? {
          markdown: fallbackMd,
          title: payload.draft_title,
          images: payload.draft_images,
        }
      : null);
  const awaiting = Boolean(payload.awaiting_topic_selection) || (!article && choices.length > 0);
  const pickList = choices.length ? choices : briefs.filter((b) => b.writer_ready);

  async function openSitePreview() {
    if (!article?.markdown || !token) return;
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewHtml("");
    try {
      const res = await api.contentProductionSitePreview(token, clientId, {
        title: article.title ? String(article.title) : undefined,
        url: article.url ? String(article.url) : undefined,
        meta_description: article.meta_description ? String(article.meta_description) : undefined,
        keyword: article.keyword ? String(article.keyword) : undefined,
        markdown: String(article.markdown),
        images: Array.isArray(article.images)
          ? (article.images as Array<Record<string, unknown>>)
          : undefined,
        media_base: window.location.origin,
      });
      setPreviewHtml(res.preview_html || "");
      setPreviewMeta(res as unknown as Record<string, unknown>);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Could not build site preview");
    } finally {
      setPreviewLoading(false);
    }
  }

  const previewActions = article?.markdown ? (
    <button
      type="button"
      className="btn btn-secondary"
      disabled={previewLoading || !token}
      onClick={() => void openSitePreview()}
    >
      {previewLoading ? "Building preview…" : "Preview on client site"}
    </button>
  ) : null;

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Content Production")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client_name ? `${String(payload.client_name)} · ` : ""}
        {payload.primary_url ? String(payload.primary_url) : ""}
      </p>
      <div className="cs-funnel-strip">
        <span>
          Briefs <strong>{fmtNum(payload.brief_count ?? briefs.length)}</strong>
        </span>
        <span>
          Writer-ready <strong>{fmtNum(payload.writer_ready_count ?? pickList.length)}</strong>
        </span>
        <span>
          Drafts this run <strong>{fmtNum(payload.draft_count ?? drafts.length)}</strong>
        </span>
        <span>
          <strong>1 page / run</strong>
        </span>
      </div>
      {payload.note ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{String(payload.note)}</p>
      ) : null}
      {refusal && typeof refusal === "object" && refusal.reason ? (
        <p style={{ fontSize: 13, color: "var(--coral, #b42318)" }}>
          Write refused: {String(refusal.reason)}
          {refusal.route_to ? ` → ${String(refusal.route_to)}` : ""}
        </p>
      ) : null}

      {article ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Full draft</h4>
          <div className="cs-pillar-block draft-panel">
            {article.markdown ? (
              <DraftDocument
                markdown={String(article.markdown)}
                title={article.title ? String(article.title) : undefined}
                url={article.url ? String(article.url) : undefined}
                author={article.author ? String(article.author) : undefined}
                standing={article.author_standing ? String(article.author_standing) : undefined}
                status={article.status ? String(article.status) : undefined}
                differentiation={article.differentiation ? String(article.differentiation) : undefined}
                metaDescription={article.meta_description ? String(article.meta_description) : undefined}
                keyword={article.keyword ? String(article.keyword) : undefined}
                action={article.action ? String(article.action) : undefined}
                funnel={article.funnel ? String(article.funnel) : undefined}
                images={Array.isArray(article.images) ? (article.images as DraftImage[]) : undefined}
                relatedKeywords={
                  Array.isArray(article.related_keywords)
                    ? article.related_keywords.map((k) => String(k)).filter(Boolean)
                    : undefined
                }
              />
            ) : (
              <p style={{ fontSize: 13, color: "var(--coral, #b42318)" }}>
                Draft ran but the article body did not attach. Click the same topic again to rewrite.
              </p>
            )}
          </div>
          {canAct ? (
            <div className="card-actions" style={{ marginTop: 10 }}>
              {previewActions}
              <button type="button" className="btn btn-primary" onClick={() => onAction("approve")}>
                Approve this draft
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
                Reject
              </button>
            </div>
          ) : previewActions ? (
            <div className="card-actions" style={{ marginTop: 10 }}>
              {previewActions}
            </div>
          ) : null}
          {previewOpen ? (
            <div className="site-preview-backdrop" role="presentation" onClick={() => setPreviewOpen(false)}>
              <div
                className="site-preview-modal"
                role="dialog"
                aria-modal="true"
                aria-label="Client site preview"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="site-preview-toolbar">
                  <div>
                    <strong>Client site preview</strong>
                    <span className="site-preview-sub">
                      {previewLoading
                        ? " · measuring the live page, then Brandfetch only if that fails…"
                        : previewMeta?.design_source === "measured"
                          ? " · measured from the live page"
                          : previewMeta?.design_source === "wordpress" && previewMeta?.design_fallback === "brandfetch"
                          ? " · WordPress site, Brandfetch filled the gaps"
                          : previewMeta?.design_source === "wordpress"
                            ? " · styled from the live WordPress site"
                            : previewMeta?.design_source === "brandfetch" || previewMeta?.brand_applied
                              ? " · Brandfetch fallback"
                              : " · neutral styling — site design unavailable"}
                      {previewMeta?.reference_url ? ` · ref ${String(previewMeta.reference_url)}` : ""}
                    </span>
                  </div>
                  <button type="button" className="btn btn-ghost" onClick={() => setPreviewOpen(false)}>
                    Close
                  </button>
                </div>
                {previewError ? (
                  <p style={{ padding: 16, color: "var(--coral, #b42318)" }}>{previewError}</p>
                ) : previewLoading ? (
                  <p style={{ padding: 16, color: "var(--muted)" }}>Building preview…</p>
                ) : previewHtml ? (
                  <iframe
                    className="site-preview-frame"
                    title="Client site preview"
                    sandbox="allow-same-origin"
                    srcDoc={previewHtml}
                  />
                ) : null}
              </div>
            </div>
          ) : null}
        </>
      ) : null}

      {pickList.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>
            {awaiting ? "Pick a priority topic to draft" : "Draft another topic"}
          </h4>
          <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
            Click one — the full article is written for that URL only.
          </p>
          <div className="topic-pick-list">
            {pickList.slice(0, 8).map((c, i) => {
              const kw = String(c.keyword || c.title || "Topic");
              const url = String(c.url || c.path || "");
              const prompt = String(
                c.prompt ||
                  (url ? `Write the full draft for: ${kw} (${url})` : `Write the full draft for: ${kw}`),
              );
              const drafted = Boolean(
                article &&
                  (sameTopic(article.url, url) ||
                    String(article.keyword || "") === kw ||
                    String(article.title || "") === kw),
              );
              return (
                <button
                  key={`${url || kw}-${i}`}
                  type="button"
                  className={`topic-pick${drafted ? " topic-pick--done" : ""}`}
                  disabled={!onDraftTopic || drafted}
                  onClick={() => onDraftTopic?.(prompt)}
                >
                  <span className="topic-pick-rank">{String(c.rank || i + 1).padStart(2, "0")}</span>
                  <span className="topic-pick-body">
                    <strong>{kw}</strong>
                    {url ? <span className="topic-pick-url">{url}</span> : null}
                    <span className="topic-pick-meta">
                      {c.action ? String(c.action) : "create"}
                      {c.funnel ? ` · ${String(c.funnel)}` : ""}
                      {c.intent ? ` · ${String(c.intent)}` : ""}
                      {drafted ? " · drafted" : ""}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      <details className="brief-fold" open={!article}>
        <summary>Briefs ({briefs.length})</summary>
        {briefs.length ? (
          briefs.slice(0, 12).map((b, i) => (
            <div key={`${String(b.url)}-${i}`} className="cs-pillar-block">
              <div className="cs-pillar-head">
                <strong>{String(b.keyword || b.url || "Brief")}</strong>
                {b.writer_ready ? <span className="cs-chip">writer-ready</span> : <span className="cs-meta">draft</span>}
                {b.action ? <span className="cs-meta">{String(b.action)}</span> : null}
              </div>
              {b.url ? (
                <p className="cs-supporting" style={{ marginTop: 4 }}>
                  {String(b.url)}
                </p>
              ) : null}
              {b.differentiation ? (
                <p style={{ fontSize: 12, margin: "4px 0 0" }}>Diff: {String(b.differentiation).slice(0, 160)}</p>
              ) : null}
            </div>
          ))
        ) : (
          <p style={{ fontSize: 13, color: "var(--muted)" }}>No briefs yet</p>
        )}
      </details>

      {queued.length && !awaiting ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Queued for next write (1 page / run)</h4>
          <ul className="missing-list">
            {queued.slice(0, 10).map((q, i) => (
              <li key={`${String(q.url)}-${i}`}>
                {String(q.keyword || q.url)} — click that topic to draft it next
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {held.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Held / refused</h4>
          <ul className="missing-list">
            {held.slice(0, 12).map((h, i) => (
              <li key={`${String(h.url)}-${i}`}>
                {String(h.keyword || h.url || "item")} —{" "}
                {Array.isArray(h.reason) ? h.reason.join("; ") : String(h.reason || "")}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
