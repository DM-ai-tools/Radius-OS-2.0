import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ChatEvent, Client } from "../api";
import { useAuth } from "../auth";
import PhaseStatusPanel from "../components/PhaseStatusPanel";
import ThinkingIndicator from "../components/ThinkingIndicator";
import DiscoveryCard from "../components/cards/DiscoveryCard";
import TrackingCard from "../components/cards/TrackingCard";
import WebsiteCard from "../components/cards/WebsiteCard";
import BrokenLinkCard from "../components/cards/BrokenLinkCard";
import OnPageSeoCard from "../components/cards/OnPageSeoCard";
import TechnicalSeoCard from "../components/cards/TechnicalSeoCard";
import SeoAuditCard from "../components/cards/SeoAuditCard";
import CompetitorCard from "../components/cards/CompetitorCard";
import ReadinessCard from "../components/cards/ReadinessCard";
import RolePlayground from "../components/RolePlayground";
import { FieldGrid, humanLabel } from "../components/cards/PresentableValue";
import { sleep, statusLinesForPrompt } from "../lib/thinkingStatus";
import type { Playground } from "../api";

type UiMessage = {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  agent_key?: string | null;
  card?: Record<string, unknown>;
  streaming?: boolean;
};

const AGENT_CLASS: Record<string, string> = {
  discovery_agent: "agent-discovery",
  tracking_access_agent: "agent-tracking",
  website_situation_agent: "agent-website",
  competitor_market_agent: "agent-competitor",
};

const AGENT_NAME: Record<string, string> = {
  discovery_agent: "Discovery Agent",
  tracking_access_agent: "Tracking & Access Agent",
  website_situation_agent: "Website Situation Agent",
  competitor_market_agent: "Competitor & Market Agent",
  readiness_gate: "Readiness Gate",
};

const PROMPT_CHIPS = [
  { label: "Run discovery", prompt: "Run discovery", agent: "discovery_agent" },
  { label: "Tracking check", prompt: "Run tracking check", agent: "tracking_access_agent" },
  { label: "Website analysis", prompt: "Run website situation analysis — SEO audit every page", agent: "website_situation_agent" },
  { label: "Competitor scan", prompt: "Refresh competitor scan", agent: "competitor_market_agent" },
  { label: "Readiness", prompt: "Readiness gate", agent: "readiness_gate" },
];

type StoredMessage = {
  id: string;
  role: string;
  content: string;
  structured_payload: Record<string, unknown> | null;
  agent_key: string | null;
  created_at: string;
};

function mapStoredMessages(rows: StoredMessage[]): UiMessage[] {
  const out: UiMessage[] = [];
  for (const row of rows) {
    const payload = row.structured_payload;
    const eventType = String(payload?.event_type || "");

    // Noise / duplicates — checkpoint usually mirrors structured_card
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
      });
      continue;
    }

    if (row.role === "user") {
      out.push({ id: row.id, role: "user", content: row.content || "" });
      continue;
    }

    if (row.role === "system" || eventType === "system_notice" || eventType === "error") {
      if (!row.content?.trim()) continue;
      out.push({ id: row.id, role: "system", content: row.content });
      continue;
    }

    // agent_message and plain agent rows
    if (!row.content?.trim() && !payload) continue;
    out.push({
      id: row.id,
      role: "agent",
      content: row.content || "",
      agent_key: row.agent_key || undefined,
    });
  }
  return out;
}

export default function ChatPage() {
  const { clientId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { token, user, canTrigger, canApprove, logout } = useAuth();
  const [client, setClient] = useState<Client | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [thinkingStatus, setThinkingStatus] = useState("Routing your request…");
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [error, setError] = useState("");
  const [playground, setPlayground] = useState<Playground | null>(null);
  const [playgroundLoading, setPlaygroundLoading] = useState(true);
  const oauthHandled = useRef(false);
  const [statuses, setStatuses] = useState({
    discovery: "not_started",
    tracking: "not_started",
    website: "not_started",
    competitor: "not_started",
  });
  const [readiness, setReadiness] = useState({
    overall: 0,
    missing: [] as string[],
    can_gate: false,
    ready_for_phase5: false,
    threshold: 90,
  });
  const bottomRef = useRef<HTMLDivElement>(null);
  const bootClientId = useRef<string | null>(null);
  const statusTimer = useRef<number | null>(null);
  const streamLock = useRef(false);

  const refreshReadiness = useCallback(async () => {
    if (!token || !clientId) return;
    const r = await api.readiness(token, clientId);
    setReadiness({
      overall: r.overall,
      missing: r.missing,
      can_gate: r.can_gate,
      ready_for_phase5: r.ready_for_phase5,
      threshold: r.threshold,
    });
    setStatuses({
      discovery: r.statuses.discovery,
      tracking: r.statuses.tracking,
      website: r.statuses.website,
      competitor: r.statuses.competitor,
    });
    try {
      setPlaygroundLoading(true);
      const pg = await api.playground(token, clientId);
      setPlayground(pg);
    } catch {
      /* playground optional if profile thin */
    } finally {
      setPlaygroundLoading(false);
    }
  }, [token, clientId]);

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
      { id, role, content: "", agent_key: agentKey || undefined, streaming: true },
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
      setStatuses((s) => ({
        discovery: String(ev.payload!.discovery_status ?? s.discovery),
        tracking: String(ev.payload!.tracking_status ?? s.tracking),
        website: String(ev.payload!.website_status ?? s.website),
        competitor: String(ev.payload!.competitor_status ?? s.competitor),
      }));
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
        String((ev.payload as { message?: string })?.message || "Working…");
      setThinking(true);
      setThinkingStatus(msg);
      return;
    }
    if (ev.type === "system_notice") {
      // Keep thinking UI; show soft progress as status, then a quiet system line
      const text = ev.content || String((ev.payload as { message?: string })?.message || "");
      if (text) {
        setThinkingStatus(text);
        setMessages((m) => [
          ...m,
          { id: crypto.randomUUID(), role: "system", content: text },
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
        await typewriterMessage(ev.content, ev.agent_key, "agent");
      }
      await sleep(120);
      return;
    }
    if (ev.type === "structured_card") {
      setThinking(false);
      stopStatusCycle();
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "agent",
          content: "",
          agent_key: (ev.payload?.agent_key as string) || ev.agent_key,
          card: ev.payload,
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
  async function streamTurn(session: string, content: string) {
    if (!token || streamLock.current) return;
    streamLock.current = true;
    setError("");
    setThinking(true);
    startStatusCycle(content);
    try {
      await api.postMessageStream(token, session, content, handleStreamEvent);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
    } finally {
      setThinking(false);
      stopStatusCycle();
      streamLock.current = false;
      await refreshReadiness();
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
          setStatuses({
            discovery: c.profile.discovery_status,
            tracking: c.profile.tracking_status,
            website: c.profile.website_status,
            competitor: c.profile.competitor_status,
          });
          setReadiness((r) => ({
            ...r,
            overall: Number(c.profile?.overall_readiness_score || 0),
            ready_for_phase5: !!c.profile?.ready_for_phase5,
          }));
        }

        // Reuse the latest session that has messages (survives refresh / return)
        const sessions = await api.listSessions(token, clientId);
        let sid: string | null = null;
        let history: UiMessage[] = [];
        for (const s of sessions.slice(0, 8)) {
          const stored = await api.messages(token, s.id);
          const mapped = mapStoredMessages(stored);
          if (mapped.length) {
            sid = s.id;
            history = mapped;
            break;
          }
          if (!sid) sid = s.id;
        }
        if (!sid) {
          const session = await api.createSession(token, clientId);
          sid = session.id;
        }
        setSessionId(sid);
        await refreshReadiness();

        if (history.length) {
          setMessages(history);
          const lastAgent = [...history].reverse().find((m) => m.agent_key);
          if (lastAgent?.agent_key) setActiveAgent(lastAgent.agent_key);
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
                : `Team memory ready for ${user?.role_label || user?.role_name}. No Phase 1–4 skills to trigger — review shared updates from other roles in the panel.`,
            },
          ]);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to open session");
      }
    })();
  }, [token, clientId, refreshReadiness, canTrigger, user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking, thinkingStatus]);

  useEffect(() => {
    return () => stopStatusCycle();
  }, []);

  async function send(content: string) {
    if (!token || !sessionId || !content.trim() || streamLock.current) return;
    setMessages((m) => [
      ...m,
      { id: crypto.randomUUID(), role: "user", content },
    ]);
    setInput("");
    await streamTurn(sessionId, content);
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
          content: `Connected ${label}. Re-running scoped tracking check…`,
        },
      ]);
      void send(`Re-check tracking now that ${provider} access is granted`);
      void refreshReadiness();
    } else {
      setError(message || "Google OAuth failed");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on return from Google
  }, [sessionId, token, searchParams]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function canAct(agentKey?: string | null) {
    if (!agentKey) return false;
    return canApprove(agentKey);
  }

  const allowedChips = PROMPT_CHIPS.filter((c) => canTrigger(c.agent));

  async function onCardAction(
    agentKey: string,
    action: string,
    edits?: Record<string, unknown>
  ) {
    if (!token || !clientId) return;
    try {
      const res = await api.reviewPhase(token, clientId, agentKey, action, edits);
      setStatuses({
        discovery: res.phase_statuses.discovery,
        tracking: res.phase_statuses.tracking,
        website: res.phase_statuses.website,
        competitor: res.phase_statuses.competitor,
      });
      setReadiness((r) => ({ ...r, overall: res.overall_readiness_score }));
      const nextHints: Record<string, string> = {
        discovery_agent: "Next: run tracking check.",
        tracking_access_agent:
          action === "flag_for_client"
            ? "Tracking flagged — fix with client, then re-check before trusting Phase 3 anomalies."
            : "Next: run website situation analysis.",
        website_situation_agent: "Next: run competitor analysis.",
        competitor_market_agent: "Opening readiness gate…",
      };
      const nextPrompts: Record<string, string> = {
        discovery_agent: "Run tracking check",
        tracking_access_agent: "Run website situation analysis — SEO audit every page",
        website_situation_agent: "Refresh competitor scan",
        competitor_market_agent: "Readiness gate",
      };
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: `Finding ${action}d for ${AGENT_NAME[agentKey] || agentKey}. ${
            action === "approve" || action === "edit" || action === "flag_for_client"
              ? nextHints[agentKey] || ""
              : "Phase returned to in progress — re-run when ready."
          }`,
        },
      ]);
      await refreshReadiness();

      // Agentic continue: after approve/edit, auto-run the next phase so the UI doesn't stall
      if (
        (action === "approve" || action === "edit") &&
        nextPrompts[agentKey] &&
        sessionId &&
        !streamLock.current
      ) {
        await send(nextPrompts[agentKey]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    }
  }

  async function onGrant(provider: string) {
    if (!token || !clientId || !sessionId) return;
    try {
      const auth = await api.oauthAuthorizeUrl(token, clientId, provider);
      if (auth.mode === "google" && auth.url) {
        window.location.href = auth.url;
        return;
      }
      await api.mockGrant(token, clientId, provider);
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: `OAuth access granted for ${provider} (demo). Re-running scoped tracking check…`,
        },
      ]);
      await send(`Re-check tracking now that ${provider} access is granted`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Grant failed");
    }
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
          content: "Questionnaire submitted — scoring completeness and building sign-off card…",
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
        content: `Imported CDD “${res.filename}” — ${Object.keys(res.fields || {}).length} fields applied to the questionnaire.`,
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
          content: "Known-changes logged — building T6 readiness scoring & sign-off…",
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

  async function onGate() {
    if (!token || !clientId) return;
    try {
      await api.gate(token, clientId, true);
      await refreshReadiness();
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: "Readiness gate passed — Client Digital Profile unlocked for Phase 5.",
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gate failed");
    }
  }

  function nextStepHint(): string {
    if (statuses.discovery !== "complete") {
      return statuses.discovery === "pending_signoff"
        ? "Next: Approve Discovery, then run tracking check."
        : "Next: finish Discovery (submit questionnaire → Approve).";
    }
    if (statuses.tracking !== "complete") {
      return statuses.tracking === "pending_signoff"
        ? "Next: Tech SEO Approve tracking baseline (T6), then run website situation analysis."
        : statuses.tracking === "in_progress"
          ? "Next: submit T5 known-changes, then T6 sign-off — or re-run tracking check."
          : "Next: run tracking check (T1–T4 automated).";
    }
    if (statuses.website !== "complete") {
      return statuses.website === "pending_signoff"
        ? "Next: Approve website report, then run competitor analysis."
        : "Next: run website situation analysis.";
    }
    if (statuses.competitor !== "complete") {
      return statuses.competitor === "pending_signoff"
        ? "Next: Approve competitor landscape, then run readiness gate."
        : "Next: run competitor analysis.";
    }
    return "Next: run readiness gate when score ≥ threshold.";
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
        />
      );
    }
    if (type === "readiness_score") {
      return (
        <ReadinessCard
          payload={card}
          isQa={canTrigger("readiness_gate") || canApprove("readiness_gate")}
          onGate={onGate}
        />
      );
    }
    return (
      <div className="structured-card">
        <h3 className="card-title">{String(card.title || humanLabel(type) || "Update")}</h3>
        <FieldGrid data={card} skipKeys={["card_type", "agent_key", "actions", "required_role"]} />
      </div>
    );
  }

  return (
    <div className="chat-layout">
      <header className="chat-topbar">
        <div className="brand-block">
          <h1>Radius OS</h1>
          <div className="meta">
            <span>{client?.name || "…"}</span>
            <span className="role-chip">{user?.role_label || user?.role_name}</span>
            <Link to="/app" style={{ fontSize: 12 }}>
              All clients
            </Link>
            <button type="button" className="btn btn-ghost" style={{ fontSize: 12, padding: "4px 8px" }} onClick={logout}>
              Log out
            </button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-ghost memory-toggle"
            onClick={() => {
              setMemoryOpen((v) => !v);
              setPanelOpen(false);
            }}
          >
            Team memory
          </button>
          <button
            type="button"
            className="btn btn-ghost panel-toggle"
            onClick={() => {
              setPanelOpen((v) => !v);
              setMemoryOpen(false);
            }}
          >
            Phases
          </button>
          <div className="readiness-badge" aria-live="polite">
            Readiness: {Math.round(readiness.overall)}%
          </div>
        </div>
      </header>

      {error && (
        <div className="error-banner" style={{ margin: "0 16px", flexShrink: 0 }}>
          {error}
        </div>
      )}

      <div className="chat-body">
        <div className="chat-main">
          <div className="chat-stream" role="log" aria-live="polite">
            <div className="chat-stream-inner">
              {messages.map((m) =>
                m.card ? (
                  <div key={m.id} className="msg card-wrap">
                    {renderCard(m.card)}
                  </div>
                ) : (
                  <div key={m.id} className={`msg ${m.role}`}>
                    {m.role === "agent" && m.agent_key && (
                      <div className={`agent-label ${AGENT_CLASS[m.agent_key] || ""}`}>
                        <span className="agent-dot" />
                        {AGENT_NAME[m.agent_key] || m.agent_key}
                      </div>
                    )}
                    <div className={`bubble${m.streaming ? " bubble-streaming" : ""}`}>
                      {m.content}
                      {m.streaming && <span className="stream-caret" aria-hidden />}
                    </div>
                  </div>
                )
              )}
              {thinking && (
                <ThinkingIndicator
                  agentName={activeAgent ? AGENT_NAME[activeAgent] || activeAgent : null}
                  status={thinkingStatus}
                />
              )}
              <div ref={bottomRef} />
            </div>
          </div>
        </div>

        <aside
          className={`shared-memory-panel${memoryOpen ? " open" : ""}`}
          aria-label="Team shared memory"
        >
          <div className="shared-memory-scroll">
            <RolePlayground
              playground={playground}
              loading={playgroundLoading}
              variant="sidebar"
              onClose={() => setMemoryOpen(false)}
            />
          </div>
        </aside>
      </div>

      <PhaseStatusPanel
        open={panelOpen}
        statuses={statuses}
        readiness={readiness}
        isQa={canTrigger("readiness_gate") || canApprove("readiness_gate")}
        onGate={onGate}
      />

      <form className="chat-input-bar" onSubmit={onSubmit}>
        <div className="active-agent-label">
          Talking to:{" "}
          {activeAgent ? AGENT_NAME[activeAgent] || activeAgent : "Auto-routed agent"}
          <span style={{ marginLeft: 12, color: "var(--muted)", fontWeight: 400 }}>
            {nextStepHint()}
          </span>
        </div>
        <div className="prompt-chips" aria-label="Suggested prompts">
          {allowedChips.map((c) => (
            <button
              key={c.label}
              type="button"
              className="prompt-chip"
              onClick={() => send(c.prompt)}
              disabled={thinking || !sessionId}
            >
              {c.label}
            </button>
          ))}
          {!allowedChips.length ? (
            <span className="prompt-chip muted">No triggerable Phase 1–4 skills for your role</span>
          ) : null}
        </div>
        <div className="input-row">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Talk to Radius OS — only your role’s skills will run…"
            disabled={thinking || !sessionId}
            rows={1}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            aria-label="Message"
          />
          <button className="send" type="submit" aria-label="Send" disabled={thinking || !sessionId}>
            ➤
          </button>
        </div>
      </form>
    </div>
  );
}
