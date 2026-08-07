import { FormEvent, type ReactNode, useMemo, useState } from "react";
import { FieldGrid, PresentableValue, humanLabel } from "./PresentableValue";
import { statusTone } from "../../lib/statusTone";

type Row = {
  id: string;
  element: string;
  check_result: string;
  detail: { message?: string; fix?: string | null; anomaly_flags?: string[] };
};

type Platform = {
  key: string;
  label: string;
  status: string;
  detail: string;
  mode?: string;
};

type AuditItem = {
  check: string;
  element: string;
  result: string;
  message?: string;
  fix?: string | null;
};

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  canGrant: boolean;
  onAction: (action: string) => void;
  onGrant: (provider: string) => void;
  onSubmitKnownChanges?: (fields: Record<string, unknown>) => void;
};

function ModeBadge({ mode }: { mode?: string }) {
  const m = String(mode || "").toUpperCase();
  const cls =
    m.includes("GATE")
      ? "mode-badge gate"
      : m.includes("HUMAN")
        ? "mode-badge human"
        : "mode-badge auto";
  return m ? <span className={cls}>{m}</span> : null;
}

function CardShell({
  payload,
  children,
  checkpoint,
}: {
  payload: Record<string, unknown>;
  children: ReactNode;
  checkpoint?: boolean;
}) {
  return (
    <div className={`structured-card tracking ${checkpoint ? "checkpoint" : ""}`}>
      <div className="tracking-card-head">
        <p className="page-kicker" style={{ marginBottom: 6 }}>
          {String(payload.step || "")}
          {payload.subtitle ? ` · ${String(payload.subtitle)}` : ""}
        </p>
        <ModeBadge mode={String(payload.mode || "")} />
      </div>
      <h3 className="card-title">{String(payload.title || "Tracking")}</h3>
      {children}
    </div>
  );
}

export default function TrackingCard({
  payload,
  canAct,
  canGrant,
  onAction,
  onGrant,
  onSubmitKnownChanges,
}: Props) {
  const cardType = String(payload.card_type || "");

  if (cardType === "oauth_request") {
    const providers = (payload.providers || []) as string[];
    const scopes = (payload.scopes || {}) as Record<string, string>;
    const providerStatus = (payload.provider_status || {}) as Record<string, string>;
    return (
      <CardShell payload={payload}>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
          {String(payload.message || "")}
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {providers.map((p) => (
            <div key={p} className="field-row">
              <div className="key">
                {p.replaceAll("_", " ")}{" "}
                <span className="status-pill unverified">
                  {providerStatus[p] || "Not granted"}
                </span>
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                {scopes[p] || "read access"}
              </div>
              <button
                className="btn btn-secondary"
                disabled={!canGrant}
                onClick={() => onGrant(p)}
              >
                Connect {p.replaceAll("_", " ")}
              </button>
            </div>
          ))}
        </div>
      </CardShell>
    );
  }

  if (cardType === "tracking_t1_access") {
    const platforms = (payload.platforms || []) as Platform[];
    const oauthKeys = new Set(["ga4", "search_console", "gtm", "google_business"]);
    return (
      <CardShell payload={payload}>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
          Requests credentials and runs a connection test per platform — access must be
          active, not just requested.
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Status</th>
                <th>Detail</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {platforms.map((p) => (
                <tr key={p.key}>
                  <td>{p.label}</td>
                  <td>
                    <span className={`status-pill ${statusTone(p.status)}`}>
                      <span aria-hidden>●</span>
                      {p.status.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td style={{ fontSize: 12, color: "var(--muted)" }}>{p.detail}</td>
                  <td>
                    {oauthKeys.has(p.key) && p.status !== "connected" ? (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={!canGrant}
                        onClick={() => onGrant(p.key)}
                      >
                        Connect
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardShell>
    );
  }

  if (cardType === "tracking_t2_audit") {
    const items = (payload.items || []) as AuditItem[];
    return (
      <CardShell payload={payload}>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
          Analytics-layer scan: tag presence, duplicates, cross-domain, referral exclusions,
          cookie consent.
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Check</th>
                <th>Result</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.element + it.check}>
                  <td>{it.check}</td>
                  <td>
                    <span className={`status-pill ${it.result}`}>
                      <span aria-hidden>●</span>
                      {it.result}
                    </span>
                  </td>
                  <td style={{ fontSize: 12 }}>
                    <div>{it.message}</div>
                    {it.fix ? (
                      <div style={{ color: "var(--amber)", marginTop: 4 }}>Fix: {it.fix}</div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardShell>
    );
  }

  if (cardType === "tracking_t3_conversions") {
    const events = (payload.events || []) as { name: string; status: string }[];
    return (
      <CardShell payload={payload}>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
          {String(payload.summary || "")}
        </p>
        <p style={{ fontSize: 12, color: "var(--amber)" }}>{String(payload.rule || "")}</p>
        <ul className="tracking-conv-list">
          {events.map((ev) => (
            <li key={ev.name}>
              <span>{ev.name}</span>
              <span className={`status-pill ${ev.status}`}>
                <span aria-hidden>●</span>
                {ev.status}
              </span>
            </li>
          ))}
        </ul>
        {payload.fix ? (
          <p style={{ fontSize: 12, color: "var(--amber)" }}>Fix: {String(payload.fix)}</p>
        ) : null}
      </CardShell>
    );
  }

  if (cardType === "tracking_t4_baseline") {
    const metrics = (payload.metrics || {}) as Record<string, unknown>;
    const access = (payload.access || {}) as Record<string, boolean>;
    return (
      <CardShell payload={payload}>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
          {String(payload.note || "6–12 months of GSC + GA4 historical performance.")}
        </p>
        <div className="metric-grid" style={{ marginBottom: 12 }}>
          <div className="metric-tile tone-neutral">
            <div className="metric-label">Window</div>
            <div className="metric-value metric-value-sm">
              {String(payload.window_months || "6–12")} mo
            </div>
          </div>
          <div className="metric-tile tone-neutral">
            <div className="metric-label">GA4</div>
            <div className="metric-value metric-value-sm">
              {access.ga4 ? "Connected" : "Missing"}
            </div>
          </div>
          <div className="metric-tile tone-neutral">
            <div className="metric-label">GSC</div>
            <div className="metric-value metric-value-sm">
              {access.search_console ? "Connected" : "Missing"}
            </div>
          </div>
          <div className="metric-tile tone-neutral">
            <div className="metric-label">Status</div>
            <div className="metric-value metric-value-sm">
              {String(payload.status || "").replaceAll("_", " ")}
            </div>
          </div>
        </div>
        <div className="report-section" style={{ borderTop: "none", paddingTop: 0 }}>
          <h4>Baseline metrics</h4>
          <FieldGrid data={metrics} />
        </div>
      </CardShell>
    );
  }

  if (cardType === "tracking_t5_known_changes") {
    return (
      <KnownChangesForm
        payload={payload}
        canAct={canAct}
        onSubmit={(fields) => onSubmitKnownChanges?.(fields)}
      />
    );
  }

  if (cardType === "tracking_t6_signoff" || cardType === "tracking_health") {
    return (
      <SignOffCard payload={payload} canAct={canAct} onAction={onAction} />
    );
  }

  return (
    <CardShell payload={payload}>
      <PresentableValue value={payload} />
    </CardShell>
  );
}

function KnownChangesForm({
  payload,
  canAct,
  onSubmit,
}: {
  payload: Record<string, unknown>;
  canAct: boolean;
  onSubmit: (fields: Record<string, unknown>) => void;
}) {
  type FieldDef = { key: string; label: string; help?: string };
  const fields = (payload.fields || []) as FieldDef[];
  const initial = useMemo(() => {
    const out: Record<string, string> = {};
    for (const f of fields) out[f.key] = "";
    return out;
  }, [fields]);
  const [values, setValues] = useState(initial);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(values)) {
      out[k] = v.trim();
    }
    onSubmit(out);
  }

  return (
    <div className="structured-card checkpoint tracking questionnaire-card">
      <div className="tracking-card-head">
        <p className="page-kicker" style={{ marginBottom: 6 }}>
          {String(payload.step || "T5")} · {String(payload.subtitle || "CSM + client")}
        </p>
        <ModeBadge mode={String(payload.mode || "HUMAN INPUT")} />
      </div>
      <h3 className="card-title">{String(payload.title || "T5 — Known-changes log")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Institutional memory only — redesigns, domain moves, past SEO, manual actions,
        security incidents, algorithm timing. APIs cannot see this.
      </p>
      <form onSubmit={handleSubmit}>
        {fields.map((f) => (
          <div key={f.key} className="field q-field q-client-only">
            <label>
              {f.label || humanLabel(f.key)}
              <span className="q-badge">Needs client</span>
            </label>
            {f.help ? <p className="q-help">{f.help}</p> : null}
            <textarea
              rows={2}
              value={values[f.key] ?? ""}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, [f.key]: e.target.value }))
              }
              placeholder="Leave blank if none / unknown"
            />
          </div>
        ))}
        {canAct && (
          <div className="card-actions">
            <button className="btn btn-primary" type="submit">
              Submit known-changes → T6
            </button>
          </div>
        )}
      </form>
    </div>
  );
}

function SignOffCard({
  payload,
  canAct,
  onAction,
}: {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const rows = (payload.rows || []) as Row[];
  const blockers = (payload.blockers || []) as string[];
  const missing = (payload.missing || []) as string[];
  const known = (payload.known_changes || {}) as Record<string, unknown>;

  return (
    <div className="structured-card checkpoint tracking">
      <div className="tracking-card-head">
        <p className="page-kicker" style={{ marginBottom: 6 }}>
          {String(payload.step || "T6")} · {String(payload.subtitle || "Technical SEO Specialist")}
        </p>
        <ModeBadge mode={String(payload.mode || "HUMAN GATE")} />
      </div>
      <h3 className="card-title">{String(payload.title || "T6 — Readiness scoring & sign-off")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Sign-off requires{" "}
        <strong>{String(payload.required_role || "technical_seo_specialist")}</strong>
        {payload.scoped ? " · Scoped re-check" : ""}
      </p>

      <div className="metric-grid" style={{ marginBottom: 12 }}>
        <div className="metric-tile tone-neutral">
          <div className="metric-label">Tracking score</div>
          <div className="metric-value">{Number(payload.tracking_score || 0).toFixed(0)}%</div>
        </div>
        <div className="metric-tile tone-neutral">
          <div className="metric-label">Automation level</div>
          <div className="metric-value">{String(payload.automation_level ?? "—")}</div>
        </div>
      </div>
      <p style={{ fontSize: 13, marginTop: 0 }}>{String(payload.automation_note || "")}</p>

      {blockers.length > 0 && (
        <div className="report-section">
          <h4>Tracking blockers</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
      )}
      {missing.length > 0 && (
        <div className="report-section">
          <h4>Missing</h4>
          <PresentableValue value={missing} />
        </div>
      )}
      {Object.keys(known).length > 0 && (
        <div className="report-section">
          <h4>Known changes (T5)</h4>
          <FieldGrid data={known} />
        </div>
      )}

      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Element</th>
              <th>Status</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id || r.element}>
                <td>{r.element.replaceAll("_", " ")}</td>
                <td>
                  <span className={`status-pill ${r.check_result}`}>
                    <span aria-hidden>●</span>
                    {r.check_result}
                  </span>
                </td>
                <td>
                  <button
                    className="btn btn-ghost"
                    style={{ padding: "4px 8px", fontSize: 12 }}
                    onClick={() =>
                      setExpanded(expanded === r.element ? null : r.element)
                    }
                  >
                    {expanded === r.element ? "Hide" : "Expand"}
                  </button>
                  {expanded === r.element && (
                    <div style={{ marginTop: 6, fontSize: 12 }}>
                      <div>{r.detail?.message}</div>
                      {r.detail?.fix && (
                        <div style={{ marginTop: 4, color: "var(--amber)" }}>
                          Fix: {r.detail.fix}
                        </div>
                      )}
                      {r.detail?.anomaly_flags?.map((f) => (
                        <div key={f} style={{ color: "var(--coral)", marginTop: 2 }}>
                          {f}
                        </div>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canAct && (
        <div className="card-actions">
          <button className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve baseline
          </button>
          <button className="btn btn-amber" onClick={() => onAction("flag_for_client")}>
            Flag for client remediation
          </button>
        </div>
      )}
    </div>
  );
}
