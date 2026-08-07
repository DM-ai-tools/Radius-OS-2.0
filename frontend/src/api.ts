const API = import.meta.env.VITE_API_URL || "";

async function request<T>(
  path: string,
  opts: RequestInit & { token?: string | null } = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  // Always send JWT when present (auth is enabled)
  if (opts.token) {
    headers.Authorization = `Bearer ${opts.token}`;
  }
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { ...opts, headers });
  } catch {
    throw new Error(
      "Cannot reach the API server. Make sure the backend is running on port 8000."
    );
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail[0]?.msg
          : "Request failed"
    );
  }
  return res.json();
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
};

export type Playground = {
  client: Client;
  role_name: string;
  role_label: string;
  permissions: Permission[];
  allowed_skills: { agent_key: string; label: string }[];
  sections: PlaygroundSection[];
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
  overall_readiness_score: number | null;
  ready_for_phase5: boolean;
  commercial_scope?: Record<string, unknown>;
  marketing_context?: Record<string, unknown>;
  tracking_baseline?: Record<string, unknown>;
  website_situation_summary?: Record<string, unknown>;
  competitive_landscape_summary?: Record<string, unknown>;
  updated_at: string;
};

export type ChatEvent = {
  type: string;
  content?: string;
  agent_key?: string;
  payload?: Record<string, unknown>;
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
  deleteClient: async (token: string | null, id: string) => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (token && token !== "demo") {
      headers.Authorization = `Bearer ${token}`;
    }
    const res = await fetch(`${API}/api/v1/clients/${id}`, {
      method: "DELETE",
      headers,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail[0]?.msg
            : "Delete failed"
      );
    }
  },
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
    onEvent: (ev: ChatEvent) => void | Promise<void>
  ) => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    };
    if (token && token !== "demo") {
      headers.Authorization = `Bearer ${token}`;
    }
    const res = await fetch(`${API}/api/v1/sessions/${session_id}/messages/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ content }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail[0]?.msg
            : "Stream failed"
      );
    }
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
    }>(`/api/v1/clients/${client_id}/phases/${agent_key}/review`, {
      method: "POST",
      token,
      body: JSON.stringify({ action, edits, note }),
    }),
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
    request<{ configured: boolean; providers: string[]; labels: Record<string, string> }>(
      `/api/v1/oauth/config`,
      { token }
    ),
  submitQuestionnaire: (token: string, client_id: string, fields: Record<string, unknown>) =>
    request<{ ok: boolean; fields: string[]; events: ChatEvent[] }>(
      `/api/v1/clients/${client_id}/questionnaire`,
      {
        method: "POST",
        token,
        body: JSON.stringify({ fields }),
      }
    ),
  submitKnownChanges: (token: string, client_id: string, fields: Record<string, unknown>) =>
    request<{ ok: boolean; fields: string[]; events: ChatEvent[] }>(
      `/api/v1/clients/${client_id}/tracking/known-changes`,
      {
        method: "POST",
        token,
        body: JSON.stringify({ fields }),
      }
    ),
  importDiscoveryDocument: async (token: string, client_id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const API = import.meta.env.VITE_API_URL || "";
    const res = await fetch(`${API}/api/v1/clients/${client_id}/discovery/import-document`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail[0]?.msg
            : "Upload failed"
      );
    }
    return res.json() as Promise<{
      ok: boolean;
      filename: string;
      fields: Record<string, unknown>;
      client: Client;
    }>;
  },
  addCompetitor: (token: string, client_id: string, name: string, url: string) =>
    request(`/api/v1/clients/${client_id}/competitors/manual`, {
      method: "POST",
      token,
      body: JSON.stringify({ name, url }),
    }),
};
