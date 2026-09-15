import { useMemo, useState } from "react";
import { httpStatusLabel, httpStatusTone } from "../../lib/httpStatusLabel";
import { FieldGrid, MetricGrid, PresentableValue, humanLabel } from "./PresentableValue";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

type SitemapPage = {
  url?: string;
  path?: string;
  title?: string;
  status?: number | string;
  cluster?: string;
  cluster_label?: string;
  depth?: number;
  cdd_focus?: boolean;
};

type SitemapSection = {
  cluster?: string;
  label?: string;
  count?: number;
  pages?: SitemapPage[];
};

type SiteSitemap = {
  url_count?: number;
  shown_count?: number;
  truncated?: boolean;
  sources?: string[];
  sections?: SitemapSection[];
  pages?: SitemapPage[];
  page_inventory_method?: string;
  page_inventory_stats?: Record<string, unknown>;
};

const META_KEYS = new Set([
  "status",
  "severity",
  "message",
  "reason",
  "sparkline",
  "note",
  "spam_pre_triage_note",
]);

function numTone(n: unknown, warnAt: number, badAt: number): "ok" | "warn" | "bad" | "neutral" {
  const v = Number(n);
  if (!Number.isFinite(v)) return "neutral";
  if (v >= badAt) return "bad";
  if (v >= warnAt) return "warn";
  return "ok";
}

function ClientSiteSitemap({
  sitemap,
  sampleUrls,
}: {
  sitemap?: SiteSitemap | null;
  sampleUrls?: string[];
}) {
  const [open, setOpen] = useState(true);
  const sections = useMemo((): SitemapSection[] => {
    if (sitemap && Array.isArray(sitemap.sections) && sitemap.sections.length) {
      return sitemap.sections;
    }
    if (Array.isArray(sitemap?.pages) && sitemap!.pages!.length) {
      return [
        {
          label: "All pages",
          count: sitemap!.pages!.length,
          pages: sitemap!.pages,
        },
      ];
    }
    if (sampleUrls?.length) {
      return [
        {
          label: "Discovered URLs",
          count: sampleUrls.length,
          pages: sampleUrls.map((url) => ({ url, path: url, title: url } as SitemapPage)),
        },
      ];
    }
    return [];
  }, [sitemap, sampleUrls]);

  if (!sections.length) return null;

  const total = Number(sitemap?.url_count || sitemap?.shown_count || sampleUrls?.length || 0);
  const sources = Array.isArray(sitemap?.sources) ? sitemap!.sources! : [];

  return (
    <div className="report-section">
      <div className="cs-pillar-head" style={{ marginBottom: 8 }}>
        <h4 style={{ margin: 0 }}>Client website sitemap</h4>
        <button type="button" className="btn btn-ghost" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : "Show"}
        </button>
      </div>
      <p className="field-hint" style={{ marginTop: 0 }}>
        Current live-site URL inventory (crawl / XML sitemap / SEO audit / Perplexity
        page inventory)
        {total ? ` · ${total} URL${total === 1 ? "" : "s"}` : ""}
        {sources.length ? ` · source: ${sources.join(", ")}` : ""}
        {sitemap?.truncated ? " · list truncated for display" : ""}.
      </p>
      {sitemap?.page_inventory_method ? (
        <p className="field-hint" style={{ marginTop: 0 }}>
          Method: {String(sitemap.page_inventory_method).slice(0, 280)}
          {String(sitemap.page_inventory_method).length > 280 ? "…" : ""}
        </p>
      ) : null}
      {open ? (
        <div className="site-sitemap">
          {sections.map((section) => (
            <div key={String(section.cluster || section.label)} className="site-sitemap-section">
              <div className="site-sitemap-section-label">
                <strong>{section.label || section.cluster || "Pages"}</strong>
                <span className="cs-meta">{section.count ?? section.pages?.length ?? 0}</span>
              </div>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Path</th>
                      <th>Title</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(section.pages || []).slice(0, 80).map((page, i) => {
                      const tone = httpStatusTone(page.status);
                      const badgeTone =
                        page.status == null
                          ? "info"
                          : tone === "pass"
                            ? "info"
                            : tone === "warning"
                              ? "warning"
                              : "critical";
                      const indent = Math.min(Number(page.depth) || 0, 6);
                      return (
                        <tr key={`${page.url || page.path}-${i}`}>
                          <td className="url-cell">
                            <span style={{ paddingLeft: indent * 12 }}>
                              {page.path || page.url || "—"}
                              {page.cdd_focus ? (
                                <span className="cs-chip" style={{ marginLeft: 6 }}>
                                  CDD
                                </span>
                              ) : null}
                            </span>
                          </td>
                          <td>{page.title || "—"}</td>
                          <td>
                            {page.status == null ? (
                              <span className="severity info">listed</span>
                            ) : (
                              <span className={`severity ${badgeTone}`}>
                                {httpStatusLabel(page.status)}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function TechnicalTab({
  data,
  sitemap,
  sampleUrls,
}: {
  data: Record<string, unknown>;
  sitemap?: SiteSitemap | null;
  sampleUrls?: string[];
}) {
  const broken = (data.broken_links || {}) as Record<string, unknown>;
  const samples = (data.status_samples || []) as { url?: string; status?: number | string }[];
  const changes = (data.notable_changes || []) as unknown[];
  const tabSitemap = (data.site_sitemap as SiteSitemap | undefined) || sitemap;

  return (
    <div className="audit-panel">
      <MetricGrid
        items={[
          {
            label: "Unique pages",
            value: data.pages_found ?? tabSitemap?.url_count,
            tone: Number(data.pages_found ?? tabSitemap?.url_count) <= 1 ? "warn" : "neutral",
          },
          { label: "Indexable", value: data.indexable, tone: "ok" },
          {
            label: "Redirects",
            value: data.redirects,
            tone: numTone(data.redirects, 5, 20),
          },
          {
            label: "Redirect chains",
            value: data.redirect_chains,
            tone: numTone(data.redirect_chains, 1, 3),
          },
          {
            label: "Canonical issues",
            value: data.canonical_issues,
            tone: numTone(data.canonical_issues, 1, 5),
          },
          {
            label: "Broken links",
            value: broken.broken_count ?? data.broken_count,
            tone: numTone(broken.broken_count ?? data.broken_count, 1, 10),
          },
        ]}
      />
      {data.note ? (
        <p className="field-hint" style={{ marginTop: 10 }}>
          {String(data.note)}
        </p>
      ) : null}
      {data.error ? (
        <p className="field-hint" style={{ marginTop: 6, color: "var(--coral)" }}>
          Crawl error: {String(data.error)}
        </p>
      ) : null}

      <ClientSiteSitemap sitemap={tabSitemap} sampleUrls={sampleUrls} />

      {(broken.pages_scanned != null || broken.redirect_chain_count != null) && (
        <div className="report-section">
          <h4>Broken link scan</h4>
          <MetricGrid
            items={[
              { label: "Pages scanned", value: broken.pages_scanned },
              {
                label: "Broken count",
                value: broken.broken_count,
                tone: numTone(broken.broken_count, 1, 10),
              },
              {
                label: "Redirect chains",
                value: broken.redirect_chain_count,
                tone: numTone(broken.redirect_chain_count, 1, 3),
              },
            ]}
          />
        </div>
      )}

      {samples.length > 0 && (
        <div className="report-section">
          <h4>Page checks</h4>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {samples.slice(0, 15).map((s, i) => {
                  const tone = httpStatusTone(s.status);
                  const badgeTone =
                    tone === "pass" ? "info" : tone === "warning" ? "warning" : "critical";
                  return (
                    <tr key={`${s.url}-${i}`}>
                      <td className="url-cell">{s.url || "—"}</td>
                      <td>
                        <span className={`severity ${badgeTone}`}>
                          {httpStatusLabel(s.status)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {changes.length > 0 && (
        <div className="report-section">
          <h4>Notable changes</h4>
          <PresentableValue value={changes} />
        </div>
      )}

      <div className="report-section">
        <h4>Additional details</h4>
        <FieldGrid
          data={data}
          skipKeys={[
            ...META_KEYS,
            "pages_found",
            "indexable",
            "redirects",
            "redirect_chains",
            "canonical_issues",
            "broken_links",
            "broken_count",
            "status_samples",
            "notable_changes",
            "source",
            "task_id",
            "discovered_urls",
            "site_sitemap",
          ]}
        />
      </div>
    </div>
  );
}

function AuthorityTab({ data }: { data: Record<string, unknown> }) {
  const anchors = data.top_anchor_text;
  const spam = Number(data.spam_risk_score);
  const spamTone: "ok" | "warn" | "bad" | "neutral" =
    !Number.isFinite(spam) ? "neutral" : spam > 0.4 ? "bad" : spam > 0.25 ? "warn" : "ok";

  return (
    <div className="audit-panel">
      <MetricGrid
        items={[
          { label: "Provider", value: data.provider },
          { label: "Referring domains", value: data.referring_domains, tone: "neutral" },
          { label: "Authority score", value: data.authority_score, tone: "ok" },
          {
            label: "Spam risk",
            value: Number.isFinite(spam) ? `${Math.round(spam * 100)}%` : data.spam_risk_score,
            tone: spamTone,
          },
        ]}
      />
      {data.note ? <p className="hint-line">{String(data.note)}</p> : null}
      {data.spam_pre_triage_note ? (
        <p className="hint-line">{String(data.spam_pre_triage_note)}</p>
      ) : null}
      {anchors != null && (
        <div className="report-section">
          <h4>Top anchor text</h4>
          <PresentableValue value={anchors} />
        </div>
      )}
      <div className="report-section">
        <h4>Additional details</h4>
        <FieldGrid
          data={data}
          skipKeys={[
            ...META_KEYS,
            "provider",
            "referring_domains",
            "authority_score",
            "spam_risk_score",
            "top_anchor_text",
          ]}
        />
      </div>
    </div>
  );
}

function AnomaliesTab({ data }: { data: Record<string, unknown> }) {
  const spark = (data.sparkline || []) as number[];
  const max = Math.max(...spark, 1);
  return (
    <div className="audit-panel">
      {data.message ? (
        <div className="anomaly-banner">
          <span className={`severity ${String(data.severity || "info")}`}>
            {String(data.severity || "info")}
          </span>
          <p>{String(data.message)}</p>
        </div>
      ) : null}
      {spark.length > 0 && (
        <div className="report-section">
          <h4>Traffic trend</h4>
          <div className="sparkline" aria-label="Traffic sparkline">
            {spark.map((v, i) => (
              <i key={i} style={{ height: `${Math.max(4, (v / max) * 36)}px` }} />
            ))}
          </div>
        </div>
      )}
      <div className="report-section">
        <h4>Details</h4>
        <FieldGrid data={data} skipKeys={[...META_KEYS, "sparkline"]} />
      </div>
    </div>
  );
}

function GenericTab({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="audit-panel">
      {data.message ? <p className="hint-line">{String(data.message)}</p> : null}
      <FieldGrid data={data} skipKeys={[...META_KEYS]} />
    </div>
  );
}

export default function WebsiteCard({ payload, canAct, onAction }: Props) {
  const [tab, setTab] = useState<"technical" | "authority" | "anomalies">("technical");
  const tabs = (payload.tabs || {}) as Record<string, Record<string, unknown>>;
  const severities = (payload.severities || {}) as Record<string, string>;
  const data = tabs[tab] || {};
  const notRun =
    data.status === "not_run_this_session" ||
    data.status === "skipped" ||
    data.status === "unavailable";
  const siteSitemap = (payload.site_sitemap as SiteSitemap | undefined) || null;
  const sampleUrls = Array.isArray(payload.sample_urls)
    ? (payload.sample_urls as unknown[]).map(String).filter(Boolean)
    : undefined;

  return (
    <div className="structured-card checkpoint website">
      <h3 className="card-title">
        {String(payload.title || "Website Situation — Audit Summary")}
      </h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Sign-off requires{" "}
        <strong>{String(payload.required_role || "technical_seo_specialist")}</strong>
        {Array.isArray(payload.tabs_run)
          ? ` · Ran: ${(payload.tabs_run as string[]).map((t) => humanLabel(String(t))).join(", ")}`
          : ""}
        {payload.sitemap_url_count != null
          ? ` · Sitemap: ${String(payload.sitemap_url_count)} URL(s)`
          : ""}
      </p>
      <div className="tabs" role="tablist">
        {(["technical", "authority", "anomalies"] as const).map((t) => {
          const tData = tabs[t] || {};
          const idle =
            tData.status === "not_run_this_session" ||
            tData.status === "skipped" ||
            tData.status === "unavailable";
          return (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`tab ${tab === t ? "active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t[0].toUpperCase() + t.slice(1)}{" "}
              <span className={`severity ${idle ? "info" : severities[t] || "info"}`}>
                {idle ? String(tData.status || "not run").replaceAll("_", " ") : severities[t] || "info"}
              </span>
            </button>
          );
        })}
      </div>

      {notRun ? (
        <p style={{ fontSize: 13, color: "var(--muted)", margin: "12px 0 0" }}>
          {String(data.message || data.reason || "Not run this session.")}
        </p>
      ) : tab === "technical" ? (
        <TechnicalTab data={data} sitemap={siteSitemap} sampleUrls={sampleUrls} />
      ) : tab === "authority" ? (
        <AuthorityTab data={data} />
      ) : tab === "anomalies" ? (
        <AnomaliesTab data={data} />
      ) : (
        <GenericTab data={data} />
      )}

      {canAct && (
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
