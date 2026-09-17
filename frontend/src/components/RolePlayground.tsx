import { useEffect, useMemo, useRef, useState } from "react";
import PlaygroundPhaseReport from "./PlaygroundPhaseReport";
import ReportDownloadButton from "./ReportDownloadButton";
import { PHASE_REPORT_CARD_TYPE } from "./ReportCardShell";
import type { Playground, PlaygroundSection, ReportDownloadFormat } from "../api";

type Props = {
  playground: Playground | null;
  loading?: boolean;
  /** Sidebar/drawer layout — single-column cards, compact header */
  variant?: "page" | "sidebar";
  clientId?: string;
  token?: string | null;
  onClose?: () => void;
  onDownloadReports?: (format: ReportDownloadFormat) => void;
  downloadingReports?: boolean;
  onError?: (message: string) => void;
};

const DOWNLOAD_FORMATS: { format: ReportDownloadFormat; label: string }[] = [
  { format: "pdf", label: "PDF" },
  { format: "docx", label: "Word (.docx)" },
];

function DownloadReportsButton({
  onPick,
  loading,
}: {
  onPick: (format: ReportDownloadFormat) => void;
  loading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div ref={rootRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="btn btn-ghost"
        style={{ fontSize: 12, padding: "4px 10px" }}
        disabled={loading}
        onClick={() => setOpen((v) => !v)}
        title="Download all phase reports as PDF or Word"
        aria-haspopup="true"
        aria-expanded={open}
      >
        {loading ? "Preparing…" : "Download reports ▾"}
      </button>
      {open ? (
        <div className="report-download-menu-list" role="menu">
          {DOWNLOAD_FORMATS.map((f) => (
            <button
              key={f.format}
              type="button"
              role="menuitem"
              className="report-download-menu-item"
              onClick={() => {
                setOpen(false);
                onPick(f.format);
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type PhaseMeta = {
  id: string;
  order: number;
  label: string;
};

const PIPELINE: PhaseMeta[] = [
  { id: "discovery", order: 1, label: "01 · Discovery" },
  { id: "tracking", order: 2, label: "02 · Tracking" },
  { id: "website", order: 3, label: "03 · Website audit + sitemap" },
  { id: "competitor", order: 4, label: "04 · Competitors" },
  { id: "search_demand", order: 5, label: "05 · Keywords" },
  { id: "site_architecture", order: 6, label: "06a · URL mapping" },
  { id: "seo_strategy", order: 7, label: "06b · Titles & calendar" },
  { id: "technical_seo", order: 8, label: "07 · Technical SEO" },
  { id: "content_audit", order: 9, label: "08 · Existing content audit" },
  { id: "content_planning", order: 10, label: "09 · Content planning" },
  { id: "content_production", order: 11, label: "10 · Draft & preview" },
  { id: "on_page_seo", order: 12, label: "11 · On-page & linking" },
  { id: "publishing", order: 13, label: "12 · Publish to WordPress" },
];

const KIND_TO_ID: Record<string, string> = {
  discovery: "discovery",
  tracking: "tracking",
  website: "website",
  competitors: "competitor",
  competitor: "competitor",
  search_demand: "search_demand",
  seo_strategy: "seo_strategy",
  site_architecture: "site_architecture",
  technical_seo: "technical_seo",
  content_audit: "content_audit",
  content_planning: "content_planning",
  content_production: "content_production",
  on_page_seo: "on_page_seo",
  publishing: "publishing",
};

function phaseIdsForSection(sec: PlaygroundSection): string[] {
  const t = sec.title.toLowerCase();
  if (t === "client" || t.includes("client profile") || t.includes("department overview")) {
    return ["context"];
  }
  const ids: string[] = [];
  if (t.includes("discovery")) ids.push("discovery");
  if (t.includes("tracking")) ids.push("tracking");
  if (t.includes("website")) ids.push("website");
  if (t.includes("competitor")) ids.push("competitor");
  if (t.includes("search demand") || t.includes("keyword")) ids.push("search_demand");
  if (t.includes("url mapping") || t.includes("site architecture") || (t.includes("architecture") && !t.includes("strategy"))) {
    ids.push("site_architecture");
  }
  if (t.includes("technical seo")) ids.push("technical_seo");
  if (t.includes("content audit")) ids.push("content_audit");
  if (t.includes("content planning") || t.includes("roadmap")) ids.push("content_planning");
  if (t.includes("production") || t.includes("brief")) ids.push("content_production");
  if (t.includes("on-page") || t.includes("on page")) ids.push("on_page_seo");
  if (t.includes("publish")) ids.push("publishing");
  if (t.includes("strategy") || t.includes("content strategy") || t.includes("seo strategy")) {
    ids.push("seo_strategy");
  }
  const kindId = KIND_TO_ID[String(sec.report_kind || "").toLowerCase()];
  if (kindId && !ids.includes(kindId)) ids.push(kindId);
  return [...new Set(ids)];
}

function inferKind(title: string, reportKind?: string): string {
  if (reportKind && reportKind !== "generic") return reportKind;
  const ids = phaseIdsForSection({
    title,
    source_role: "",
    source_label: "",
    status: "",
    empty_hint: "",
    data: null,
    report_kind: reportKind,
  });
  return ids[0] || "generic";
}

type PhaseCard = {
  id: string;
  label: string;
  order: number;
  sections: PlaygroundSection[];
  status: "ready" | "waiting" | "mixed" | "planned";
};

const CONTEXT_PHASE: PhaseMeta = {
  id: "context",
  order: 0,
  label: "Client & overview",
};

function emptySection(meta: PhaseMeta): PlaygroundSection {
  return {
    title: meta.label,
    source_role: "shared",
    source_label: "Shared memory",
    status: "waiting",
    empty_hint: "This phase has not published memory yet.",
    data: null,
  };
}

function buildPhaseCards(sections: PlaygroundSection[]): PhaseCard[] {
  const all = [CONTEXT_PHASE, ...PIPELINE];
  const map = new Map<string, PhaseCard>();
  for (const meta of all) {
    map.set(meta.id, {
      id: meta.id,
      label: meta.label,
      order: meta.order,
      sections: [],
      status: "waiting",
    });
  }
  for (const sec of sections) {
    const ids = phaseIdsForSection(sec);
    const targets = ids.length ? ids : ["context"];
    for (const id of targets) {
      const existing = map.get(id);
      if (existing) {
        existing.sections.push(sec);
        continue;
      }
      map.set(id, {
        id,
        label: sec.title,
        order: 50,
        sections: [sec],
        status: "waiting",
      });
    }
  }
  const cards = Array.from(map.values()).sort((a, b) => a.order - b.order);
  for (const card of cards) {
    if (!card.sections.length) {
      if (card.id === "context") continue;
      card.sections = [emptySection(card)];
    }
    const statuses = card.sections.map((s) => s.status || "waiting");
    if (statuses.every((s) => s === "ready")) card.status = "ready";
    else if (statuses.every((s) => s === "planned")) card.status = "planned";
    else if (statuses.some((s) => s === "ready")) card.status = "mixed";
    else card.status = "waiting";
  }
  return cards.filter((c) => c.id !== "context" || c.sections.length > 0);
}

function statusLabel(status: PhaseCard["status"]): string {
  if (status === "ready") return "Ready";
  if (status === "mixed") return "Partial";
  if (status === "planned") return "Later";
  return "Waiting";
}

export default function RolePlayground({
  playground,
  loading,
  variant = "page",
  clientId,
  token = null,
  onClose,
  onDownloadReports,
  downloadingReports = false,
  onError,
}: Props) {
  const [openId, setOpenId] = useState<string | null>(null);

  const phaseCards = useMemo(
    () => (playground ? buildPhaseCards(playground.sections) : []),
    [playground]
  );

  if (loading) {
    return (
      <div className={`playground${variant === "sidebar" ? " is-sidebar" : ""}`}>
        <div className="playground-loading">Loading team memory…</div>
      </div>
    );
  }

  if (!playground) {
    return (
      <div className={`playground${variant === "sidebar" ? " is-sidebar" : ""}`}>
        <div className="playground-loading">
          Team memory unavailable. Refresh the page — if this persists, confirm the API on port
          8000 is running.
        </div>
      </div>
    );
  }

  const clientName = playground.client?.name || "Client";

  return (
    <div className={`playground playground-accordion${variant === "sidebar" ? " is-sidebar" : ""}`}>
      <header className="playground-hero">
        <div className="playground-hero-row">
          <div>
            <h2>Client memory</h2>
            <p className="playground-sub">
              {clientName} · {playground.role_label || playground.role_name} · locked packs from approved phases
            </p>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {onDownloadReports ? (
              <DownloadReportsButton onPick={onDownloadReports} loading={downloadingReports} />
            ) : null}
            {onClose ? (
              <button type="button" className="btn btn-ghost playground-close" onClick={onClose}>
                Close
              </button>
            ) : null}
          </div>
        </div>
      </header>

      <div className="playground-phase-list">
        <div className="playground-phase-group">
          <div className="playground-phase-group-title">Pipeline · 01–12</div>
          {phaseCards.map((card) => {
            const open = openId === card.id;
            return (
              <article
                key={card.id}
                className={`playground-phase-card status-${card.status}${open ? " is-open" : ""}`}
              >
                    <button
                      type="button"
                      className="playground-phase-toggle"
                      aria-expanded={open}
                      onClick={() => setOpenId(open ? null : card.id)}
                    >
                      <span className="playground-phase-toggle-main">
                        <span className="playground-phase-label">{card.label}</span>
                        <span className={`playground-phase-pill status-${card.status}`}>
                          {statusLabel(card.status)}
                        </span>
                      </span>
                      <span className="playground-phase-meta">
                        {card.sections.length > 1
                          ? `${card.sections.length} blocks`
                          : card.sections[0]?.source_label || "Shared"}
                      </span>
                      <span className="playground-phase-chevron" aria-hidden>
                        {open ? "▾" : "▸"}
                      </span>
                    </button>

                    {open ? (
                      <div className="playground-phase-body">
                        {clientId && PHASE_REPORT_CARD_TYPE[card.id] ? (
                          <div className="playground-phase-download">
                            <ReportDownloadButton
                              clientId={clientId}
                              token={token}
                              cardType={PHASE_REPORT_CARD_TYPE[card.id]}
                              label="Download phase report"
                              onError={onError}
                            />
                          </div>
                        ) : null}
                        {card.sections.map((sec) => (
                          <div key={sec.title} className="playground-phase-section">
                            {card.sections.length > 1 || sec.title !== card.label ? (
                              <div className="playground-phase-section-head">
                                <h4>{sec.title}</h4>
                                <span className="playground-source">
                                  {sec.source_label}
                                  {sec.status === "ready"
                                    ? " · ready"
                                    : sec.status === "planned"
                                      ? " · later"
                                      : " · waiting"}
                                </span>
                              </div>
                            ) : (
                              <div className="playground-phase-section-head">
                                <span className="playground-source">
                                  {sec.source_label}
                                  {sec.status === "ready" ? " · in memory" : ""}
                                </span>
                              </div>
                            )}
                            {sec.data != null ? (
                              <div className="playground-body">
                                <PlaygroundPhaseReport
                                  reportKind={sec.report_kind || inferKind(sec.title)}
                                  data={sec.data}
                                />
                              </div>
                            ) : (
                              <p className="playground-empty">{sec.empty_hint}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : null}
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
