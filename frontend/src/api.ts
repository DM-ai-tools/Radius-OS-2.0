const API = import.meta.env.VITE_API_URL || "";

function friendlyApiError(detail: unknown, fallback: string): string {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const obj = detail as { code?: string; message?: string };
    if (obj.code === "dead_man_switch_tripped") {
      return (
        obj.message ||
        "This deployment is locked by the dead man's switch. Contact an operator to check in."
      );
    }
  }
  const text =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail[0]?.msg
        : fallback;
  if (!text || text === "Internal Server Error" || text === "Request failed") {
    return "The API was unavailable (it may have been restarting). Refresh the page and try again.";
  }
  return text;
}

// Vestigial sentinel: nothing in the app ever sets the token to "demo", but an older
// build did. Four call sites checked for it and skipped auth while every other call
// would have sent `Bearer demo`. Centralised here so all paths agree.
const DEMO_TOKEN = "demo";

type RequestOpts = RequestInit & { token?: string | null; timeoutMs?: number };

/** Shared transport: headers, auth, timeout, and error shape. Returns the raw Response
 *  so streaming and blob callers can use the same path as JSON ones. */
async function requestRaw(path: string, opts: RequestOpts = {}): Promise<Response> {
  const { token, timeoutMs, signal: externalSignal, ...fetchOpts } = opts;
  // FormData must set its own multipart boundary — forcing JSON here breaks uploads.
  const isFormData =
    typeof FormData !== "undefined" && fetchOpts.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(fetchOpts.headers as Record<string, string>),
  };
  if (token && token !== DEMO_TOKEN) {
    headers.Authorization = `Bearer ${token}`;
  }
  const controller = new AbortController();
  // timeoutMs 0 = no abort (SSE / long agent runs). Default 20s is time-to-headers only.
  const ms = timeoutMs === undefined ? 20_000 : timeoutMs;
  const timer =
    ms > 0 ? setTimeout(() => controller.abort(), ms) : null;
  // A caller-supplied signal (e.g. aborted from a component's unmount cleanup)
  // cancels the same underlying fetch as the timeout does.
  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onExternalAbort);
  }
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...fetchOpts,
      headers,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      // Deliberate cancel (unmount/navigation), not a timeout — let the
      // caller tell the two apart instead of surfacing a scary error.
      if (externalSignal?.aborted) throw err;
      throw new Error(
        "The API took too long to respond. Check that the backend on port 8000 is healthy, then try again."
      );
    }
    throw new Error(
      "Cannot reach the API server. Make sure the backend is running on port 8000."
    );
  } finally {
    if (timer) clearTimeout(timer);
    if (externalSignal) externalSignal.removeEventListener("abort", onExternalAbort);
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(friendlyApiError(err.detail, "Request failed"));
  }
  return res;
}

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const res = await requestRaw(path, opts);
  // 204 and other empty bodies are valid responses (e.g. DELETE); res.json() throws.
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export type ReportDownloadFormat = "pdf" | "docx";

function triggerBrowserDownload(blob: Blob, filename: string): string {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  return filename;
}

/** Fetch a report file (PDF or Word) and hand it to the browser's downloader.
 *  Returns the saved filename. */
async function downloadReportFile(
  path: string,
  token: string | null,
  fallbackName: string
): Promise<string> {
  // Server-side rendering of a full report bundle exceeds the default budget.
  const res = await requestRaw(path, { token, timeoutMs: 120_000 });
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const filename = disposition.match(/filename="([^"]+)"/i)?.[1] || fallbackName;
  return triggerBrowserDownload(blob, filename);
}

/** Save a self-contained HTML document (e.g. designed site preview) via the browser. */
export function downloadHtmlFile(html: string, filename: string): string {
  const cleaned = filename.replace(/[<>:"/\\|?*\u0000-\u001f]+/g, "-").trim() || "site-preview.html";
  const name = cleaned.toLowerCase().endsWith(".html") ? cleaned : `${cleaned}.html`;
  return triggerBrowserDownload(new Blob([html], { type: "text/html;charset=utf-8" }), name);
}

export type Permission = {
  agent_key: string;
  label: string;
  can_trigger: boolean;
  can_approve: boolean;
  implemented?: boolean;
};

export type User = {
  id: string;
  email: string;
  full_name: string;
  role_name: string;
  role_label?: string;
  is_active: boolean;
  permissions?: Permission[];
};

export type SeoRole = {
  name: string;
  label: string;
  description: string;
};

export type PlaygroundSection = {
  title: string;
  source_role: string;
  source_label: string;
  status: string;
  empty_hint: string;
  data: unknown;
  report_kind?: string;
};

export type Playground = {
  client: Client;
  role_name: string;
  role_label: string;
  permissions: Permission[];
  allowed_skills: { agent_key: string; label: string }[];
  sections: PlaygroundSection[];
};

export type EngineRoomPayload = {
  generated_at: string;
  window_days: number;
  summary: {
    agents_total: number;
    agents_ok: number;
    open_issues: number;
    api_calls: number;
    estimated_cost_usd: number;
    clients: number;
  };
  agents: Array<{
    agent_key: string;
    label: string;
    phase: number | null;
    registered: boolean;
    feature_enabled: boolean;
    status: string;
  }>;
  integrations: Array<{ provider: string; configured: boolean; mock: boolean }>;
  recent_jobs: Array<Record<string, unknown>>;
  validation_issues: Array<Record<string, unknown>>;
  api_errors: Array<Record<string, unknown>>;
  recent_api_calls: Array<Record<string, unknown>>;
  cost_by_provider: Array<{ provider: string; cost_usd: number; calls: number }>;
  cost_by_agent: Array<{ agent_key: string; cost_usd: number; calls: number }>;
  issues: Array<{
    severity: string;
    source: string;
    agent_key?: string;
    message: string;
    at: string | null;
  }>;
  feature_flags: Record<string, boolean>;
};

export type CostTrackerPayload = {
  generated_at: string;
  window_days: number;
  summary: {
    api_calls: number;
    estimated_cost_usd: number;
    clients_with_usage: number;
  };
  cost_by_vendor: Array<{ vendor: string; cost_usd: number; calls: number }>;
  cost_by_model: Array<{
    vendor: string;
    provider: string;
    model: string;
    cost_usd: number;
    calls: number;
    prompt_tokens?: number | null;
    completion_tokens?: number | null;
  }>;
  cost_by_provider: Array<{ provider: string; cost_usd: number; calls: number }>;
  cost_by_agent: Array<{ agent_key: string; cost_usd: number; calls: number }>;
  cost_by_client: Array<{
    client_id: string | null;
    client_name: string;
    cost_usd: number;
    calls: number;
  }>;
  recent_api_calls: Array<Record<string, unknown>>;
};

export type ClientCostPayload = {
  client_id: string;
  window_days: number;
  estimated_cost_usd: number;
  call_count: number;
  cost_by_vendor: Array<{ vendor: string; cost_usd: number; calls: number }>;
  cost_by_model: Array<{
    vendor: string;
    provider: string;
    model: string;
    cost_usd: number;
    calls: number;
    prompt_tokens?: number | null;
    completion_tokens?: number | null;
  }>;
  cost_by_provider: Array<{ provider: string; cost_usd: number; calls: number }>;
  cost_by_agent: Array<{ agent_key: string; cost_usd: number; calls: number }>;
  calls: Array<Record<string, unknown>>;
};

export type Client = {
  id: string;
  name: string;
  primary_url: string;
  industry: string | null;
  status: string;
  created_at: string;
  profile?: Profile;
};

export type Profile = {
  id: string;
  client_id: string;
  discovery_status: string;
  tracking_status: string;
  website_status: string;
  competitor_status: string;
  search_demand_status?: string;
  seo_strategy_status?: string;
  site_architecture_status?: string;
  technical_seo_status?: string;
  content_audit_status?: string;
  content_planning_status?: string;
  content_production_status?: string;
  on_page_seo_status?: string;
  publishing_status?: string;
  overall_readiness_score: number | null;
  ready_for_phase5: boolean;
  commercial_scope?: Record<string, unknown>;
  marketing_context?: Record<string, unknown>;
  tracking_baseline?: Record<string, unknown>;
  website_situation_summary?: Record<string, unknown>;
  competitive_landscape_summary?: Record<string, unknown>;
  search_demand_summary?: Record<string, unknown>;
  seo_strategy_summary?: Record<string, unknown>;
  site_architecture_summary?: Record<string, unknown>;
  technical_seo_summary?: Record<string, unknown>;
  content_audit_summary?: Record<string, unknown>;
  content_planning_summary?: Record<string, unknown>;
  content_production_summary?: Record<string, unknown>;
  on_page_seo_summary?: Record<string, unknown>;
  publishing_summary?: Record<string, unknown>;
  updated_at: string;
};

export type WordPressGate = {
  state: "ready" | "limited" | "failed" | "not_connected";
  label: string;
  ok: boolean;
  detail: string;
};

export type WordPressStatus = {
  connected: boolean;
  base_url?: string;
  username?: string;
  wp_user?: string | null;
  can_publish?: boolean | null;
  error?: string | null;
  gate?: WordPressGate;
};

export type ChatEvent = {
  type: string;
  content?: string;
  agent_key?: string;
  payload?: Record<string, unknown>;
};


/** One competitor's comparison across every benchmark dimension. */
export type BenchmarkDimension = {
  key: string;
  label: string;
  measurable: boolean;
  score: number | null;
  ours: unknown;
  theirs: unknown;
  detail: string;
  reason: string;
  gaps: string[];
  recommendations: string[];
};

export type BenchmarkCompetitor = {
  competitor_id: string;
  name: string;
  domain: string;
  url: string;
  source: string;
  ok: boolean;
  error: string | null;
  verdict: string;
  score: number | null;
  warnings?: string[];
  content: Record<string, unknown>;
  dimensions: BenchmarkDimension[];
};

export type BenchmarkReport = {
  version: string;
  service_id: string;
  service_name: string;
  generated_at: string;
  competitors: BenchmarkCompetitor[];
  overall: {
    score: number | null;
    verdict: string;
    competitors_compared: number;
    competitors_failed: number;
    our_word_count: number;
  } | null;
  gaps: Array<{ gap: string; dimension: string; competitors: string[] }>;
  recommendations: Array<{
    priority: number;
    recommendation: string;
    dimension: string;
    raised_by: string[];
  }>;
  notes: string[];
};

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  signup: (body: {
    email: string;
    password: string;
    full_name: string;
    role_name: string;
  }) =>
    request<{ access_token: string }>("/api/v1/auth/signup", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  roles: () => request<SeoRole[]>("/api/v1/auth/roles"),
  me: (token: string) => request<User>("/api/v1/auth/me", { token }),
  playground: (token: string, client_id: string) =>
    request<Playground>(`/api/v1/clients/${client_id}/playground`, { token }),
  clients: (token: string) => request<Client[]>("/api/v1/clients", { token }),
  getClient: (token: string, id: string) =>
    request<Client>(`/api/v1/clients/${id}`, { token }),
  createClient: (
    token: string,
    body: {
      name: string;
      primary_url: string;
      industry?: string;
      primary_contact_name?: string;
      primary_contact_email?: string;
      primary_contact_phone?: string;
      company_size?: string;
      headquarters_location?: string;
      target_audience?: string;
      geographic_focus?: string;
      known_competitors?: string;
      website_priorities?: string;
      objectives?: string[];
      business_keywords?: string;
      b2b_b2c?: string;
      business_model?: string;
    }
  ) =>
    request<Client>("/api/v1/clients", {
      method: "POST",
      token,
      body: JSON.stringify(body),
    }),
  updateClient: (
    token: string,
    id: string,
    body: {
      name?: string;
      primary_url?: string;
      industry?: string;
      status?: string;
      primary_contact_name?: string;
      primary_contact_email?: string;
      primary_contact_phone?: string;
      company_size?: string;
      headquarters_location?: string;
      target_audience?: string;
      geographic_focus?: string;
      known_competitors?: string;
      website_priorities?: string;
      objectives?: string[];
      business_keywords?: string;
      b2b_b2c?: string;
      business_model?: string;
    }
  ) =>
    request<Client>(`/api/v1/clients/${id}`, {
      method: "PATCH",
      token,
      body: JSON.stringify(body),
    }),
  deleteClient: (token: string | null, id: string) =>
    request<void>(`/api/v1/clients/${id}`, { method: "DELETE", token }),
  contentProductionSitePreview: (
    token: string,
    clientId: string,
    body: {
      title?: string;
      url?: string;
      meta_description?: string;
      keyword?: string;
      markdown: string;
      images?: Array<Record<string, unknown>>;
      media_base?: string;
      /** Inline images as data URIs for offline/downloadable HTML. */
      embed_images?: boolean;
    },
  ) =>
    request<{
      preview_html: string;
      brand_applied: boolean;
      layout_available: boolean;
      design_source?: string;
      design_fallback?: string | null;
      wordpress_kit_id?: string | null;
      images_embedded?: boolean;
    }>(`/api/v1/clients/${clientId}/content-production/site-preview`, {
      method: "POST",
      token,
      body: JSON.stringify(body),
      timeoutMs: 90_000,
    }),
  createSession: (token: string, client_id: string) =>
    request<{ id: string }>("/api/v1/sessions", {
      method: "POST",
      token,
      body: JSON.stringify({ client_id }),
    }),
  listSessions: (token: string, client_id: string) =>
    request<
      {
        id: string;
        client_id: string;
        user_id: string;
        active_agent_key: string | null;
        started_at: string;
        ended_at: string | null;
      }[]
    >(`/api/v1/sessions?client_id=${encodeURIComponent(client_id)}`, { token }),
  messages: (token: string, session_id: string) =>
    request<
      {
        id: string;
        role: string;
        content: string;
        structured_payload: Record<string, unknown> | null;
        agent_key: string | null;
        created_at: string;
      }[]
    >(`/api/v1/sessions/${session_id}/messages?limit=500`, { token }),
  postMessage: (token: string, session_id: string, content: string) =>
    request<{ events: ChatEvent[] }>(`/api/v1/sessions/${session_id}/messages`, {
      method: "POST",
      token,
      body: JSON.stringify({ content }),
    }),
  /** Claude-style SSE: thinking first, then events one-by-one, ends with {type:"done"}. */
  postMessageStream: async (
    token: string | null,
    session_id: string,
    content: string,
    onEvent: (ev: ChatEvent) => void | Promise<void>,
    signal?: AbortSignal
  ) => {
    const res = await requestRaw(`/api/v1/sessions/${session_id}/messages/stream`, {
      method: "POST",
      token,
      headers: { Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
      timeoutMs: 0,
      signal,
    });
    if (!res.body) {
      throw new Error("No stream body");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (!raw || raw === "[DONE]") continue;
        try {
          const ev = JSON.parse(raw) as ChatEvent;
          await onEvent(ev);
        } catch {
          /* skip malformed chunk */
        }
      }
    }
  },
  reviewPhase: (
    token: string,
    client_id: string,
    agent_key: string,
    action: string,
    edits?: Record<string, unknown>,
    note?: string
  ) =>
    request<{
      resolved: number;
      overall_readiness_score: number;
      phase_statuses: Record<string, string>;
      handoff?: {
        from_agent: string;
        to_agent?: string | null;
        to_label?: string | null;
        prompt?: string | null;
        message: string;
      };
    }>(`/api/v1/clients/${client_id}/phases/${agent_key}/review`, {
      method: "POST",
      token,
      body: JSON.stringify({ action, edits, note }),
    }),
  saveServicePrioritization: (
    token: string,
    client_id: string,
    body: Record<string, unknown>,
  ) =>
    request<{ ok: boolean; service_prioritization: Record<string, unknown> }>(
      `/api/v1/clients/${client_id}/service-prioritization`,
      {
        method: "PUT",
        token,
        body: JSON.stringify(body),
      },
    ),
  readiness: (token: string, client_id: string) =>
    request<{
      overall: number;
      threshold: number;
      ready_for_phase5: boolean;
      phases: Record<string, { score: number; missing: string[] }>;
      missing: string[];
      statuses: Record<string, string>;
      can_gate: boolean;
    }>(`/api/v1/clients/${client_id}/readiness`, { token }),
  gate: (token: string, client_id: string, approve: boolean, note?: string) =>
    request(`/api/v1/clients/${client_id}/readiness/gate`, {
      method: "POST",
      token,
      body: JSON.stringify({ approve, note }),
    }),
  mockGrant: (token: string, client_id: string, provider: string) =>
    request(`/api/v1/oauth/mock-grant`, {
      method: "POST",
      token,
      body: JSON.stringify({ client_id, provider, scope: "read" }),
    }),
  oauthAuthorizeUrl: (token: string, client_id: string, provider: string) =>
    request<{
      mode: "google" | "mock";
      url: string | null;
      provider: string;
      scopes?: string[];
      message?: string;
    }>(`/api/v1/oauth/authorize-url`, {
      method: "POST",
      token,
      body: JSON.stringify({ client_id, provider }),
    }),
  oauthConfig: (token: string) =>
    request<{
      configured: boolean;
      providers: string[];
      labels: Record<string, string>;
      redirect_uri?: string;
    }>(`/api/v1/oauth/config`, { token }),
  /** Each client connects their own WordPress site — no shared/global site. */
  wordpressStatus: (token: string, client_id: string) =>
    request<WordPressStatus>(`/api/v1/clients/${client_id}/integrations/wordpress`, { token }),
  technicalSeoIssueUrls: (
    token: string,
    client_id: string,
    rule_id: string,
    offset = 0,
    limit = 50,
  ) =>
    request<{
      client_id: string;
      rule_id: string;
      total: number;
      offset: number;
      limit: number;
      urls: string[];
      issue?: Record<string, unknown>;
    }>(
      `/api/v1/clients/${client_id}/technical-seo/issues/${encodeURIComponent(rule_id)}/urls?offset=${offset}&limit=${limit}`,
      { token },
    ),
  wordpressConnect: (
    token: string,
    client_id: string,
    body: { base_url: string; username: string; app_password: string }
  ) =>
    request<WordPressStatus>(`/api/v1/clients/${client_id}/integrations/wordpress`, {
      method: "POST",
      token,
      body: JSON.stringify(body),
      timeoutMs: 90_000,
    }),
  wordpressDisconnect: (token: string, client_id: string) =>
    request<WordPressStatus>(`/api/v1/clients/${client_id}/integrations/wordpress`, {
      method: "DELETE",
      token,
    }),
  submitQuestionnaire: (
    token: string,
    client_id: string,
    fields: Record<string, unknown>,
    session_id?: string | null
  ) =>
    request<{ ok: boolean; fields: string[]; events: ChatEvent[] }>(
      `/api/v1/clients/${client_id}/questionnaire`,
      {
        method: "POST",
        token,
        body: JSON.stringify({ fields, session_id: session_id || undefined }),
      }
    ),
  submitKnownChanges: (
    token: string,
    client_id: string,
    fields: Record<string, unknown>,
    session_id?: string | null
  ) =>
    request<{ ok: boolean; fields: string[]; events: ChatEvent[] }>(
      `/api/v1/clients/${client_id}/tracking/known-changes`,
      {
        method: "POST",
        token,
        body: JSON.stringify({ fields, session_id: session_id || undefined }),
      }
    ),
  importDiscoveryDocument: (token: string, client_id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{
      ok: boolean;
      filename: string;
      fields: Record<string, unknown>;
      client: Client;
      cdd_version?: { id: string; version_no: number; created_at: string | null };
      diff?: {
        changed_keys?: string[];
        added?: Record<string, unknown>;
        removed?: Record<string, unknown>;
        modified?: Record<string, { old: unknown; new: unknown }>;
      };
      staleness?: Record<
        string,
        { state: string; fields?: string[]; upstream?: string[]; reason?: string }
      >;
      stale_phases?: string[];
      events?: ChatEvent[];
      questionnaire_card?: Record<string, unknown>;
    }>(`/api/v1/clients/${client_id}/discovery/import-document`, {
      method: "POST",
      token,
      body: form,
      // Parsing a spreadsheet server-side outruns the default 20s budget.
      timeoutMs: 120_000,
    });
  },
  addCompetitor: (token: string, client_id: string, name: string, url: string) =>
    request(`/api/v1/clients/${client_id}/competitors/manual`, {
      method: "POST",
      token,
      body: JSON.stringify({ name, url }),
    }),
  /** Competitors attached to one service, plus every competitor available to pick. */
  serviceCompetitors: (token: string, client_id: string, service_id: string) =>
    request<{
      service_id: string;
      selected_competitor_ids: string[];
      selected: Array<Record<string, unknown>>;
      available: Array<Record<string, unknown>>;
    }>(
      `/api/v1/clients/${client_id}/services/${encodeURIComponent(service_id)}/competitors`,
      { token },
    ),
  /** Replace a service's competitor selection. Order is preserved. */
  saveServiceCompetitors: (
    token: string,
    client_id: string,
    service_id: string,
    competitor_ids: string[],
  ) =>
    request<{ ok: boolean; selected_competitor_ids: string[]; warnings: string[] }>(
      `/api/v1/clients/${client_id}/services/${encodeURIComponent(service_id)}/competitors`,
      { method: "PUT", token, body: JSON.stringify({ competitor_ids }) },
    ),
  /** Add a competitor inline and attach it to this service (reuses an existing domain). */
  addServiceCompetitor: (
    token: string,
    client_id: string,
    service_id: string,
    body: { name: string; website: string; notes?: string },
  ) =>
    request<{
      ok: boolean;
      created: boolean;
      competitor: Record<string, unknown>;
      warnings: string[];
    }>(
      `/api/v1/clients/${client_id}/services/${encodeURIComponent(service_id)}/competitors`,
      { method: "POST", token, body: JSON.stringify(body) },
    ),
  /** Detach from this service, or delete the competitor from the client entirely. */
  removeServiceCompetitor: (
    token: string,
    client_id: string,
    service_id: string,
    competitor_id: string,
    deleteEverywhere = false,
  ) =>
    request<{ ok: boolean; deleted: boolean; selected_competitor_ids: string[] }>(
      `/api/v1/clients/${client_id}/services/${encodeURIComponent(service_id)}` +
        `/competitors/${competitor_id}${deleteEverywhere ? "?delete_everywhere=true" : ""}`,
      { method: "DELETE", token },
    ),
  /** Benchmark our content for a service against its selected competitors. */
  benchmarkService: (
    token: string,
    client_id: string,
    service_id: string,
    body: {
      competitor_ids?: string[];
      target_keywords?: string[];
      allow_live_fetch?: boolean;
      pasted?: Record<string, string>;
    } = {},
  ) =>
    request<BenchmarkReport>(
      `/api/v1/clients/${client_id}/services/${encodeURIComponent(service_id)}/benchmark`,
      {
        method: "POST",
        token,
        body: JSON.stringify(body),
        // Live-fetching several competitor pages outruns the default budget.
        timeoutMs: 120_000,
      },
    ),
  /** Download latest structured report per phase as a single PDF or Word document. */
  downloadReports: async (
    token: string | null,
    client_id: string,
    format: ReportDownloadFormat = "pdf"
  ) => {
    const filename = await downloadReportFile(
      `/api/v1/clients/${client_id}/reports/export?format=${format}`,
      token,
      `reports.${format}`
    );
    return { filename, report_count: null as number | null };
  },
  /** Download one phase report as PDF or Word by card_type. */
  downloadReport: async (
    token: string | null,
    client_id: string,
    card_type: string,
    format: ReportDownloadFormat = "pdf"
  ) => {
    const filename = await downloadReportFile(
      `/api/v1/clients/${client_id}/reports/${encodeURIComponent(card_type)}/export?format=${format}`,
      token,
      `report.${format}`
    );
    return { filename };
  },

  engineRoom: (token: string | null, days = 7) =>
    request<EngineRoomPayload>(`/api/v1/engine-room?days=${days}`, { token, timeoutMs: 30_000 }),

  costTracker: (token: string | null, days = 7) =>
    request<CostTrackerPayload>(`/api/v1/cost-tracker?days=${days}`, { token, timeoutMs: 30_000 }),

  clientCosts: (token: string | null, clientId: string, days = 30) =>
    request<ClientCostPayload>(`/api/v1/cost-tracker/clients/${clientId}?days=${days}`, {
      token,
      timeoutMs: 30_000,
    }),

  downloadWorkbook: (token: string | null, clientId: string) =>
    downloadReportFile(
      `/api/v1/clients/${clientId}/workbook/export`,
      token,
      "Category_Mapping_Search_Demand.xlsx"
    ),
};
