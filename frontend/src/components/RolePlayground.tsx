import { useMemo, useState } from "react";
import DownloadReportsMenu from "./DownloadReportsMenu";
import PhaseBrowser, { type BrowserEntry } from "./PhaseBrowser";
import PlaygroundPhaseReport from "./PlaygroundPhaseReport";
import ReportDownloadButton from "./ReportDownloadButton";
import { PHASE_REPORT_CARD_TYPE } from "./ReportCardShell";
import type { Playground, PlaygroundSection, ReportDownloadFormat } from "../api";

type Props = {
  playground: Playground | null;
  loading?: boolean;
  clientId?: string;
  token?: string | null;
  onDownloadReports?: (format: ReportDownloadFormat) => void;
  downloadingReports?: boolean;
  onError?: (message: string) => void;
};

type PhaseMeta = {
  id: string;
  order: number;
  /** Position in the pipeline, e.g. "05" or "06a". */
  number: string;
  label: string;
};

const PIPELINE: PhaseMeta[] = [
  { id: "discovery", order: 1, number: "01", label: "Discovery" },
  { id: "tracking", order: 2, number: "02", label: "Tracking" },
  { id: "website", order: 3, number: "03", label: "Website audit + sitemap" },
  { id: "competitor", order: 4, number: "04", label: "Competitors" },
  { id: "search_demand", order: 5, number: "05", label: "Keywords" },
  { id: "site_architecture", order: 6, number: "06a", label: "URL mapping" },
  { id: "seo_strategy", order: 7, number: "06b", label: "Titles & calendar" },
  { id: "technical_seo", order: 8, number: "07", label: "Technical SEO" },
  { id: "content_audit", order: 9, number: "08", label: "Existing content audit" },
  { id: "content_planning", order: 10, number: "09", label: "Content planning" },
  { id: "content_production", order: 11, number: "10", label: "Draft & preview" },
  { id: "on_page_seo", order: 12, number: "11", label: "On-page & linking" },
  { id: "publishing", order: 13, number: "12", label: "Publish to WordPress" },
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
  number: "",
  label: "Client & overview",
};

function emptySection(meta: { label: string }): PlaygroundSection {
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

function entryFor(card: PhaseCard, number: string): BrowserEntry {
  const saved = card.status === "ready";
  const partial = card.status === "mixed";
  return {
    key: card.id,
    number,
    name: card.label,
    state: saved ? "done" : partial ? "wait" : "todo",
    statusText: saved ? "Saved" : partial ? "Partly saved" : card.status === "planned" ? "Later" : "Nothing yet",
    hasContent: saved || partial,
  };
}

export default function RolePlayground({
  playground,
  loading,
  clientId,
  token = null,
  onDownloadReports,
  downloadingReports = false,
  onError,
}: Props) {
  const [pick, setPick] = useState<string | null>(null);

  const phaseCards = useMemo(
    () => (playground ? buildPhaseCards(playground.sections) : []),
    [playground]
  );
  const entries = useMemo(() => {
    const numbers = new Map(PIPELINE.map((p) => [p.id, p.number]));
    return phaseCards.map((c) => entryFor(c, numbers.get(c.id) || ""));
  }, [phaseCards]);

  if (loading) {
    return (
      <div className="ct" aria-busy="true">
        <div className="ct-skeleton" />
      </div>
    );
  }

  if (!playground) {
    return (
      <div className="ct">
        <p className="ct-empty">Client memory could not be loaded. Refresh the page to try again.</p>
      </div>
    );
  }

  const clientName = playground.client?.name || "this client";
  const roleLabel = playground.role_label || playground.role_name;
  const pipelineIds = new Set(PIPELINE.map((p) => p.id));
  const saved = entries.filter((e) => e.hasContent && pipelineIds.has(e.key)).length;
  const firstWithContent = entries.find((e) => e.hasContent)?.key ?? null;
  const selectedKey = entries.some((e) => e.key === pick && e.hasContent) ? pick : firstWithContent;
  const card = phaseCards.find((c) => c.id === selectedKey) || null;
  const singleSection = card && card.sections.length === 1 ? card.sections[0] : null;

  return (
    <PhaseBrowser
      title="Client memory"
      summary={`${saved} of ${PIPELINE.length} phases are saved for ${clientName}. Showing the ${roleLabel} view.`}
      actions={
        onDownloadReports ? <DownloadReportsMenu onPick={onDownloadReports} loading={downloadingReports} /> : null
      }
      entries={entries}
      selectedKey={selectedKey}
      onSelect={setPick}
      missingNote="have nothing saved yet"
      empty={<p className="ct-empty">Nothing is saved for {clientName} yet. Memory fills in as the phases run.</p>}
      detailMeta={singleSection?.source_label ? `From the ${singleSection.source_label}` : undefined}
      detailActions={
        card && clientId && PHASE_REPORT_CARD_TYPE[card.id] ? (
          <ReportDownloadButton
            clientId={clientId}
            token={token}
            cardType={PHASE_REPORT_CARD_TYPE[card.id]}
            label="Download report"
            onError={onError}
          />
        ) : null
      }
    >
      {card
        ? card.sections.map((sec) => (
            <div key={sec.title} className="pb-block">
              {card.sections.length > 1 ? (
                <div className="pb-block-head">
                  <h4>{sec.title}</h4>
                  <span className="pb-block-source">
                    {sec.source_label}
                    {sec.status === "ready" ? "" : sec.status === "planned" ? ", later" : ", nothing saved yet"}
                  </span>
                </div>
              ) : null}
              {sec.data != null ? (
                <div className="playground-body">
                  <PlaygroundPhaseReport reportKind={sec.report_kind || inferKind(sec.title)} data={sec.data} />
                </div>
              ) : (
                <p className="pb-empty">{sec.empty_hint}</p>
              )}
            </div>
          ))
        : null}
    </PhaseBrowser>
  );
}
