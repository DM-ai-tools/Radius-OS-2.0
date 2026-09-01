import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ChatEvent, Client, Profile, WordPressStatus } from "../api";
import { useAuth } from "../auth";
import ThinkingIndicator from "../components/ThinkingIndicator";
import WordPressConnectModal from "../components/WordPressConnectModal";
import DiscoveryCard from "../components/cards/DiscoveryCard";
import TrackingCard from "../components/cards/TrackingCard";
import WebsiteCard from "../components/cards/WebsiteCard";
import BrokenLinkCard from "../components/cards/BrokenLinkCard";
import OnPageSeoCard from "../components/cards/OnPageSeoCard";
import TechnicalSeoCard from "../components/cards/TechnicalSeoCard";
import SeoAuditCard from "../components/cards/SeoAuditCard";
import CompetitorCard from "../components/cards/CompetitorCard";
import SearchDemandCard from "../components/cards/SearchDemandCard";
import ContentStrategyCard from "../components/cards/ContentStrategyCard";
import SiteArchitectureCard from "../components/cards/SiteArchitectureCard";
import ContentAuditCard from "../components/cards/ContentAuditCard";
import ContentPlanningCard from "../components/cards/ContentPlanningCard";
import ContentProductionCard from "../components/cards/ContentProductionCard";
import PublishingCard from "../components/cards/PublishingCard";
import ReadinessCard from "../components/cards/ReadinessCard";
import RolePlayground from "../components/RolePlayground";
import EngineRoomPanel from "../components/EngineRoomPanel";
import CostTrackerPanel from "../components/CostTrackerPanel";
import ReportCardShell from "../components/ReportCardShell";
import { FieldGrid, humanLabel } from "../components/cards/PresentableValue";
import { sleep, statusLinesForPrompt } from "../lib/thinkingStatus";
import type { Playground, ReportDownloadFormat } from "../api";

type UiMessage = {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  agent_key?: string | null;
  card?: Record<string, unknown>;
  streaming?: boolean;
  created_at?: string;
  changeRequest?: boolean;
  changeReply?: boolean;
};

const AGENT_CLASS: Record<string, string> = {
  discovery_agent: "agent-discovery",
  tracking_access_agent: "agent-tracking",
  website_situation_agent: "agent-website",
  competitor_market_agent: "agent-competitor",
  search_demand: "agent-search-demand",
  content_strategy: "agent-content-strategy",
  site_architecture: "agent-site-architecture",
  technical_seo: "agent-technical-seo",
  content_audit: "agent-content-audit",
  content_planning: "agent-content-planning",
  content_production: "agent-content-production",
  on_page_seo: "agent-on-page-seo",
  publishing: "agent-publishing",
};

const AGENT_NAME: Record<string, string> = {
  discovery_agent: "Discovery Agent",
  tracking_access_agent: "Tracking & Access Agent",
  website_situation_agent: "Website Situation Agent",
  competitor_market_agent: "Competitor & Market Agent",
  readiness_gate: "Readiness Score",
  search_demand: "Search Demand Agent",
  content_strategy: "Content Strategy Agent",
  site_architecture: "Site Architecture Agent",
  technical_seo: "Technical SEO Agent",
  content_audit: "Content Audit Agent",
  content_planning: "Content Planning Agent",
  content_production: "Content Production Agent",
  on_page_seo: "On-Page SEO Agent",
  publishing: "Publishing Agent",
  memory_qa: "Shared Memory",
};

const EMPTY_STATUSES = {
  discovery: "not_started",
  tracking: "not_started",
  website: "not_started",
  competitor: "not_started",
  search_demand: "not_started",
  seo_strategy: "not_started",
  site_architecture: "not_started",
  technical_seo: "not_started",
  content_audit: "not_started",
  content_planning: "not_started",
  content_production: "not_started",
  on_page_seo: "not_started",
  publishing: "not_started",
};

const CONTROL_PHASES: Array<{
  key: keyof typeof EMPTY_STATUSES;
  label: string;
  agent: string;
  prompt: string;
}> = [
  { key: "discovery", label: "01. Discovery", agent: "discovery_agent", prompt: "Run discovery" },
  { key: "tracking", label: "02. Tracking", agent: "tracking_access_agent", prompt: "Run tracking check" },
  {
    key: "website",
    label: "03. Website",
    agent: "website_situation_agent",
    prompt: "Run website situation analysis  -  SEO audit every page",
  },
  { key: "competitor", label: "04. Competitors", agent: "competitor_market_agent", prompt: "Refresh competitor scan" },
  { key: "search_demand", label: "05. Search demand", agent: "search_demand", prompt: "Run keyword research / search demand" },
  {
    key: "seo_strategy",
    label: "06a. Content strategy",
    agent: "content_strategy",
    prompt: "Run content strategy and content calendar",
  },
  {
    key: "site_architecture",
    label: "06b. Site architecture",
    agent: "site_architecture",
    prompt: "Run site architecture and click-depth audit",
  },
  { key: "technical_seo", label: "07. Technical SEO", agent: "technical_seo", prompt: "Run technical SEO audit" },
  { key: "content_audit", label: "08. Content audit", agent: "content_audit", prompt: "Run existing content audit" },
  {
    key: "content_planning",
    label: "09. Content planning",
    agent: "content_planning",
    prompt: "Merge strategy, architecture, and audit into a locked page roadmap",
  },
  {
    key: "content_production",
    label: "10. Content production",
    agent: "content_production",
    prompt: "Run content production briefs and drafts",
  },
  { key: "on_page_seo", label: "11. On-page SEO", agent: "on_page_seo", prompt: "Run on-page SEO package" },
  { key: "publishing", label: "12. Publishing", agent: "publishing", prompt: "Run publishing checklist and IndexNow preview" },
];

const PHASE_OWNER: Record<string, string> = {
  discovery: "SEO Strategist",
  tracking: "Technical SEO Specialist",
  website: "Technical SEO Specialist",
  competitor: "SEO Strategist",
  search_demand: "SEO Strategist",
  seo_strategy: "SEO Strategist",
  site_architecture: "SEO Strategist",
  technical_seo: "Technical SEO Specialist",
  content_audit: "Content Writer",
  content_planning: "SEO Strategist",
  content_production: "Content Writer",
  on_page_seo: "SEO Strategist",
  publishing: "Digital Account Manager",
};

function reportPhaseIndex(msg: UiMessage): number {
  const key = String(
    msg.card?.validated_agent_key || msg.agent_key || msg.card?.agent_key || ""
  );
  const type = String(msg.card?.card_type || "");
  const i = CONTROL_PHASES.findIndex(
    (p) =>
      p.agent === key ||
      p.key === key ||
      type.startsWith(p.key) ||
      type.includes(p.agent) ||
      (p.key === "seo_strategy" && (type.includes("content_strategy") || type.includes("seo_strategy")))
  );
  return i === -1 ? CONTROL_PHASES.length : i;
}

function reportPhaseKey(msg: UiMessage): string | null {
  const i = reportPhaseIndex(msg);
  return i >= 0 && i < CONTROL_PHASES.length ? String(CONTROL_PHASES[i].key) : null;
}

function reportTime(msg: UiMessage): number {
  const t = Date.parse(msg.created_at || "");
  return Number.isFinite(t) ? t : 0;
}

function changeThreadLabel(msg: UiMessage): string {
  if (msg.changeRequest) return "You";
  if (msg.role === "system") return "System";
  return "Update";
}

function changeThreadText(msg: UiMessage): string {
  if (msg.content.trim()) return msg.content.trim();
  if (msg.card) {
    const title = String(msg.card.title || msg.card.card_type || "Report").trim();
    if (msg.card.revised_via_chat) return `Updated ${title} from your change request.`;
    return `Report pack: ${title}`;
  }
  return "";
}

function isRevisionReport(m: UiMessage): boolean {
  if (!m.card) return false;
  return Boolean(m.changeReply || m.card.revised_via_chat);
}

function reportBucketKey(m: UiMessage): string {
  const type = String(m.card?.card_type || "");
  const key =
    type === "phase_validation_report"
      ? String(m.card?.validated_agent_key || m.agent_key || m.card?.agent_key || m.id)
      : String(m.agent_key || m.card?.agent_key || m.card?.card_type || m.id);
  return type === "phase_validation_report" ? `${key}__validation` : key;
}

function reportsInPipelineOrder(messages: UiMessage[]): UiMessage[] {
  const bucketRuns = new Map<string, UiMessage[]>();
  const revisions: UiMessage[] = [];

  messages.forEach((m) => {
    if (!m.card) return;
    // Reports pane: phase deliverables only — never show validation QC cards.
    if (String(m.card.card_type) === "phase_validation_report") return;
    if (isRevisionReport(m)) {
      revisions.push(m);
      return;
    }
    const bucket = reportBucketKey(m);
    if (!bucketRuns.has(bucket)) bucketRuns.set(bucket, []);
    bucketRuns.get(bucket)!.push(m);
  });

  // Sort buckets by phase order (use the first message in each bucket for ordering)
  const sortedBuckets = [...bucketRuns.entries()].sort(([, aList], [, bList]) => {
    const a = aList[0];
    const b = bList[0];
    const phase = reportPhaseIndex(a) - reportPhaseIndex(b);
    if (phase !== 0) return phase;
    return reportTime(a) - reportTime(b);
  });

  // Flatten: for each bucket emit the LATEST run first (gets phase focus),
  // then earlier runs labeled as history. Users were looking at Run #1 after
  // re-running Search Demand and thinking topics had not changed.
  const base: UiMessage[] = [];
  for (const [, runs] of sortedBuckets) {
    runs.sort((a, b) => {
      const t = reportTime(a) - reportTime(b);
      if (t !== 0) return t;
      // Prefer Phase 5–spined strategy queues when timestamps collide.
      const spine = strategyQueueSpineScore(a) - strategyQueueSpineScore(b);
      if (spine !== 0) return spine;
      // Prefer production cards that actually carry a draft body.
      const draft = productionDraftScore(a) - productionDraftScore(b);
      if (draft !== 0) return draft;
      return messages.indexOf(a) - messages.indexOf(b);
    });
    const latest = runs[runs.length - 1];
    const earlier = runs.slice(0, -1).reverse();
    (latest as any)._isLatestRun = true;
    delete (latest as any)._rerunIndex;
    delete (latest as any)._rerunTotal;
    earlier.forEach((m, i) => {
      (m as any)._isLatestRun = false;
      (m as any)._rerunIndex = earlier.length - i;
      (m as any)._rerunTotal = runs.length;
    });
    base.push(latest, ...earlier);
  }

  const sortedRevisions = [...revisions].sort((a, b) => {
    const ta = reportTime(a);
    const tb = reportTime(b);
    if (ta !== tb) return ta - tb;
    return messages.indexOf(a) - messages.indexOf(b);
  });

  return [...base, ...sortedRevisions];
}

/** Higher = more likely the approved Phase 5 topic spine (not ranked best_opps). */
function strategyQueueSpineScore(m: UiMessage): number {
  if (String(m.card?.card_type) !== "content_strategy_report") return 0;
  const q = m.card?.priority_queue;
  if (!Array.isArray(q) || !q.length) return 0;
  const top = q.slice(0, 5) as Array<Record<string, unknown>>;
  return top.filter((r) => r && r.from_phase5_topic).length;
}

/** Prefer Content Production cards that include a real draft body. */
function productionDraftScore(m: UiMessage): number {
  if (String(m.card?.card_type) !== "content_production_report") return 0;
  const drafts = m.card?.drafts;
  if (Array.isArray(drafts) && drafts.length) {
    const md = String((drafts[0] as Record<string, unknown>)?.markdown || "");
    if (md.length > 200) return 2;
    if (md.length > 0) return 1;
  }
  if (String(m.card?.draft_markdown || "").length > 200) return 2;
  return 0;
}

/** Overlay locked CDP strategy queue onto report cards so stale chat payloads cannot win. */
function hydrateStrategyQueueFromProfile(history: UiMessage[], profile?: Profile): UiMessage[] {
  const pack = profile?.seo_strategy_summary;
  if (!pack || typeof pack !== "object" || Array.isArray(pack)) return history;
  const queue = (pack as Record<string, unknown>).priority_queue;
  if (!Array.isArray(queue) || !queue.length) return history;
  const spineCount = queue
    .slice(0, 5)
    .filter((r) => r && typeof r === "object" && (r as Record<string, unknown>).from_phase5_topic)
    .length;
  if (spineCount < 1) return history;

  const calendar = (pack as Record<string, unknown>).calendar;
  const combined = (pack as Record<string, unknown>).combined_priority_queue;
  return history.map((m) => {
    if (String(m.card?.card_type) !== "content_strategy_report" || !m.card) return m;
    return {
      ...m,
      card: {
        ...m.card,
        priority_queue: queue,
        ...(Array.isArray(calendar) ? { calendar, content_calendar: calendar } : {}),
        ...(Array.isArray(combined) ? { combined_priority_queue: combined } : {}),
        queue_source: "phase5_topic_plan",
        hydrated_from_cdp: true,
      },
    };
  });
}

/** Overlay CDP production draft onto report cards so empty twin cards cannot hide output. */
function hydrateProductionDraftFromProfile(history: UiMessage[], profile?: Profile): UiMessage[] {
  const pack = profile?.content_production_summary;
  if (!pack || typeof pack !== "object" || Array.isArray(pack)) return history;
  const drafts = (pack as Record<string, unknown>).drafts;
  if (!Array.isArray(drafts) || !drafts.length) return history;
  const first = drafts[0] as Record<string, unknown>;
  const md = String(first?.markdown || "");
  if (md.length < 200) return history;

  return history.map((m) => {
    if (String(m.card?.card_type) !== "content_production_report" || !m.card) return m;
    const existing = Array.isArray(m.card.drafts) ? m.card.drafts : [];
    const existingMd =
      existing.length && typeof existing[0] === "object"
        ? String((existing[0] as Record<string, unknown>).markdown || "")
        : String(m.card.draft_markdown || "");
    if (existingMd.length >= md.length) return m;
    return {
      ...m,
      card: {
        ...m.card,
        drafts,
        draft_markdown: md,
        draft_title: first.title || first.keyword || m.card.draft_title,
        draft_images: first.images || m.card.draft_images,
        draft_count: drafts.length,
        awaiting_topic_selection: false,
        topic_choices: (pack as Record<string, unknown>).topic_choices || m.card.topic_choices,
        queued_for_next_write:
          (pack as Record<string, unknown>).queued_for_next_write || m.card.queued_for_next_write,
        selected_keyword: (pack as Record<string, unknown>).selected_keyword,
        selected_url: (pack as Record<string, unknown>).selected_url,
        note: (pack as Record<string, unknown>).note || m.card.note,
        hydrated_from_cdp: true,
      },
    };
  });
}

const PHASE_VALIDATION_SUMMARIES: Array<{ agent: string; summary: keyof Profile }> = [
  { agent: "tracking_access_agent", summary: "tracking_baseline" },
  { agent: "website_situation_agent", summary: "website_situation_summary" },
  { agent: "competitor_market_agent", summary: "competitive_landscape_summary" },
  { agent: "search_demand", summary: "search_demand_summary" },
  { agent: "content_strategy", summary: "seo_strategy_summary" },
  { agent: "site_architecture", summary: "site_architecture_summary" },
  { agent: "technical_seo", summary: "technical_seo_summary" },
  { agent: "content_audit", summary: "content_audit_summary" },
  { agent: "content_planning", summary: "content_planning_summary" },
  { agent: "content_production", summary: "content_production_summary" },
  { agent: "on_page_seo", summary: "on_page_seo_summary" },
  { agent: "publishing", summary: "publishing_summary" },
];

function hydrateValidationReports(history: UiMessage[], profile?: Profile): UiMessage[] {
  // Validation cards disabled — return history without hydrating validation reports
  return history;
}

function phaseStatusLabel(status: string): string {
  if (status === "complete") return "Approved";
  if (status === "pending_signoff") return "Needs approval";
  if (status === "in_progress") return "Running";
  return "Waiting";
}

const START_OPERATIONS: Array<{ label: string; prompt: string; agent: string }> = [
  { label: "Complete SEO Audit", prompt: "Run website situation analysis  -  SEO audit every page", agent: "website_situation_agent" },
  { label: "Analyse Competitors", prompt: "Refresh competitor scan", agent: "competitor_market_agent" },
  { label: "Find Keyword Opportunities", prompt: "Run keyword research / search demand", agent: "search_demand" },
  { label: "Build Content Strategy", prompt: "Run content strategy and content calendar", agent: "content_strategy" },
  { label: "Improve Technical SEO", prompt: "Run technical SEO audit", agent: "technical_seo" },
  { label: "Create & Publish Content", prompt: "Run content production briefs and drafts", agent: "content_production" },
];

const AGENT_PURPOSE: Record<string, string> = {
  discovery: "Understand business, audience and market",
  tracking: "Verify analytics, GTM and conversion access",
  website: "Audit crawlability, indexation and page health",
  competitor: "Map landscape, gaps and SERP overlap",
  search_demand: "Find demand, keywords and SERP opportunities",
  seo_strategy: "Lock pillars, calendar and content queue",
  site_architecture: "Click-depth, URL tree and IA",
  technical_seo: "CWV, indexation and technical debt",
  content_audit: "Score existing pages for refresh vs retire",
  content_planning: "Merge strategy + IA + audit into a roadmap",
  content_production: "Briefs, drafts and production QA",
  on_page_seo: "Titles, schema and on-page packages",
  publishing: "CMS checklist, IndexNow and go-live",
};

function monitorStatusLabel(status: string): string {
  if (status === "complete") return "Completed";
  if (status === "in_progress") return "Running";
  if (status === "pending_signoff") return "Sign-off";
  return "Waiting";
}

function monitorStatusClass(status: string): string {
  if (status === "complete") return "done";
  if (status === "in_progress") return "running";
  if (status === "pending_signoff") return "signoff";
  return "waiting";
}

function phaseStatusTone(status: string): string {
  if (status === "complete") return "pass";
  if (status === "pending_signoff") return "warning";
  if (status === "in_progress") return "unverified";
  return "unverified";
}

function mapPhaseStatuses(
  src: Record<string, unknown> | null | undefined,
  fallback?: typeof EMPTY_STATUSES,
): typeof EMPTY_STATUSES {
  const f = fallback || EMPTY_STATUSES;
  return {
    discovery: String(src?.discovery ?? src?.discovery_status ?? f.discovery),
    tracking: String(src?.tracking ?? src?.tracking_status ?? f.tracking),
    website: String(src?.website ?? src?.website_status ?? f.website),
    competitor: String(src?.competitor ?? src?.competitor_status ?? f.competitor),
    search_demand: String(src?.search_demand ?? src?.search_demand_status ?? f.search_demand),
    seo_strategy: String(src?.seo_strategy ?? src?.seo_strategy_status ?? f.seo_strategy),
    site_architecture: String(
      src?.site_architecture ?? src?.site_architecture_status ?? f.site_architecture,
    ),
    technical_seo: String(src?.technical_seo ?? src?.technical_seo_status ?? f.technical_seo),
    content_audit: String(src?.content_audit ?? src?.content_audit_status ?? f.content_audit),
    content_planning: String(
      src?.content_planning ?? src?.content_planning_status ?? f.content_planning,
    ),
    content_production: String(
      src?.content_production ?? src?.content_production_status ?? f.content_production,
    ),
    on_page_seo: String(src?.on_page_seo ?? src?.on_page_seo_status ?? f.on_page_seo),
    publishing: String(src?.publishing ?? src?.publishing_status ?? f.publishing),
  };
}

type StoredMessage = {
  id: string;
  role: string;
  content: string;
  structured_payload: Record<string, unknown> | null;
  agent_key: string | null;
  created_at: string;
};

function isStaleGateNotice(text: string): boolean {
  const t = text.toLowerCase().trim();
  return (
    t === "readiness gate" ||
    t.startsWith("readiness gate") ||
    t.includes("readiness gate is approved") ||
    t.includes("qa / hod approve") ||
    t.includes("approve the gate") ||
    (t.includes("phases 5") && t.includes("readiness gate"))
  );
}

function scrubCardPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const next: Record<string, unknown> = { ...payload };
  for (const [k, v] of Object.entries(next)) {
    if (typeof v === "string" && /readiness gate/i.test(v)) {
      next[k] = v
        .replace(/then run readiness gate\.?/gi, "then run keyword research (Phase 5).")
        .replace(/run readiness gate/gi, "run keyword research")
        .replace(/Readiness Gate/g, "Readiness Score");
    }
  }
  return next;
}

function mapStoredMessages(rows: StoredMessage[]): UiMessage[] {
  const out: UiMessage[] = [];
  for (const row of rows) {
    const payload = row.structured_payload
      ? scrubCardPayload(row.structured_payload)
      : null;
    const eventType = String(payload?.event_type || "");

    // Noise / duplicates  -  checkpoint usually mirrors structured_card
    if (
      eventType === "checkpoint" ||
      eventType === "phase_status" ||
      eventType === "job_progress" ||
      eventType === "thinking" ||
      eventType === "done" ||
      eventType === "active_agent"
    ) {
      continue;
    }

    if (eventType === "structured_card" || (payload && payload.card_type)) {
      out.push({
        id: row.id,
        role: "agent",
        content: row.content || String(payload?.title || "Report"),
        agent_key: row.agent_key || (payload?.agent_key as string) || undefined,
        card: payload || undefined,
        created_at: row.created_at,
      });
      continue;
    }

    if (row.role === "user") {
      // Collapse repeated identical user prompts (re-approve auto-continue spam)
      const last = out[out.length - 1];
      if (last?.role === "user" && last.content === (row.content || "")) continue;
      out.push({ id: row.id, role: "user", content: row.content || "" });
      continue;
    }

    if (row.role === "system" || eventType === "system_notice" || eventType === "error") {
      if (!row.content?.trim()) continue;
      if (isStaleGateNotice(row.content)) continue;
      out.push({ id: row.id, role: "system", content: row.content });
      continue;
    }

    // agent_message and plain agent rows
    if (!row.content?.trim() && !payload) continue;
    if (row.content && isStaleGateNotice(row.content)) continue;
    out.push({
      id: row.id,
      role: "agent",
      content: row.content || "",
      agent_key: row.agent_key || undefined,
        created_at: row.created_at,
    });
  }
  return out;
}

export default function ChatPage() {
  const { clientId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { token, user, canTrigger, canApprove, logout } = useAuth();
  const canViewEngineRoom =
    user?.role_name === "head_of_department" || user?.role_name === "client_success_manager";
  const canViewCostTracker = canViewEngineRoom;
  const [client, setClient] = useState<Client | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [thinkingStatus, setThinkingStatus] = useState("Routing your request...");
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [sidebarNav, setSidebarNav] = useState<
    "command" | "operations" | "reports" | "memory" | "agents" | "engine_room" | "cost_tracker"
  >("command");
  const [kpiDetail, setKpiDetail] = useState<null | "issues" | "opportunities">(null);
  const [reportFocusKey, setReportFocusKey] = useState<string | null>(null);
  const [changeDockOpen, setChangeDockOpen] = useState(false);
  const commandRef = useRef<HTMLTextAreaElement>(null);
  const [commandFocusTick, setCommandFocusTick] = useState(0);
  const [error, setError] = useState("");
  const [playground, setPlayground] = useState<Playground | null>(null);
  const [playgroundLoading, setPlaygroundLoading] = useState(true);
  const [exportingReports, setExportingReports] = useState(false);
  const [wordpressStatus, setWordpressStatus] = useState<WordPressStatus | null>(null);
  const [wordpressModalOpen, setWordpressModalOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    typeof document !== "undefined" && document.documentElement.classList.contains("theme-light")
      ? "light"
      : "dark"
  );
  const oauthHandled = useRef(false);
  const [statuses, setStatuses] = useState({ ...EMPTY_STATUSES });
  const [readiness, setReadiness] = useState({
    overall: 0,
    missing: [] as string[],
    can_gate: false,
    ready_for_phase5: false,
    threshold: 90,
  });
  const bottomRef = useRef<HTMLDivElement>(null);
  const reportsEndRef = useRef<HTMLDivElement>(null);
  const reportPaneRef = useRef<HTMLDivElement>(null);
  const bootClientId = useRef<string | null>(null);
  const statusTimer = useRef<number | null>(null);
  const streamLock = useRef(false);
  const changeFlow = useRef(false);
  const [pendingHandoff, setPendingHandoff] = useState<{
    prompt: string;
    label: string;
  } | null>(null);

  const refreshReadiness = useCallback(async () => {
    if (!token || !clientId) return;
    try {
      const r = await api.readiness(token, clientId);
      setReadiness({
        overall: r.overall,
        missing: r.missing,
        can_gate: r.can_gate,
        ready_for_phase5: r.ready_for_phase5,
        threshold: r.threshold,
      });
      setStatuses(mapPhaseStatuses(r.statuses as Record<string, unknown>));
    } catch {
      /* readiness can fail during API reload  -  chat history still works */
    }
    try {
      setPlaygroundLoading(true);
      const pg = await api.playground(token, clientId);
      setPlayground(pg);
    } catch {
      setPlayground(null);
    } finally {
      setPlaygroundLoading(false);
    }
  }, [token, clientId]);

  const refreshWordpressStatus = useCallback(async () => {
    if (!token || !clientId) return;
    try {
      setWordpressStatus(await api.wordpressStatus(token, clientId));
    } catch {
      // Not fatal  -  the Publishing card just shows "not connected" until this succeeds.
      setWordpressStatus(null);
    }
  }, [token, clientId]);

  async function onDisconnectWordpress() {
    if (!token || !clientId) return;
    try {
      setWordpressStatus(await api.wordpressDisconnect(token, clientId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not disconnect WordPress");
    }
  }

  async function downloadAllReports(format: ReportDownloadFormat = "pdf") {
    if (!token || !clientId || exportingReports) return;
    setExportingReports(true);
    setError("");
    try {
      await api.downloadReports(token, clientId, format);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to download reports");
    } finally {
      setExportingReports(false);
    }
  }

  function toggleTheme() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.classList.toggle("theme-light", next === "light");
    try {
      localStorage.setItem("seo-os-theme", next);
    } catch {
      /* ignore quota / private mode */
    }
  }

  function startStatusCycle(prompt: string) {
    const lines = statusLinesForPrompt(prompt);
    let i = 0;
    setThinkingStatus(lines[0]);
    if (statusTimer.current) window.clearInterval(statusTimer.current);
    statusTimer.current = window.setInterval(() => {
      i = (i + 1) % lines.length;
      setThinkingStatus(lines[i]);
    }, 2200);
  }

  function stopStatusCycle() {
    if (statusTimer.current) {
      window.clearInterval(statusTimer.current);
      statusTimer.current = null;
    }
  }

  async function typewriterMessage(
    full: string,
    agentKey?: string | null,
    role: "agent" | "system" = "agent"
  ) {
    const id = crypto.randomUUID();
    setMessages((m) => [
      ...m,
      { id, role, content: "", agent_key: agentKey || undefined, streaming: true, created_at: new Date().toISOString() },
    ]);
    // Word-ish chunks for a Claude-like cadence (faster on long text)
    const chunk =
      full.length > 400 ? 5 : full.length > 180 ? 3 : 2;
    for (let i = 0; i < full.length; i += chunk) {
      const slice = full.slice(0, i + chunk);
      setMessages((m) => m.map((msg) => (msg.id === id ? { ...msg, content: slice } : msg)));
      await sleep(full.length > 500 ? 8 : 14);
    }
    setMessages((m) =>
      m.map((msg) => (msg.id === id ? { ...msg, content: full, streaming: false } : msg))
    );
  }

  async function handleStreamEvent(ev: ChatEvent) {
    if (ev.type === "thinking") {
      setThinking(true);
      if (ev.agent_key) setActiveAgent(ev.agent_key);
      return;
    }
    if (ev.type === "done") {
      setThinking(false);
      stopStatusCycle();
      return;
    }
    if (ev.type === "error") {
      setError(ev.content || "Agent error");
      setThinking(false);
      stopStatusCycle();
      return;
    }
    if (ev.type === "active_agent") {
      setActiveAgent(ev.agent_key || (ev.payload?.agent_key as string) || null);
      return;
    }
    if (ev.type === "phase_status" && ev.payload) {
      setStatuses((s) => mapPhaseStatuses(ev.payload as Record<string, unknown>, s));
      if (ev.payload.overall_readiness_score != null) {
        setReadiness((r) => ({
          ...r,
          overall: Number(ev.payload!.overall_readiness_score),
        }));
      }
      return;
    }
    if (ev.type === "job_progress") {
      const msg =
        ev.content ||
        String((ev.payload as { message?: string })?.message || "Working...");
      setThinking(true);
      setThinkingStatus(msg);
      return;
    }
    if (ev.type === "system_notice") {
      // Keep thinking UI; show soft progress as status, then a quiet system line
      const text = ev.content || String((ev.payload as { message?: string })?.message || "");
      if (text && !isStaleGateNotice(text)) {
        setThinkingStatus(text);
        setMessages((m) => [
          ...m,
          {
            id: crypto.randomUUID(),
            role: "system",
            content: text,
            changeReply: changeFlow.current || undefined,
            created_at: new Date().toISOString(),
          },
        ]);
        await sleep(180);
      }
      return;
    }
    if (ev.type === "agent_message") {
      setThinking(false);
      stopStatusCycle();
      setActiveAgent(ev.agent_key || null);
      if (ev.content) {
        if (changeFlow.current) {
          changeFlow.current = false;
          setMessages((m) => [
            ...m,
            {
              id: crypto.randomUUID(),
              role: "agent",
              content: ev.content || "",
              agent_key: ev.agent_key,
              changeReply: true,
              created_at: new Date().toISOString(),
            },
          ]);
        } else {
          await typewriterMessage(ev.content, ev.agent_key, "agent");
        }
      }
      await sleep(120);
      return;
    }
    if (ev.type === "structured_card") {
      setThinking(false);
      stopStatusCycle();
      const isChange = changeFlow.current;
      changeFlow.current = false;
      const payload = (ev.payload || {}) as Record<string, unknown>;
      const cardTitle = String(payload.title || payload.card_type || "Report");
      if (isChange) {
        setReportFocusKey(null);
      }
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "agent",
          content: isChange ? `Updated ${cardTitle} from your change request.` : "",
          agent_key: (payload.agent_key as string) || ev.agent_key,
          card: payload,
          created_at: new Date().toISOString(),
          changeReply: isChange,
        },
      ]);
      await sleep(200);
      return;
    }
    if (ev.type === "checkpoint") {
      return;
    }
  }

  /** Prefer streamTurn for chat turns */
  async function streamTurn(session: string, content: string): Promise<boolean> {
    if (!token || streamLock.current) return false;
    streamLock.current = true;
    setError("");
    setThinking(true);
    startStatusCycle(content);
    try {
      await api.postMessageStream(token, session, content, handleStreamEvent);
      await refreshReadiness();
      // After a topic draft run, pull CDP so empty twin cards cannot hide the markdown.
      try {
        if (clientId) {
          const c = await api.getClient(token, clientId);
          if (c.profile) {
            setClient(c);
            setStatuses(mapPhaseStatuses(c.profile as unknown as Record<string, unknown>));
            setMessages((prev) =>
              hydrateProductionDraftFromProfile(
                hydrateStrategyQueueFromProfile(prev, c.profile),
                c.profile
              )
            );
          }
        }
      } catch {
        /* non-fatal — stream events already appended */
      }
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
      return false;
    } finally {
      setThinking(false);
      stopStatusCycle();
      streamLock.current = false;
    }
  }

  useEffect(() => {
    if (!token || !clientId) return;
    if (bootClientId.current === clientId) return;
    bootClientId.current = clientId;
    setMessages([]);
    setSessionId(null);
    setError("");
    (async () => {
      try {
        const c = await api.getClient(token, clientId);
        setClient(c);
        if (c.profile) {
          setStatuses(mapPhaseStatuses(c.profile as unknown as Record<string, unknown>));
          setReadiness((r) => ({
            ...r,
            overall: Number(c.profile?.overall_readiness_score || 0),
            ready_for_phase5: !!c.profile?.ready_for_phase5,
          }));
        }

        // Reuse the latest session that has messages (survives refresh / return).
        // Fetched together rather than one-at-a-time: the old loop awaited up to 8
        // round-trips in sequence before the chat could render, and each can return
        // several hundred rows.
        const sessions = await api.listSessions(token, clientId);
        const candidates = sessions.slice(0, 8);
        let sid: string | null = candidates[0]?.id ?? null;
        let history: UiMessage[] = [];
        if (candidates.length) {
          const loaded = await Promise.all(
            candidates.map((s) =>
              api
                .messages(token, s.id)
                .then((stored) => ({ id: s.id, mapped: mapStoredMessages(stored) }))
                // One unreadable session must not blank the whole chat.
                .catch(() => ({ id: s.id, mapped: [] as UiMessage[] }))
            )
          );
          const firstWithHistory = loaded.find((r) => r.mapped.length);
          if (firstWithHistory) {
            sid = firstWithHistory.id;
            history = hydrateProductionDraftFromProfile(
              hydrateStrategyQueueFromProfile(
                hydrateValidationReports(firstWithHistory.mapped, c.profile),
                c.profile
              ),
              c.profile
            );
          }
        }
        if (!sid) {
          const session = await api.createSession(token, clientId);
          sid = session.id;
        }
        setSessionId(sid);
        await refreshReadiness();
        void refreshWordpressStatus();

        if (history.length) {
          setMessages(history);
          const lastAgent = [...history].reverse().find((m) => m.agent_key);
          if (lastAgent?.agent_key) setActiveAgent(lastAgent.agent_key);
          const demandDone =
            c.profile?.search_demand_status === "complete" ||
            c.profile?.search_demand_status === "pending_signoff";
          const hasDemandCard = history.some(
            (m) => m.card?.card_type === "search_demand_report"
          );
          if (!demandDone && !hasDemandCard) {
            const lastUser = [...history].reverse().find((m) => m.role === "user");
            const lastText = (lastUser?.content || "").toLowerCase();
            const askedDemand =
              lastText.includes("keyword") || lastText.includes("search demand");
            if (askedDemand || c.profile?.competitor_status === "complete") {
              setPendingHandoff({
                prompt:
                  askedDemand && lastUser
                    ? lastUser.content
                    : "Run keyword research / search demand",
                label: "Run keyword research / search demand",
              });
            }
          }
          return;
        }

        const allowedDiscovery = canTrigger("discovery_agent");
        if (
          allowedDiscovery &&
          (c.profile?.discovery_status === "not_started" || !c.profile)
        ) {
          await streamTurn(sid, `Start onboarding discovery for ${c.name}`);
        } else {
          const skills = (user?.permissions || [])
            .filter((p) => p.can_trigger)
            .map((p) => p.label)
            .join(", ");
          setMessages([
            {
              id: crypto.randomUUID(),
              role: "system",
              content: skills
                ? `Team memory ready for ${user?.role_label || user?.role_name}. Your skills: ${skills}. Shared updates from other roles appear in the panel.`
                : `Team memory ready for ${user?.role_label || user?.role_name}. No Phase 1-12 skills to trigger  -  review shared updates from other roles in the panel.`,
            },
          ]);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to open session");
      }
    })();
  }, [token, clientId, refreshReadiness, refreshWordpressStatus, canTrigger, user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking, thinkingStatus]);

  useEffect(() => {
    return () => stopStatusCycle();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openCommandBar();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!commandFocusTick) return;
    if (sidebarNav !== "memory" && sidebarNav !== "reports") return;
    commandRef.current?.focus();
  }, [commandFocusTick, sidebarNav]);

  async function send(content: string) {
    if (!token || !sessionId || !content.trim()) return;
    if (streamLock.current) {
      setError("Still running the previous step  -  wait for it to finish, then try again.");
      return;
    }
    const text = content.trim();
    // Dedupe accidental double-clicks / re-approve auto-continues
    setMessages((m) => {
      const last = m[m.length - 1];
      if (last?.role === "user" && last.content === text) return m;
      return [...m, { id: crypto.randomUUID(), role: "user", content: text }];
    });
    setInput("");
    const ok = await streamTurn(sessionId, text);
    if (ok) {
      setPendingHandoff(null);
    } else {
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content:
            "That step did not start. Confirm the API is running on port 8000, then click the button again.",
        },
      ]);
    }
  }

  useEffect(() => {
    if (!sessionId || !token || oauthHandled.current) return;
    const oauth = searchParams.get("oauth");
    if (!oauth) return;
    oauthHandled.current = true;
    const provider = searchParams.get("provider") || "google";
    const label = searchParams.get("label") || provider.replaceAll("_", " ");
    const message = searchParams.get("message");
    setSearchParams({}, { replace: true });
    if (oauth === "ok") {
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: `Connected ${label}. Re-running scoped tracking check...`,
        },
      ]);
      void send(`Re-check tracking now that ${provider} access is granted`);
      void refreshReadiness();
    } else {
      setError(message || "Google OAuth failed");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on return from Google
  }, [sessionId, token, searchParams]);

  useEffect(() => {
    const panel = searchParams.get("panel");
    if (panel === "engine-room" && canViewEngineRoom) {
      setSidebarNav("engine_room");
      setSearchParams({}, { replace: true });
    } else if (panel === "cost-tracker" && canViewCostTracker) {
      setSidebarNav("cost_tracker");
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, canViewEngineRoom, canViewCostTracker, setSearchParams]);

  function openCommandBar(draft?: string) {
    if (draft) setInput(draft);
    setChangeDockOpen(true);
    setSidebarNav((current) => (current === "memory" || current === "reports" ? current : "reports"));
    setCommandFocusTick((n) => n + 1);
  }

  function onSubmitChange(e: FormEvent) {
    e.preventDefault();
    void sendChangeRequest(input);
  }

  async function sendChangeRequest(content: string) {
    if (!token || !sessionId || !content.trim()) return;
    if (streamLock.current) {
      setError("Still applying the previous change  -  wait for it to finish.");
      return;
    }
    const text = content.trim();
    setChangeDockOpen(true);
    changeFlow.current = true;
    setMessages((m) => {
      const last = m[m.length - 1];
      if (last?.role === "user" && last.content === text) return m;
      return [...m, { id: crypto.randomUUID(), role: "user", content: text, changeRequest: true }];
    });
    setInput("");
    const ok = await streamTurn(sessionId, text);
    if (!ok) {
      changeFlow.current = false;
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: "Could not apply that change. Check the API is running, then try again.",
          changeReply: true,
        },
      ]);
    }
  }

  function canAct(agentKey?: string | null) {
    if (!agentKey) return false;
    if (!canApprove(agentKey)) return false;
    // Hide Approve on already-completed phases (stops re-approve  ->  auto-continue loops)
    const statusByAgent: Record<string, string> = {
      discovery_agent: statuses.discovery,
      tracking_access_agent: statuses.tracking,
      website_situation_agent: statuses.website,
      competitor_market_agent: statuses.competitor,
      search_demand: statuses.search_demand,
      content_strategy: statuses.seo_strategy,
      site_architecture: statuses.site_architecture,
      technical_seo: statuses.technical_seo,
      content_audit: statuses.content_audit,
      content_planning: statuses.content_planning,
      content_production: statuses.content_production,
      on_page_seo: statuses.on_page_seo,
      publishing: statuses.publishing,
    };
    const st = statusByAgent[agentKey];
    return st === "pending_signoff" || st === "in_progress";
  }

  const latestReports = useMemo(() => reportsInPipelineOrder(messages), [messages]);

  function openPhaseReport(phaseKey: string) {
    setReportFocusKey(phaseKey);
    setSidebarNav("reports");
  }

  useEffect(() => {
    const paneEl = reportPaneRef.current;
    if (!paneEl || sidebarNav !== "reports") return;
    const scrollPane: HTMLElement = paneEl;

    function onWheel(e: WheelEvent) {
      if (e.ctrlKey || !e.deltaY) return;
      let node = e.target as HTMLElement | null;
      while (node && node !== scrollPane) {
        const style = getComputedStyle(node);
        const canY =
          (style.overflowY === "auto" || style.overflowY === "scroll") &&
          node.scrollHeight > node.clientHeight + 1;
        if (canY) {
          const atTop = e.deltaY < 0 && node.scrollTop <= 0;
          const atBottom = e.deltaY > 0 && node.scrollTop + node.clientHeight >= node.scrollHeight - 1;
          if (!atTop && !atBottom) return;
        }
        node = node.parentElement;
      }
      const max = scrollPane.scrollHeight - scrollPane.clientHeight;
      if (max <= 0) return;
      const next = Math.min(max, Math.max(0, scrollPane.scrollTop + e.deltaY));
      if (next === scrollPane.scrollTop) return;
      scrollPane.scrollTop = next;
      e.preventDefault();
    }

    scrollPane.addEventListener("wheel", onWheel, { passive: false });
    return () => scrollPane.removeEventListener("wheel", onWheel);
  }, [sidebarNav]);
  const approvalQueue = useMemo(
    () => CONTROL_PHASES.filter((p) => statuses[p.key] === "pending_signoff"),
    [statuses]
  );
  const changeThread = useMemo(
    () => messages.filter((m) => m.changeRequest || m.changeReply).slice(-6),
    [messages]
  );
  const awaitingChangeReply = useMemo(() => {
    if (!thinking) return false;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.changeReply) return false;
      if (m.changeRequest) return true;
    }
    return false;
  }, [thinking, messages]);

  useEffect(() => {
    if (!changeDockOpen || awaitingChangeReply || thinking) return;
    if (!changeThread.length) return;
    const id = window.setTimeout(() => setChangeDockOpen(false), 1200);
    return () => window.clearTimeout(id);
  }, [changeDockOpen, awaitingChangeReply, thinking, changeThread.length]);

  useEffect(() => {
    if (sidebarNav !== "reports") return;
    const id = window.requestAnimationFrame(() => {
      if (reportFocusKey) {
        document.getElementById(`report-phase-${reportFocusKey}`)?.scrollIntoView({ block: "start" });
        return;
      }
      reportsEndRef.current?.scrollIntoView({ block: "end" });
    });
    return () => window.cancelAnimationFrame(id);
  }, [sidebarNav, reportFocusKey, latestReports.length, changeThread.length]);

  const phasesCompleted = useMemo(
    () => CONTROL_PHASES.filter((p) => statuses[p.key] === "complete").length,
    [statuses]
  );
  const progressPct = useMemo(
    () => Math.round((phasesCompleted / CONTROL_PHASES.length) * 100),
    [phasesCompleted]
  );
  const activePipelineKey = useMemo(() => {
    const running = CONTROL_PHASES.find((p) => statuses[p.key] === "in_progress");
    if (running) return running.key;
    const signoff = CONTROL_PHASES.find((p) => statuses[p.key] === "pending_signoff");
    if (signoff) return signoff.key;
    const next = CONTROL_PHASES.find((p) => statuses[p.key] !== "complete");
    return next?.key || "publishing";
  }, [statuses]);
  const recentAlerts = useMemo(() => {
    return messages
      .filter((m) => (m.role === "system" || m.changeReply) && m.content.trim())
      .slice(-5)
      .reverse();
  }, [messages]);
  const activePhaseLabel = useMemo(() => {
    const phase = CONTROL_PHASES.find((p) => p.key === activePipelineKey);
    return phase?.label || "Pipeline";
  }, [activePipelineKey]);
  const liveActivity = useMemo(() => {
    const rows = messages
      .filter((m) => (m.role === "system" || m.changeReply) && m.content.trim())
      .slice(-8);
    if (thinking && thinkingStatus) {
      rows.push({
        id: "live-status",
        role: "system",
        content: thinkingStatus,
        created_at: new Date().toISOString(),
      });
    }
    return rows.slice(-8).reverse();
  }, [messages, thinking, thinkingStatus]);
  const healthLabel = readiness.overall >= 70 ? "Good" : readiness.overall >= 40 ? "Average" : "Building";
  const healthTone = readiness.overall >= 70 ? "good" : "warn";

  function pipeClass(status: string, key: string): string {
    if (status === "complete") return "is-complete";
    if (status === "pending_signoff") return "is-signoff";
    if (status === "in_progress" || key === activePipelineKey) return "is-running";
    return "is-waiting";
  }

  async function onCardAction(
    agentKey: string,
    action: string,
    edits?: Record<string, unknown>
  ) {
    if (!token || !clientId) return;
    try {
      const res = await api.reviewPhase(token, clientId, agentKey, action, edits);
      setStatuses(mapPhaseStatuses(res.phase_statuses as Record<string, unknown>));
      setReadiness((r) => ({ ...r, overall: res.overall_readiness_score }));
      const handoff = res.handoff;
      const nextHints: Record<string, string> = {
        discovery_agent: "Handoff: Discovery  ->  Tracking. Next: run tracking check.",
        tracking_access_agent:
          action === "flag_for_client"
            ? "Tracking flagged  -  fix with client, then re-check before trusting Phase 3 anomalies."
            : "Handoff: Tracking  ->  Website. Next: run website situation analysis.",
        website_situation_agent: "Handoff: Website  ->  Competitors. Next: run competitor analysis.",
        competitor_market_agent: "Handoff: Competitors  ->  Search Demand. Next: run keyword research.",
        search_demand: "Handoff: Search Demand  ->  Content Strategy. Approve then continue.",
        content_strategy: "Handoff: Strategy  ->  Site Architecture. Assign URLs to the queue.",
        site_architecture: "Handoff: Architecture  ->  Technical SEO.",
        technical_seo: "Handoff: Technical SEO  ->  Content Audit, then Planning.",
        content_audit: "Handoff: Audit  ->  Content Planning.",
        content_planning: "Handoff: Planning  ->  Production. Brief locked create/refresh pages.",
        content_production: "Handoff: Production  ->  On-Page SEO.",
        on_page_seo: "Handoff: On-Page  ->  Publishing.",
        publishing: "Pipeline complete  -  publish package is in shared memory (mock CMS only).",
      };
      const nextPrompts: Record<string, string> = {
        discovery_agent: "Run tracking check",
        tracking_access_agent: "Run website situation analysis  -  SEO audit every page",
        website_situation_agent: "Refresh competitor scan",
        competitor_market_agent: "Run keyword research / search demand",
        search_demand: "Run content strategy and content calendar",
        content_strategy: "Run site architecture and click-depth audit",
        site_architecture: "Run technical SEO audit",
        technical_seo: "Run existing content audit",
        content_audit: "Merge strategy, architecture, and audit into a locked page roadmap",
        content_planning: "Run content production briefs and drafts",
        content_production: "Run on-page SEO package",
        on_page_seo: "Run publishing checklist and IndexNow preview",
      };
      const nextAgent: Record<string, string> = {
        discovery_agent: "tracking_access_agent",
        tracking_access_agent: "website_situation_agent",
        website_situation_agent: "competitor_market_agent",
        competitor_market_agent: "search_demand",
        search_demand: "content_strategy",
        content_strategy: "site_architecture",
        site_architecture: "technical_seo",
        technical_seo: "content_audit",
        content_audit: "content_planning",
        content_planning: "content_production",
        content_production: "on_page_seo",
        on_page_seo: "publishing",
      };
      const handoffMsg =
        (action === "approve" || action === "edit") && handoff?.message
          ? handoff.message
          : action === "approve" || action === "edit" || action === "flag_for_client"
            ? nextHints[agentKey] || ""
            : "Phase returned to in progress  -  re-run when ready.";
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: `Finding ${action}d for ${AGENT_NAME[agentKey] || agentKey}. ${handoffMsg}`,
        },
      ]);
      await refreshReadiness();

      // Agentic continue: only if this role can trigger the next skill
      const followAgent = (handoff?.to_agent as string | undefined) || nextAgent[agentKey];
      const followPrompt = handoff?.prompt || nextPrompts[agentKey];
      const nextStatusByFollow: Record<string, string> = {
        tracking_access_agent: statuses.tracking,
        website_situation_agent: statuses.website,
        competitor_market_agent: statuses.competitor,
        search_demand: statuses.search_demand,
        content_strategy: statuses.seo_strategy,
        site_architecture: statuses.site_architecture,
        technical_seo: statuses.technical_seo,
        content_audit: statuses.content_audit,
        content_planning: statuses.content_planning,
        content_production: statuses.content_production,
        on_page_seo: statuses.on_page_seo,
        publishing: statuses.publishing,
      };
      const followStatus = followAgent ? nextStatusByFollow[followAgent] : "";
      if (followPrompt && followAgent && (action === "approve" || action === "edit")) {
        setPendingHandoff({
          prompt: followPrompt,
          label: `Run ${handoff?.to_label || followPrompt}`,
        });
      }
      if (
        (action === "approve" || action === "edit") &&
        followPrompt &&
        followAgent &&
        canTrigger(followAgent) &&
        sessionId &&
        !streamLock.current &&
        followStatus !== "complete" &&
        followStatus !== "pending_signoff"
      ) {
        await send(followPrompt);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    }
  }

  async function onGrant(provider: string) {
    const label = provider === "skip" ? "Google APIs" : provider.replaceAll("_", " ");
    setMessages((m) => [
      ...m,
      {
        id: crypto.randomUUID(),
        role: "system",
        content: `Skipped ${label}. Tracking continues from live-site HTML  -  no Google login.`,
      },
    ]);
  }

  async function onSubmitQuestionnaire(fields: Record<string, unknown>) {
    if (!token || !clientId) return;
    setThinking(true);
    startStatusCycle("questionnaire discovery");
    try {
      const res = await api.submitQuestionnaire(token, clientId, fields);
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: "Questionnaire submitted  -  scoring completeness and building sign-off card...",
        },
      ]);
      for (const ev of res.events || []) {
        await handleStreamEvent(ev);
      }
      setThinking(false);
      stopStatusCycle();
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Questionnaire submit failed");
      setThinking(false);
      stopStatusCycle();
    }
  }

  async function onUploadCdd(file: File) {
    if (!token || !clientId) throw new Error("Not ready");
    const res = await api.importDiscoveryDocument(token, clientId, file);
    setMessages((m) => [
      ...m,
      {
        id: crypto.randomUUID(),
        role: "system",
        content: `Imported CDD "${res.filename}" - ${Object.keys(res.fields || {}).length} fields applied to the questionnaire.`,
      },
    ]);
    return res.fields || {};
  }

  async function onSubmitKnownChanges(fields: Record<string, unknown>) {
    if (!token || !clientId) return;
    setThinking(true);
    startStatusCycle("known changes tracking");
    try {
      const res = await api.submitKnownChanges(token, clientId, fields);
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: "Known-changes logged  -  building T6 readiness scoring & sign-off...",
        },
      ]);
      for (const ev of res.events || []) {
        await handleStreamEvent(ev);
      }
      setThinking(false);
      stopStatusCycle();
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Known-changes submit failed");
      setThinking(false);
      stopStatusCycle();
    }
  }

  function nextStepHint(): string {
    if (statuses.discovery !== "complete") {
      return statuses.discovery === "pending_signoff"
        ? "Next: Approve Discovery, then run tracking check."
        : "Next: finish Discovery (submit questionnaire  ->  Approve).";
    }
    if (statuses.tracking !== "complete") {
      return statuses.tracking === "pending_signoff"
        ? "Next: Tech SEO Approve tracking baseline (T6), then run website situation analysis."
        : statuses.tracking === "in_progress"
          ? "Next: submit T5 known-changes, then T6 sign-off  -  or re-run tracking check."
          : "Next: run tracking check (T1-T4 automated).";
    }
    if (statuses.website !== "complete") {
      return statuses.website === "pending_signoff"
        ? "Next: Approve website report, then run competitor analysis."
        : "Next: run website situation analysis.";
    }
    if (statuses.competitor !== "complete") {
      return statuses.competitor === "pending_signoff"
        ? "Next: Approve competitor landscape, then run keyword research."
        : "Next: run competitor analysis.";
    }
    if (!readiness.ready_for_phase5) {
      return "Next: run keyword research / search demand (Phase 5).";
    }
    if (statuses.search_demand !== "complete") {
      return statuses.search_demand === "pending_signoff"
        ? "Next: Approve Search Demand, then run content strategy."
        : "Next: run keyword research / search demand (Phase 5).";
    }
    if (statuses.seo_strategy !== "complete") {
      return statuses.seo_strategy === "pending_signoff"
        ? "Next: Strategist Approve content strategy."
        : "Next: run content strategy / SEO calendar (Phase 6).";
    }
    if (statuses.site_architecture !== "complete") {
      return statuses.site_architecture === "pending_signoff"
        ? "Next: SEO Strategist Approve site architecture blueprint."
        : "Next: run site architecture / click-depth audit.";
    }
    if (statuses.technical_seo !== "complete") {
      return statuses.technical_seo === "pending_signoff"
        ? "Next: Technical SEO Specialist Approve technical SEO report."
        : "Next: run technical SEO audit (Phase 7).";
    }
    if (statuses.content_audit !== "complete") {
      return statuses.content_audit === "pending_signoff"
        ? "Next: Approve content audit, then run content planning."
        : "Next: run existing content audit (Phase 8).";
    }
    if (statuses.content_planning !== "complete") {
      return statuses.content_planning === "pending_signoff"
        ? "Next: Approve content roadmap, then run content production."
        : "Next: merge strategy + architecture + audit into a locked roadmap (Phase 9).";
    }
    if (statuses.content_production !== "complete") {
      return statuses.content_production === "pending_signoff"
        ? "Next: Approve the draft, then run on-page SEO."
        : "Next: pick a priority topic and draft the full page (Phase 10).";
    }
    if (statuses.on_page_seo !== "complete") {
      return statuses.on_page_seo === "pending_signoff"
        ? "Next: Approve on-page package, then run publishing."
        : "Next: run on-page SEO package (Phase 11).";
    }
    if (statuses.publishing !== "complete") {
      return statuses.publishing === "pending_signoff"
        ? "Next: Approve publishing package to finish Phases 7-12."
        : "Next: run publishing checklist (Phase 12).";
    }
    return "Phases 1-12 complete  -  strategy through publish package are in shared memory.";
  }

  function renderCard(card: Record<string, unknown>) {
    const type = String(card.card_type || "");
    const agentKey = String(card.agent_key || "");
    if (
      type === "discovery_profile" ||
      type === "discovery_preresearch" ||
      type === "discovery_questionnaire" ||
      type === "discovery_completeness" ||
      type === "discovery_rerun_confirm"
    ) {
      return (
        <DiscoveryCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a, edits) => onCardAction(agentKey, a, edits)}
          onSubmitQuestionnaire={onSubmitQuestionnaire}
          onUploadCdd={onUploadCdd}
        />
      );
    }
    if (
      type === "tracking_health" ||
      type === "oauth_request" ||
      type === "tracking_t1_access" ||
      type === "tracking_t2_audit" ||
      type === "tracking_t3_conversions" ||
      type === "tracking_t4_baseline" ||
      type === "tracking_t5_known_changes" ||
      type === "tracking_t6_signoff"
    ) {
      return (
        <TrackingCard
          payload={card}
          canAct={canAct(agentKey)}
          canGrant={true}
          onAction={(a) => onCardAction(agentKey, a)}
          onGrant={onGrant}
          onSubmitKnownChanges={onSubmitKnownChanges}
        />
      );
    }
    if (type === "website_audit") {
      return (
        <WebsiteCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "broken_link_report") {
      return (
        <BrokenLinkCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "on_page_seo_report") {
      return (
        <OnPageSeoCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "technical_seo_report") {
      return (
        <TechnicalSeoCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "seo_audit_report") {
      return (
        <SeoAuditCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "competitor_landscape") {
      return (
        <CompetitorCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
          onAddManual={async (name, url) => {
            if (!token || !clientId) return;
            try {
              await api.addCompetitor(token, clientId, name, url);
              setMessages((m) => [
                ...m,
                {
                  id: crypto.randomUUID(),
                  role: "system",
                  content: `Added competitor ${name} (${url}) for the next scan (Architecture v1.9 override).`,
                },
              ]);
            } catch (e) {
              setError(e instanceof Error ? e.message : "Failed to add competitor");
            }
          }}
        />
      );
    }
    if (type === "search_demand_report") {
      return (
        <SearchDemandCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "content_strategy_report") {
      return (
        <ContentStrategyCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "site_architecture_blueprint") {
      return (
        <SiteArchitectureCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "content_audit_report") {
      return (
        <ContentAuditCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "content_planning_report") {
      return (
        <ContentPlanningCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
        />
      );
    }
    if (type === "content_production_report") {
      return (
        <ContentProductionCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a, edits) => onCardAction(agentKey, a, edits)}
          onDraftTopic={
            thinking
              ? undefined
              : (prompt) => {
                  void send(prompt);
                }
          }
        />
      );
    }
    if (type === "publishing_report") {
      return (
        <PublishingCard
          payload={card}
          canAct={canAct(agentKey)}
          onAction={(a) => onCardAction(agentKey, a)}
          wordpressStatus={wordpressStatus}
          onConnectWordPress={() => setWordpressModalOpen(true)}
          onDisconnectWordPress={onDisconnectWordpress}
        />
      );
    }
    if (type === "readiness_score") {
      return <ReadinessCard payload={card} />;
    }
    return (
      <div className="structured-card">
        <h3 className="card-title">{String(card.title || humanLabel(type) || "Update")}</h3>
        <FieldGrid data={card} skipKeys={["card_type", "agent_key", "actions", "required_role"]} />
      </div>
    );
  }

  return (
    <div className="cc-shell">
      <aside className="cc-sidebar">
        <div className="cc-brand">
          Radius OS
          <span>Command Center</span>
        </div>
        <nav className="cc-nav" aria-label="Main">
          <div className="cc-nav-group">
            <div className="cc-nav-label">Workspace</div>
            <button type="button" className={sidebarNav === "command" ? "is-active" : ""} onClick={() => setSidebarNav("command")}>
              <span className="cc-nav-ico">01</span> Overview
            </button>
            <Link to="/app">
              <span className="cc-nav-ico">02</span> Clients
            </Link>
            <button type="button" className={sidebarNav === "operations" ? "is-active" : ""} onClick={() => setSidebarNav("operations")}>
              <span className="cc-nav-ico">03</span> Operations
            </button>
            <button type="button" className={sidebarNav === "reports" ? "is-active" : ""} onClick={() => { setReportFocusKey(null); setSidebarNav("reports"); }}>
              <span className="cc-nav-ico">04</span> Reports
            </button>
            {canViewCostTracker ? (
              <button
                type="button"
                className={sidebarNav === "cost_tracker" ? "is-active" : ""}
                onClick={() => setSidebarNav("cost_tracker")}
              >
                <span className="cc-nav-ico">05</span> Cost tracker
              </button>
            ) : null}
          </div>
          <div className="cc-nav-group">
            <div className="cc-nav-label">Automation</div>
            <button type="button" className={sidebarNav === "agents" ? "is-active" : ""} onClick={() => setSidebarNav("agents")}>
              <span className="cc-nav-ico">06</span> Agents
            </button>
            <button type="button" className={sidebarNav === "command" ? "is-active" : ""} onClick={() => setSidebarNav("command")}>
              <span className="cc-nav-ico">07</span> Runs
            </button>
            {canViewEngineRoom ? (
              <button
                type="button"
                className={sidebarNav === "engine_room" ? "is-active" : ""}
                onClick={() => setSidebarNav("engine_room")}
              >
                <span className="cc-nav-ico">08</span> Engine room
              </button>
            ) : null}
          </div>
          <div className="cc-nav-group">
            <div className="cc-nav-label">Knowledge</div>
            <button type="button" className={sidebarNav === "memory" ? "is-active" : ""} onClick={() => setSidebarNav("memory")}>
              <span className="cc-nav-ico">09</span> Client Memory
            </button>
          </div>
        </nav>
        <div className="cc-sidebar-foot">
          <strong>{user?.role_label || user?.role_name || "Team"}</strong>
          <span>{client?.name || "No client"}</span>
          <button type="button" className="btn btn-ghost" style={{ fontSize: 11, padding: "6px 8px", marginTop: 8 }} onClick={logout}>
            Log out
          </button>
        </div>
      </aside>

      <div className="cc-main">
        <header className="cc-topbar">
          <div>
            <p className="cc-kicker">Command Center</p>
            <h1>
              {sidebarNav === "engine_room"
                ? "Engine room"
                : sidebarNav === "cost_tracker"
                  ? "Cost tracker"
                  : client?.name || "Radius OS"}
            </h1>
          </div>
          <div className="cc-topbar-meta">
            <button
              type="button"
              className="cc-theme-toggle"
              onClick={toggleTheme}
              aria-pressed={theme === "light"}
              title={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
            >
              {theme === "light" ? "Dark" : "Light"}
            </button>
            <div className={`cc-status-live${thinking ? " is-running" : ""}`}>
              <i />
              {thinking ? thinkingStatus : "System idle"}
            </div>
          </div>
        </header>

        {error ? <div className="error-banner" style={{ margin: "0 24px", flexShrink: 0 }}>{error}</div> : null}

        {sidebarNav === "memory" ? (
          <div className="cc-workspace cc-memory">
            <RolePlayground
              playground={playground}
              loading={playgroundLoading}
              variant="sidebar"
              clientId={clientId}
              token={token}
              onDownloadReports={(format) => downloadAllReports(format)}
              downloadingReports={exportingReports}
              onError={setError}
            />
          </div>
        ) : sidebarNav === "agents" ? (
          <div className="cc-workspace">
            <p className="cc-kicker">Agent library</p>
            <h2 style={{ margin: 0, fontSize: 20 }}>Agents are the engine. Operations are the product.</h2>
            <div className="cc-kpi-strip" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
              {CONTROL_PHASES.map((phase) => (
                <article key={phase.key} className="cc-kpi">
                  <span className="cc-kpi-label">{phase.label}</span>
                  <span className="cc-kpi-status">{phaseStatusLabel(statuses[phase.key])}</span>
                  <p style={{ margin: "8px 0 0", fontSize: 12, color: "var(--text-secondary)" }}>
                    {AGENT_PURPOSE[String(phase.key)]}
                  </p>
                </article>
              ))}
            </div>
          </div>
        ) : sidebarNav === "engine_room" ? (
          <div className="cc-workspace">
            <EngineRoomPanel token={token} onError={setError} />
          </div>
        ) : sidebarNav === "cost_tracker" ? (
          <div className="cc-workspace">
            <CostTrackerPanel
              token={token}
              clientId={clientId}
              clientName={client?.name}
              onError={setError}
            />
          </div>
        ) : sidebarNav === "reports" ? (
          <div className="cc-workspace cc-report-pane" ref={reportPaneRef}>
            <p className="cc-kicker">Radius OS Report</p>
            <h2 style={{ margin: 0, fontSize: 20 }}>{client?.name}</h2>
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
              {Math.round(readiness.overall)} / 100. {healthLabel}. {nextStepHint()}
            </p>
            {reportFocusKey && !latestReports.some((m) => reportPhaseKey(m) === reportFocusKey) ? (
              <p style={{ margin: 0, fontSize: 13, color: "var(--warning-bright)" }}>
                No report yet for {CONTROL_PHASES.find((p) => p.key === reportFocusKey)?.label || reportFocusKey}. Run that stage first.
              </p>
            ) : null}
            {latestReports.map((m) => {
              if (!m.card) return null;
              if (String(m.card.card_type) === "phase_validation_report") return null;
              const phaseKey = reportPhaseKey(m);
              const isRevision = isRevisionReport(m);
              const rerunIndex = (m as any)._rerunIndex as number | undefined;
              const phaseLabel = phaseKey
                ? CONTROL_PHASES.find((p) => p.key === phaseKey)?.label?.replace(/^\d+[a-z]?. /, "")
                : null;
              const isLatest = Boolean((m as any)._isLatestRun) && !rerunIndex;
              return (
                <div
                  key={m.id}
                  id={phaseKey && !isRevision && isLatest ? `report-phase-${phaseKey}` : undefined}
                  className={`msg card-wrap${phaseKey === reportFocusKey && !isRevision && isLatest ? " is-report-focus" : ""}${isRevision ? " is-revision-report" : ""}${rerunIndex ? " is-rerun-report" : ""}`}
                >
                  {rerunIndex ? (
                    <div className="cc-revision-banner">
                      <span className="cc-revision-badge">Earlier run #{rerunIndex}</span>
                      <span>{phaseLabel ? `${phaseLabel} — previous output` : "Previous report"}</span>
                    </div>
                  ) : isRevision ? (
                    <div className="cc-revision-banner">
                      <span className="cc-revision-badge">Revised</span>
                      <span>{phaseLabel ? `${phaseLabel} — updated from your change request` : "Updated report"}</span>
                    </div>
                  ) : null}
                  <ReportCardShell card={m.card} clientId={clientId} token={token} onError={setError}>
                    {renderCard(m.card)}
                  </ReportCardShell>
                </div>
              );
            })}
            {!latestReports.length ? <p style={{ color: "var(--text-muted)" }}>No intelligence packs yet. Start an operation.</p> : null}
            <div ref={reportsEndRef} />
          </div>
        ) : (
          <div className="cc-workspace cc-overview">
            <div className="cc-kpi-strip">
              <div className="cc-kpi">
                <span className="cc-kpi-label">SEO Health</span>
                <div className="cc-kpi-metric">
                  <span className="cc-kpi-value">{Math.round(readiness.overall)}</span>
                  <span className={`cc-kpi-status ${healthTone}`}>{healthLabel}</span>
                </div>
              </div>
              <div className="cc-kpi">
                <span className="cc-kpi-label">Phases completed</span>
                <div className="cc-kpi-metric">
                  <span className="cc-kpi-value">{phasesCompleted}/{CONTROL_PHASES.length}</span>
                  <span className="cc-kpi-status">{progressPct}% of pipeline</span>
                </div>
              </div>
              <button
                type="button"
                className={`cc-kpi cc-kpi-action${kpiDetail === "issues" ? " is-open" : ""}`}
                onClick={() => setKpiDetail((d) => (d === "issues" ? null : "issues"))}
                aria-expanded={kpiDetail === "issues"}
              >
                <span className="cc-kpi-label">Issues</span>
                <div className="cc-kpi-metric">
                  <span className="cc-kpi-value">{readiness.missing.length}</span>
                  <span className="cc-kpi-status warn">Gaps in readiness</span>
                </div>
              </button>
              <button
                type="button"
                className={`cc-kpi cc-kpi-action${kpiDetail === "opportunities" ? " is-open" : ""}`}
                onClick={() => setKpiDetail((d) => (d === "opportunities" ? null : "opportunities"))}
                aria-expanded={kpiDetail === "opportunities"}
              >
                <span className="cc-kpi-label">Opportunities</span>
                <div className="cc-kpi-metric">
                  <span className="cc-kpi-value">{approvalQueue.length}</span>
                  <span className="cc-kpi-status warn">Pending sign-off</span>
                </div>
              </button>
            </div>

            {kpiDetail === "issues" ? (
              <div className="cc-kpi-detail">
                <div className="cc-kpi-detail-head">
                  <h2>Readiness gaps</h2>
                  <p>{readiness.missing.length ? `${readiness.missing.length} item${readiness.missing.length === 1 ? "" : "s"} blocking a complete picture.` : "No readiness gaps right now."}</p>
                </div>
                {readiness.missing.length ? (
                  <ul className="cc-gap-list">
                    {readiness.missing.map((gap) => {
                      const split = gap.indexOf(":");
                      const phaseKey = split > 0 ? gap.slice(0, split).trim() : "";
                      const detail = split > 0 ? gap.slice(split + 1).trim() : gap;
                      const phase = CONTROL_PHASES.find((p) => p.key === phaseKey || p.agent === phaseKey);
                      return (
                        <li key={gap}>
                          {phase ? <span className="cc-gap-phase">{phase.label}</span> : null}
                          <span>{detail}</span>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="cc-empty-note">Nothing missing in the current readiness check.</p>
                )}
              </div>
            ) : null}

            {kpiDetail === "opportunities" ? (
              <div className="cc-kpi-detail">
                <div className="cc-kpi-detail-head">
                  <h2>Pending sign-off</h2>
                  <p>
                    {approvalQueue.length
                      ? `${approvalQueue.length} phase${approvalQueue.length === 1 ? "" : "s"} waiting for human approval.`
                      : "No phases are waiting for sign-off."}
                  </p>
                </div>
                {approvalQueue.length ? (
                  <ul className="cc-gap-list">
                    {approvalQueue.map((phase) => (
                      <li key={phase.key}>
                        <span className="cc-gap-phase">{phase.label}</span>
                        <span>{PHASE_OWNER[String(phase.key)] || "Needs approval"}</span>
                        <button type="button" className="btn btn-primary cc-gap-go" onClick={() => openPhaseReport(String(phase.key))}>
                          Review
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="cc-empty-note">No pending approvals. Keep the pipeline moving from Operations.</p>
                )}
              </div>
            ) : null}

            {approvalQueue.length ? (
              <div className="cc-attention">
                <div>
                  <strong>Action required</strong>
                  <p>{approvalQueue.length} phase{approvalQueue.length === 1 ? "" : "s"} need human approval before the operation continues.</p>
                </div>
                <button type="button" className="btn btn-primary" onClick={() => setSidebarNav("reports")}>
                  Review
                </button>
              </div>
            ) : null}

            {sidebarNav === "operations" ? (
              <div className="cc-card">
                <div className="cc-card-head">
                  <div>
                    <h2>Start an operation</h2>
                    <p>What should Radius OS accomplish? The system chooses which agents to run.</p>
                  </div>
                </div>
                <div className="cc-card-body">
                  <div className="cc-ops">
                    {START_OPERATIONS.map((op) => (
                      <button
                        key={op.label}
                        type="button"
                        disabled={thinking || !sessionId || !canTrigger(op.agent)}
                        onClick={() => void send(op.prompt)}
                      >
                        {op.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}

            <div className="cc-grid-7-5">
              <section className="cc-card">
                <div className="cc-card-head">
                  <div>
                    <h2>SEO operation pipeline</h2>
                    <p>{nextStepHint()}</p>
                  </div>
                  {pendingHandoff && !thinking ? (
                    <button type="button" className="btn btn-primary" disabled={!sessionId} onClick={() => void send(pendingHandoff.prompt)}>
                      Continue
                    </button>
                  ) : null}
                </div>
                <div className="cc-card-body cc-pipeline-list">
                  {CONTROL_PHASES.map((phase) => {
                    const status = statuses[phase.key];
                    const canRun = canTrigger(phase.agent) && !thinking && !!sessionId;
                    return (
                      <div key={phase.key} className={`cc-pipe ${pipeClass(status, phase.key)}`}>
                        <span className="cc-pipe-dot" />
                        <div>
                          <div className="cc-pipe-name">{phase.label.replace(/^\d+[a-z]?. /, "")}</div>
                          <div className="cc-pipe-meta">{phaseStatusLabel(status)} · {PHASE_OWNER[String(phase.key)]}</div>
                        </div>
                        <button
                          type="button"
                          className={`btn cc-pipe-run ${pipeClass(status, phase.key) === "is-running" ? "btn-primary" : "btn-ghost"}`}
                          disabled={!canRun}
                          onClick={() => void send(phase.prompt)}
                        >
                          {status === "complete" ? "Re-run" : "Run"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="cc-card">
                <div className="cc-card-head">
                  <div>
                    <h2>Live operation monitor</h2>
                    <p>Real-time system status</p>
                  </div>
                </div>
                <div className="cc-card-body">
                  <div className="cc-monitor-kpis">
                    <div className="cc-kpi cc-kpi-compact">
                      <span className="cc-kpi-label">Active agents</span>
                      <div className="cc-kpi-metric">
                        <span className="cc-kpi-value">{thinking ? 1 : 0}</span>
                        <span className={`cc-kpi-status ${thinking ? "info" : "good"}`}>{thinking ? "Running" : "Idle"}</span>
                      </div>
                    </div>
                    <div className="cc-kpi cc-kpi-compact">
                      <span className="cc-kpi-label">Phases completed</span>
                      <div className="cc-kpi-metric">
                        <span className="cc-kpi-value">{phasesCompleted} / {CONTROL_PHASES.length}</span>
                      </div>
                    </div>
                  </div>
                  <div className="cc-monitor-list">
                    {CONTROL_PHASES.map((phase) => {
                      const st = statuses[phase.key];
                      return (
                        <button
                          type="button"
                          className="cc-monitor-row"
                          onClick={() => openPhaseReport(String(phase.key))}
                        >
                          <span>{phase.label.replace(/^\d+[a-z]? · /, "").replace(/^\d+[a-z]?. /, "")} Agent</span>
                          <span className={`cc-dot-status ${monitorStatusClass(st)}`}>
                            {monitorStatusLabel(st)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="cc-kicker cc-section-label">Resource usage</div>
                  <div className="cc-resource">
                    <div className="cc-resource-top"><span>Readiness</span><span>{Math.round(readiness.overall)}%</span></div>
                    <div className="cc-resource-track"><i className="bar-green" style={{ width: `${Math.min(100, readiness.overall)}%` }} /></div>
                  </div>
                  <div className="cc-resource">
                    <div className="cc-resource-top"><span>Pipeline</span><span>{progressPct}%</span></div>
                    <div className="cc-resource-track"><i className="bar-blue" style={{ width: `${progressPct}%` }} /></div>
                  </div>
                  <div className="cc-kicker cc-section-label">Live agent activity</div>
                  <div className="cc-activity">
                    {liveActivity.length ? liveActivity.map((m) => (
                      <div key={m.id} className="cc-activity-row">
                        <time>{new Date(m.created_at || Date.now()).toLocaleTimeString()}</time>
                        <span>{m.content}</span>
                      </div>
                    )) : <span className="cc-empty-note">No live events yet.</span>}
                  </div>
                </div>
              </section>
            </div>
          </div>
        )}

        {sidebarNav === "memory" || sidebarNav === "reports" ? (
          changeDockOpen || awaitingChangeReply ? (
          <div className="cc-changes-dock">
            <div className="cc-changes-head">
              <strong>{sidebarNav === "reports" ? "Report changes" : "Memory changes"}</strong>
              <span>
                {sidebarNav === "reports"
                  ? "Request edits below — updates appear in the thread and add a revised report at the bottom."
                  : "Request edits to locked client memory packs."}
              </span>
            </div>
            {(changeThread.length || awaitingChangeReply) ? (
              <div className="cc-changes-thread" aria-live="polite">
                {changeThread.map((m) => {
                  const text = changeThreadText(m);
                  if (!text) return null;
                  return (
                    <div
                      key={m.id}
                      className={`cc-changes-thread-item${m.changeRequest ? " is-user" : m.role === "system" ? " is-system" : " is-update"}`}
                    >
                      <span className="cc-changes-thread-role">{changeThreadLabel(m)}</span>
                      <span className="cc-changes-thread-body">{text}</span>
                    </div>
                  );
                })}
                {awaitingChangeReply ? (
                  <div className="cc-changes-thread-item is-update">
                    <span className="cc-changes-thread-role">Update</span>
                    <span className="cc-changes-thread-body cc-changes-pending">{thinkingStatus || "Applying change…"}</span>
                  </div>
                ) : null}
              </div>
            ) : null}
            <form className="cc-command" onSubmit={onSubmitChange}>
              <div className="cc-command-row">
                <span>CMD</span>
                <textarea
                  ref={commandRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={
                    sidebarNav === "reports"
                      ? "Request a change to this report..."
                      : "Request a change to this client's memory..."
                  }
                  disabled={thinking || !sessionId}
                  rows={1}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void sendChangeRequest(input);
                    }
                  }}
                  aria-label="Command"
                />
                <button type="submit" disabled={thinking || !sessionId}>Enter</button>
              </div>
              <p className="cc-command-hint">
                {sidebarNav === "reports"
                  ? "Ctrl/Cmd K to focus. Example: add keyword \"local seo melbourne\""
                  : "Ctrl/Cmd K to focus. Use for change requests on locked packs."}
              </p>
            </form>
          </div>
          ) : (
            <div className="cc-changes-toggle-wrap">
              <button
                type="button"
                className="cc-changes-toggle"
                onClick={() => {
                  setChangeDockOpen(true);
                  setCommandFocusTick((n) => n + 1);
                }}
              >
                {sidebarNav === "reports" ? "Request report change" : "Request memory change"}
              </button>
            </div>
          )
        ) : null}
      </div>

      <WordPressConnectModal
        open={wordpressModalOpen}
        token={token}
        clientId={clientId ?? null}
        onClose={() => setWordpressModalOpen(false)}
        onConnected={setWordpressStatus}
      />
    </div>
  );
}
