import { useEffect, useMemo, useState } from "react";
import { api, downloadHtmlFile } from "../../api";
import { useAuth } from "../../auth";
import DraftDocument, { type DraftImage } from "./DraftDocument";

function designedPreviewFilename(article: Record<string, unknown> | null): string {
  const raw = String(article?.title || article?.keyword || "draft-site-preview");
  const slug = raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return `${slug || "draft-site-preview"}-designed-preview.html`;
}

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

function normPath(value: unknown): string {
  return String(value || "")
    .replace(/^https?:\/\/[^/]+/i, "")
    .replace(/\/$/, "")
    .toLowerCase();
}

function sameTopic(a: unknown, b: unknown): boolean {
  const na = normPath(a);
  const nb = normPath(b);
  return Boolean(na) && Boolean(nb) && (na === nb || na.endsWith(nb) || nb.endsWith(na));
}

function workKind(row: Record<string, unknown>): "optimize" | "create" {
  const action = String(row.action || "").toLowerCase();
  const label = String(row.work_label || "").toLowerCase();
  if (
    action === "refresh" ||
    label.includes("optimize") ||
    label.includes("refresh") ||
    label.includes("optimise")
  ) {
    return "optimize";
  }
  return "create";
}

function findBrief(
  briefs: Array<Record<string, unknown>>,
  option: { url: string; keyword: string },
): Record<string, unknown> | null {
  const urlKey = normPath(option.url);
  const kwKey = option.keyword.trim().toLowerCase();
  for (const brief of briefs) {
    const bUrl = normPath(brief.url || brief.path);
    if (urlKey && bUrl && (bUrl === urlKey || bUrl.endsWith(urlKey) || urlKey.endsWith(bUrl))) {
      return brief;
    }
  }
  for (const brief of briefs) {
    const bKw = String(brief.keyword || brief.title || "")
      .trim()
      .toLowerCase();
    if (kwKey && bKw && (bKw === kwKey || kwKey.includes(bKw) || bKw.includes(kwKey))) {
      return brief;
    }
  }
  return null;
}

function outlineTitles(brief: Record<string, unknown>): string[] {
  const outline = Array.isArray(brief.outline) ? brief.outline : [];
  return outline
    .map((row) => {
      if (typeof row === "string") return row;
      if (row && typeof row === "object") {
        return String((row as Record<string, unknown>).title || "").trim();
      }
      return "";
    })
    .filter(Boolean)
    .slice(0, 8);
}

function asKeywordList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const term =
      typeof item === "string"
        ? item.trim()
        : item && typeof item === "object"
          ? String((item as Record<string, unknown>).keyword || "").trim()
          : "";
    const key = term.toLowerCase();
    if (!term || seen.has(key)) continue;
    seen.add(key);
    out.push(term);
  }
  return out;
}

function pageTypeLabel(raw: unknown): string {
  const key = String(raw || "")
    .trim()
    .toLowerCase();
  if (!key) return "—";
  const labels: Record<string, string> = {
    blog: "Blog",
    article: "Blog",
    post: "Blog",
    service: "Service page",
    "service page": "Service page",
    subservice: "Sub-service page",
    sub_service: "Sub-service page",
    landing: "Landing page",
    "landing page": "Landing page",
    faq: "FAQ",
    guide: "Guide",
    comparison: "Comparison",
    listicle: "Listicle",
    tool: "Tool",
  };
  return labels[key] || String(raw);
}

function secondaryKeywords(
  brief: Record<string, unknown> | null,
  option: Record<string, unknown>,
  primary: string,
): string[] {
  const fromPlacement = asKeywordList(
    Array.isArray(brief?.keyword_placement)
      ? (brief!.keyword_placement as Array<Record<string, unknown>>).map((row) => row.keyword)
      : [],
  );
  const fromFaq = asKeywordList(
    Array.isArray(brief?.faq)
      ? (brief!.faq as Array<Record<string, unknown>>).map((row) => {
          const q = String(row.q || row.question || "").trim();
          // Keep short FAQ topics as related keyword stand-ins when the brief
          // never stored secondary_keywords.
          if (!q) return "";
          return q.replace(/\?+$/, "").trim();
        })
      : [],
  );
  const combined = [
    ...asKeywordList(brief?.secondary_keywords),
    ...asKeywordList(brief?.supporting_keywords),
    ...asKeywordList(brief?.related_keywords),
    ...asKeywordList(option.secondary_keywords),
    ...asKeywordList(option.supporting_keywords),
    ...fromPlacement,
    ...fromFaq,
  ];
  const primaryKey = primary.trim().toLowerCase();
  return combined.filter((term) => term.toLowerCase() !== primaryKey).slice(0, 8);
}

function contentBriefText(brief: Record<string, unknown> | null, option: { kind: string }): string {
  if (!brief) {
    return option.kind === "optimize"
      ? "Brief not on file yet. Drafting will build the brief, then refresh the live page."
      : "Brief not on file yet. Drafting will build the brief, then write the new page.";
  }
  const meta = String(brief.meta_description || "").trim();
  const goal = String(brief.content_goal || "").trim();
  const outcomes = asKeywordList(
    brief.required_coverage && typeof brief.required_coverage === "object"
      ? (brief.required_coverage as Record<string, unknown>).outcomes
      : [],
  );
  // Never reuse differentiation/angle here — that field is shown separately.
  const parts = [
    meta,
    goal ? `Goal: ${goal}.` : "",
    outcomes.length ? `Cover: ${outcomes.slice(0, 3).join("; ")}.` : "",
  ].filter(Boolean);
  return parts.join(" ") || "No short content brief on file for this page yet.";
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

  const draftableOptions = useMemo(
    () =>
      pickList.map((c, i) => {
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
        const kind = workKind(c);
        return {
          key: `${url || kw}-${i}`,
          rank: String(c.rank || i + 1),
          keyword: kw,
          url,
          prompt,
          drafted,
          kind,
          workLabel: String(
            c.work_label || (kind === "optimize" ? "Optimize page" : "Create new page"),
          ),
          funnel: c.funnel ? String(c.funnel) : "",
          intent: c.intent ? String(c.intent) : "",
          row: c,
        };
      }),
    [pickList, article],
  );

  const firstOpenKey = draftableOptions.find((o) => !o.drafted)?.key || "";
  const [selectedKey, setSelectedKey] = useState(firstOpenKey);
  useEffect(() => {
    if (!draftableOptions.some((o) => o.key === selectedKey)) {
      setSelectedKey(firstOpenKey);
    }
  }, [draftableOptions, firstOpenKey, selectedKey]);

  const selectedOption =
    draftableOptions.find((o) => o.key === selectedKey) ||
    draftableOptions.find((o) => !o.drafted) ||
    null;
  const selectedBrief = useMemo(() => {
    if (!selectedOption) return null;
    const matched = findBrief(briefs, selectedOption);
    if (matched) return { ...selectedOption.row, ...matched };
    if (
      selectedOption.row.meta_description ||
      selectedOption.row.differentiation ||
      selectedOption.row.outline
    ) {
      return selectedOption.row;
    }
    return null;
  }, [selectedOption, briefs]);
  const outline = selectedBrief ? outlineTitles(selectedBrief) : [];

  async function buildSitePreview(opts?: { embedImages?: boolean }): Promise<string | null> {
    if (!article?.markdown || !token) return null;
    setPreviewLoading(true);
    setPreviewError(null);
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
        embed_images: Boolean(opts?.embedImages),
      });
      const html = res.preview_html || "";
      setPreviewHtml(html);
      setPreviewMeta(res as unknown as Record<string, unknown>);
      return html || null;
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Could not build site preview");
      return null;
    } finally {
      setPreviewLoading(false);
    }
  }

  async function openSitePreview() {
    if (!article?.markdown || !token) return;
    setPreviewOpen(true);
    setPreviewHtml("");
    await buildSitePreview();
  }

  async function downloadDesignedHtml() {
    if (!article?.markdown || !token) return;
    setPreviewOpen(true);
    setPreviewHtml("");
    const built = await buildSitePreview({ embedImages: true });
    if (built) downloadHtmlFile(built, designedPreviewFilename(article));
  }

  const previewActions = article?.markdown ? (
    <>
      <button
        type="button"
        className="btn btn-secondary"
        disabled={previewLoading || !token}
        onClick={() => void openSitePreview()}
      >
        {previewLoading ? "Building preview…" : "Preview on client site"}
      </button>
      <button
        type="button"
        className="btn btn-ghost"
        disabled={previewLoading || !token}
        onClick={() => void downloadDesignedHtml()}
        title="Download the self-contained HTML with client design applied"
      >
        {previewLoading ? "Preparing HTML…" : "Download designed HTML"}
      </button>
    </>
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
          Pages to draft <strong>{fmtNum(draftableOptions.length)}</strong>
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
                Draft ran but the article body did not attach. Select the same page again to rewrite.
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
                        : previewMeta?.previous_page_url
                          ? " · design copied from previous plan page"
                          : previewMeta?.design_source === "measured"
                            ? " · measured from the live page"
                            : previewMeta?.design_source === "wordpress" && previewMeta?.design_fallback === "brandfetch"
                            ? " · WordPress site, Brandfetch filled the gaps"
                            : previewMeta?.design_source === "wordpress"
                              ? " · styled from the live WordPress site"
                              : previewMeta?.design_source === "brandfetch" || previewMeta?.brand_applied
                                ? " · Brandfetch fallback"
                                : " · neutral styling — site design unavailable"}
                      {previewMeta?.layout_mirrored_by_claude ? " · Claude mirrored layout" : ""}
                      {previewMeta?.previous_page_url
                        ? ` · from ${String(previewMeta.previous_page_url)}`
                        : previewMeta?.reference_url
                          ? ` · ref ${String(previewMeta.reference_url)}`
                          : ""}
                    </span>
                  </div>
                  <div className="site-preview-toolbar-actions">
                    <button
                      type="button"
                      className="btn btn-secondary"
                      disabled={previewLoading || !previewHtml}
                      onClick={() => void downloadDesignedHtml()}
                    >
                      Download HTML
                    </button>
                    <button type="button" className="btn btn-ghost" onClick={() => setPreviewOpen(false)}>
                      Close
                    </button>
                  </div>
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

      {draftableOptions.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>
            {awaiting ? "Select a page to draft" : "Draft another page"}
          </h4>
          <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
            Choose a page from the dropdown to review its brief, then draft it
            (one page per run).
          </p>
          <div className="topic-pick-select">
            <label className="topic-pick-select-label" htmlFor="topic-pick-dropdown">
              Page
            </label>
            <select
              id="topic-pick-dropdown"
              className="topic-pick-dropdown"
              value={selectedOption?.key || ""}
              onChange={(e) => setSelectedKey(e.target.value)}
            >
              {!selectedOption ? <option value="">Select a page…</option> : null}
              {draftableOptions.map((o) => (
                <option key={o.key} value={o.key} disabled={o.drafted}>
                  {`${o.kind === "optimize" ? "[Optimize]" : "[Create]"} ${String(o.rank).padStart(2, "0")}. ${o.keyword}${
                    o.url ? ` — ${o.url}` : ""
                  }${o.drafted ? " (drafted)" : ""}`}
                </option>
              ))}
            </select>

            {selectedOption ? (
              <div className="topic-brief-panel">
                <div className="topic-brief-head">
                  <div>
                    <strong>{selectedOption.keyword}</strong>
                    {selectedOption.url ? (
                      <div className="topic-pick-url">{selectedOption.url}</div>
                    ) : null}
                  </div>
                  <span
                    className={`topic-kind-tag topic-kind-tag--${selectedOption.kind}`}
                  >
                    {selectedOption.kind === "optimize"
                      ? "Optimize existing page"
                      : "Create new page"}
                  </span>
                </div>

                <div className="topic-brief-meta">
                  <span>{selectedOption.workLabel}</span>
                  {selectedOption.funnel ? <span>· {selectedOption.funnel}</span> : null}
                  {selectedOption.intent ? <span>· {selectedOption.intent}</span> : null}
                  {selectedOption.drafted ? <span>· drafted</span> : null}
                </div>

                {selectedBrief || selectedOption ? (
                  <div className="topic-brief-body">
                    <p>
                      <span className="topic-brief-label">Page type</span>
                      {pageTypeLabel(
                        selectedBrief?.page_type ||
                          selectedBrief?.content_type ||
                          selectedOption.row.page_type ||
                          selectedOption.row.content_type,
                      )}
                    </p>
                    <p>
                      <span className="topic-brief-label">Primary keyword</span>
                      {String(
                        selectedBrief?.primary_keyword ||
                          selectedBrief?.keyword ||
                          selectedOption.row.primary_keyword ||
                          selectedOption.keyword,
                      )}
                    </p>
                    <div>
                      <span className="topic-brief-label">Secondary keywords</span>
                      {(() => {
                        const primary = String(
                          selectedBrief?.primary_keyword ||
                            selectedBrief?.keyword ||
                            selectedOption.keyword,
                        );
                        const secondary = secondaryKeywords(
                          selectedBrief,
                          selectedOption.row,
                          primary,
                        );
                        return secondary.length ? (
                          <div className="topic-keyword-chips">
                            {secondary.map((term) => (
                              <span key={term} className="topic-keyword-chip">
                                {term}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span style={{ color: "var(--muted)" }}>None listed yet</span>
                        );
                      })()}
                    </div>
                    <p>
                      <span className="topic-brief-label">Content brief</span>
                      {contentBriefText(selectedBrief, selectedOption)}
                    </p>
                    {selectedBrief?.differentiation ? (
                      <p>
                        <span className="topic-brief-label">Angle</span>
                        {String(selectedBrief.differentiation)}
                      </p>
                    ) : null}
                    {outline.length ? (
                      <div>
                        <span className="topic-brief-label">Outline</span>
                        <ul className="topic-brief-outline">
                          {outline.map((title) => (
                            <li key={title}>{title}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <p style={{ fontSize: 13, color: "var(--muted)", margin: "8px 0 0" }}>
                    No brief on file yet for this page. Drafting it will build the brief
                    first, then write the page.
                  </p>
                )}

                <div className="card-actions" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!onDraftTopic || selectedOption.drafted}
                    onClick={() => onDraftTopic?.(selectedOption.prompt)}
                  >
                    {selectedOption.drafted
                      ? "Already drafted"
                      : selectedOption.kind === "optimize"
                        ? "Draft optimized page"
                        : "Draft new page"}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </>
      ) : null}

      {queued.length && !awaiting ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Queued for next write (1 page / run)</h4>
          <ul className="missing-list">
            {queued.slice(0, 10).map((q, i) => (
              <li key={`${String(q.url)}-${i}`}>
                {String(q.keyword || q.url)} — select that page above to draft it next
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
