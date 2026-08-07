import { FormEvent, useMemo, useState } from "react";
import { FieldGrid, PresentableValue, humanLabel } from "./PresentableValue";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string, edits?: Record<string, unknown>) => void;
  onSubmitQuestionnaire?: (fields: Record<string, unknown>) => void;
  onUploadCdd?: (file: File) => Promise<Record<string, unknown>>;
};

function fieldInputValue(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string" || typeof v === "number") return String(v);
  return JSON.stringify(v);
}

function CommercialScopeView({ scope }: { scope: Record<string, unknown> }) {
  const products = scope.products;
  const revenue = scope.revenue_split;

  return (
    <div className="commercial-scope">
      {Boolean(scope.business_model || scope.positioning) && (
        <div className="scope-hero">
          {scope.business_model ? (
            <p className="scope-lede">{String(scope.business_model)}</p>
          ) : null}
          {scope.positioning ? (
            <p className="scope-positioning">{String(scope.positioning)}</p>
          ) : null}
        </div>
      )}

      <div className="metric-grid">
        {scope.average_order_value != null && (
          <div className="metric-tile tone-neutral">
            <div className="metric-label">Avg order value</div>
            <div className="metric-value">{String(scope.average_order_value)}</div>
          </div>
        )}
        {scope.sales_cycle != null && (
          <div className="metric-tile tone-neutral">
            <div className="metric-label">Sales cycle</div>
            <div className="metric-value metric-value-sm">{String(scope.sales_cycle)}</div>
          </div>
        )}
        {scope.seasonality != null && (
          <div className="metric-tile tone-neutral">
            <div className="metric-label">Seasonality</div>
            <div className="metric-value metric-value-sm">{String(scope.seasonality)}</div>
          </div>
        )}
      </div>

      {Array.isArray(products) && products.length > 0 && (
        <div className="report-section" style={{ borderTop: "none", paddingTop: 4, marginTop: 8 }}>
          <h4>Products & services</h4>
          <PresentableValue value={products} />
        </div>
      )}

      {revenue != null && (
        <div className="report-section">
          <h4>Revenue split</h4>
          <PresentableValue value={revenue} />
        </div>
      )}

      {(() => {
        const skip = new Set([
          "products",
          "revenue_split",
          "business_model",
          "positioning",
          "sales_cycle",
          "seasonality",
          "average_order_value",
        ]);
        const rest = Object.fromEntries(
          Object.entries(scope).filter(([k]) => !skip.has(k))
        );
        if (!Object.keys(rest).length) return null;
        return (
          <div className="report-section">
            <h4>Other commercial fields</h4>
            <FieldGrid data={rest} />
          </div>
        );
      })()}
    </div>
  );
}

export default function DiscoveryCard({
  payload,
  canAct,
  onAction,
  onSubmitQuestionnaire,
  onUploadCdd,
}: Props) {
  const cardType = String(payload.card_type || "discovery_profile");

  if (cardType === "discovery_rerun_confirm") {
    const scope = (payload.prior_commercial_scope || {}) as Record<string, unknown>;
    return (
      <div className="structured-card checkpoint">
        <h3 className="card-title">{String(payload.title || "Re-run discovery?")}</h3>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
          Prior approved commercial scope is retained for comparison until you confirm a re-run.
        </p>
        <CommercialScopeView scope={scope} />
        {payload.hint ? (
          <p className="hint-line">{String(payload.hint)}</p>
        ) : null}
      </div>
    );
  }

  if (cardType === "discovery_preresearch") {
    const fields = (payload.fields || {}) as Record<
      string,
      { value?: unknown; confidence?: string }
    >;
    return (
      <div className="structured-card">
        <p className="page-kicker" style={{ marginBottom: 6 }}>
          {String(payload.step || "D1")} · {String(payload.subtitle || "Claude + web research")}
        </p>
        <h3 className="card-title">{String(payload.title || "D1 — Automated pre-research")}</h3>
        <p style={{ fontSize: 13, color: "var(--amber)", marginTop: 0 }}>
          {String(payload.label || "Draft — awaiting confirmation")}
        </p>
        <div className="present-fields">
          {Object.entries(fields).map(([k, meta]) => (
            <div key={k} className="present-field">
              <div className="present-key">
                {humanLabel(k)}{" "}
                <span className={`status-pill ${meta.confidence || "medium"}`}>
                  {meta.confidence || "—"}
                </span>
              </div>
              <div className="present-val">
                <PresentableValue value={meta.value} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (cardType === "discovery_completeness") {
    const score = Math.round(Number(payload.completeness_score || 0));
    return (
      <div className="structured-card">
        <p className="page-kicker" style={{ marginBottom: 6 }}>
          {String(payload.step || "D3")} ·{" "}
          {String(payload.subtitle || "Weighted readiness score")}
        </p>
        <h3 className="card-title">{String(payload.title || "D3 — Completeness scoring")}</h3>
        <div className="completeness-bar-wrap">
          <div className="completeness-score">{score}%</div>
          <div className="completeness-track">
            <i style={{ width: `${Math.min(100, score)}%` }} />
          </div>
        </div>
        {(payload.missing_fields as string[] | undefined)?.length ? (
          <div className="report-section">
            <h4>Missing / low confidence</h4>
            <PresentableValue
              value={(payload.missing_fields as string[]).map((f) => humanLabel(f))}
            />
          </div>
        ) : (
          <p style={{ fontSize: 13, color: "var(--muted)" }}>No missing fields flagged.</p>
        )}
      </div>
    );
  }

  if (cardType === "discovery_questionnaire") {
    return (
      <QuestionnaireForm
        payload={payload}
        canAct={canAct}
        onSubmit={(fields) => onSubmitQuestionnaire?.(fields)}
        onUploadCdd={onUploadCdd}
      />
    );
  }

  // D4 sign-off
  const fromResearch = (payload.from_research || {}) as Record<string, unknown>;
  const confirmed = (payload.confirmed_by_client || {}) as Record<string, unknown>;
  const discrepancies = (payload.discrepancies || []) as {
    field_key: string;
    explanation: string;
  }[];
  const discMap = Object.fromEntries(discrepancies.map((d) => [d.field_key, d.explanation]));

  return (
    <div className="structured-card checkpoint">
      <p className="page-kicker" style={{ marginBottom: 6 }}>
        {String(payload.step || "D4")} · {String(payload.subtitle || "Client Success Manager")}
      </p>
      <h3 className="card-title">{String(payload.title || "D4 — Human sign-off")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Completeness: {Math.round(Number(payload.completeness_score || 0))}% · Sign-off requires{" "}
        <strong>{String(payload.required_role || "client_success_manager")}</strong>
      </p>
      <div className="two-col">
        <div className="col-box">
          <h4>From research</h4>
          {Object.keys(fromResearch).length === 0 ? (
            <p className="pv-muted">No research fields yet.</p>
          ) : (
            Object.entries(fromResearch).map(([k, v]) => (
              <div key={k} className={`present-field ${discMap[k] ? "has-discrepancy" : ""}`}>
                <div className="present-key">{humanLabel(k)}</div>
                <div className="present-val">
                  <PresentableValue value={v} />
                </div>
                {discMap[k] && <div className="disc-note">{discMap[k]}</div>}
              </div>
            ))
          )}
        </div>
        <div className="col-box">
          <h4>Confirmed by client</h4>
          {Object.keys(confirmed).length === 0 ? (
            <div className="present-field">
              <span className="pv-muted">Awaiting questionnaire confirmation.</span>
            </div>
          ) : (
            Object.entries(confirmed).map(([k, v]) => (
              <div key={k} className={`present-field ${discMap[k] ? "has-discrepancy" : ""}`}>
                <div className="present-key">{humanLabel(k)}</div>
                <div className="present-val">
                  <PresentableValue value={v} />
                </div>
              </div>
            ))
          )}
          {(payload.missing_fields as string[] | undefined)?.length ? (
            <div className="present-field" style={{ marginTop: 8 }}>
              <div className="present-key">Missing / low confidence</div>
              <PresentableValue
                value={(payload.missing_fields as string[]).map((f) => humanLabel(f))}
              />
            </div>
          ) : null}
        </div>
      </div>
      {canAct && (
        <div className="card-actions">
          <button className="btn btn-primary" onClick={() => onAction("approve")}>
            Approve
          </button>
          <button className="btn btn-amber" onClick={() => onAction("edit", fromResearch)}>
            Edit fields
          </button>
          <button className="btn btn-danger" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

function QuestionnaireForm({
  payload,
  canAct,
  onSubmit,
  onUploadCdd,
}: {
  payload: Record<string, unknown>;
  canAct: boolean;
  onSubmit: (fields: Record<string, unknown>) => void;
  onUploadCdd?: (file: File) => Promise<Record<string, unknown>>;
}) {
  type FieldMeta = {
    value?: unknown;
    prefilled?: boolean;
    client_only?: boolean;
    confidence?: string;
    label?: string;
    help?: string;
    input?: string;
    rows?: number;
    options?: string[];
    section?: string;
    from_intake?: boolean;
    from_cdd_upload?: boolean;
  };

  const fieldsMeta = (payload.fields || {}) as Record<string, FieldMeta>;
  const catalog = (payload.field_catalog || {}) as {
    sections?: { id: string; title: string; blurb?: string }[];
    objective_options?: string[];
  };
  const sections = catalog.sections || [
    { id: "all", title: "Client details", blurb: "Confirm or complete each field." },
  ];
  const objectiveOptions = (payload.objective_options ||
    catalog.objective_options ||
    []) as string[];

  const initial = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [k, meta] of Object.entries(fieldsMeta)) {
      if (k === "objectives" && Array.isArray(meta.value)) {
        out[k] = (meta.value as string[]).join(", ");
      } else {
        out[k] = fieldInputValue(meta.value);
      }
    }
    return out;
  }, [fieldsMeta]);

  const [values, setValues] = useState(initial);
  const [uploading, setUploading] = useState(false);
  const [uploadNote, setUploadNote] = useState("");
  const [selectedObjectives, setSelectedObjectives] = useState<string[]>(() => {
    const v = fieldsMeta.objectives?.value;
    return Array.isArray(v) ? (v as string[]) : [];
  });

  async function handleUpload(file: File | null) {
    if (!file || !onUploadCdd) return;
    setUploading(true);
    setUploadNote("");
    try {
      const fields = await onUploadCdd(file);
      setValues((prev) => {
        const next = { ...prev };
        for (const [k, v] of Object.entries(fields)) {
          if (k === "objectives" && Array.isArray(v)) {
            setSelectedObjectives(v as string[]);
            next[k] = (v as string[]).join(", ");
          } else if (v != null && v !== "") {
            next[k] = typeof v === "string" ? v : JSON.stringify(v);
          }
        }
        return next;
      });
      setUploadNote(`Imported ${Object.keys(fields).length} fields from ${file.name}`);
    } catch (e) {
      setUploadNote(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const fields: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(values)) {
      if (k === "objectives") {
        fields[k] = selectedObjectives.length
          ? selectedObjectives
          : v
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean);
        continue;
      }
      try {
        fields[k] = v.trim().startsWith("{") || v.trim().startsWith("[") ? JSON.parse(v) : v;
      } catch {
        fields[k] = v;
      }
    }
    onSubmit(fields);
  }

  const keysBySection = (sectionId: string) =>
    Object.keys(fieldsMeta).filter((k) => {
      const sec = fieldsMeta[k]?.section || FIELD_META_FALLBACK[k]?.section || "account";
      if (sectionId === "all") return true;
      return sec === sectionId;
    });

  return (
    <div className="structured-card checkpoint questionnaire-card">
      <p className="page-kicker" style={{ marginBottom: 6 }}>
        {String(payload.step || "D2")} ·{" "}
        {String(payload.subtitle || "APSA Client Discovery Document")}
      </p>
      <h3 className="card-title">{String(payload.title || "D2 — CDD questionnaire")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Fields match the APSA Discovery Document — including system access credentials
        (Analytics, Ads, GTM, CMS, hosting). Confirm research, complete client-only
        metrics, or upload an existing CDD (Excel, Word, or PDF).
      </p>
      {canAct && onUploadCdd ? (
        <div className="cdd-upload-bar">
          <label className="btn btn-secondary" style={{ cursor: "pointer" }}>
            {uploading ? "Importing…" : "Upload CDD file"}
            <input
              type="file"
              accept=".xlsx,.xlsm,.csv,.docx,.pdf,.txt,.md"
              hidden
              disabled={uploading}
              onChange={(e) => handleUpload(e.target.files?.[0] || null)}
            />
          </label>
          {uploadNote ? <span className="field-hint">{uploadNote}</span> : null}
        </div>
      ) : null}
      <form onSubmit={handleSubmit}>
        {sections.map((section) => {
          const keys = keysBySection(section.id);
          if (!keys.length) return null;
          return (
            <div key={section.id} className="q-section">
              <div className="q-section-head">
                <h4>{section.title}</h4>
                {section.blurb ? <p>{section.blurb}</p> : null}
              </div>
              {keys.map((k) => {
                const meta = fieldsMeta[k] || {};
                const label = meta.label || humanLabel(k);
                const help = meta.help;
                const input = meta.input || (k === "objectives" ? "objectives" : "textarea");
                const options = meta.options || [];
                return (
                  <div
                    key={k}
                    className={`field q-field ${meta.client_only ? "q-client-only" : ""}`}
                  >
                    <label>
                      {label}
                      {meta.client_only ? (
                        <span className="q-badge">Needs client</span>
                      ) : null}
                      {meta.prefilled ? <span className="q-badge muted">Pre-filled</span> : null}
                      {meta.from_intake ? <span className="q-badge muted">From intake</span> : null}
                    </label>
                    {help ? <p className="q-help">{help}</p> : null}
                    {input === "objectives" && objectiveOptions.length > 0 ? (
                      <div className="q-check-grid">
                        {objectiveOptions.map((opt) => (
                          <label key={opt} className="q-check">
                            <input
                              type="checkbox"
                              checked={selectedObjectives.includes(opt)}
                              onChange={(e) => {
                                setSelectedObjectives((prev) =>
                                  e.target.checked
                                    ? [...prev, opt]
                                    : prev.filter((x) => x !== opt)
                                );
                              }}
                            />
                            {opt}
                          </label>
                        ))}
                      </div>
                    ) : input === "select" ? (
                      <select
                        value={values[k] ?? ""}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [k]: e.target.value }))
                        }
                      >
                        <option value="">Select…</option>
                        {options.map((opt) => (
                          <option key={opt} value={opt}>
                            {opt}
                          </option>
                        ))}
                      </select>
                    ) : input === "text" || input === "email" ? (
                      <input
                        type={input === "email" ? "email" : "text"}
                        value={values[k] ?? ""}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [k]: e.target.value }))
                        }
                      />
                    ) : (
                      <textarea
                        rows={meta.rows || 2}
                        value={values[k] ?? ""}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [k]: e.target.value }))
                        }
                      />
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
        {canAct && (
          <div className="card-actions">
            <button className="btn btn-primary" type="submit">
              Submit questionnaire
            </button>
          </div>
        )}
      </form>
    </div>
  );
}

const FIELD_META_FALLBACK: Record<string, { section?: string }> = {
  inferred_industry: { section: "account" },
  business_model: { section: "account" },
  business_keywords: { section: "account" },
  products: { section: "account" },
  products_for_promotion: { section: "account" },
  positioning: { section: "account" },
  geographic_focus: { section: "audience" },
  target_demographic: { section: "audience" },
  b2b_b2c: { section: "audience" },
  industry_targeting: { section: "audience" },
  average_ticket_size: { section: "commercial" },
  lifetime_value: { section: "commercial" },
  lead_modes: { section: "commercial" },
  revenue_split: { section: "commercial" },
  sales_cycle: { section: "commercial" },
  seasonality: { section: "commercial" },
  competitors: { section: "competitors" },
  strengths: { section: "competitors" },
  weaknesses: { section: "competitors" },
  opportunities: { section: "competitors" },
  threats: { section: "competitors" },
  strategy_approach: { section: "competitors" },
  analytics_access: { section: "access" },
  search_console_access: { section: "access" },
  gtm_access: { section: "access" },
  google_ads_access: { section: "access" },
  merchant_center_access: { section: "access" },
  cms_access: { section: "access" },
  hosting_access: { section: "access" },
  google_business_access: { section: "access" },
  access_level: { section: "access" },
  public_reviews_summary: { section: "brand" },
  social_presence: { section: "brand" },
  google_business_signals: { section: "brand" },
  brand_guidelines: { section: "brand" },
  content_creation_notes: { section: "brand" },
  blogs_notes: { section: "brand" },
  business_goal: { section: "goals" },
  sales_promises: { section: "goals" },
  objectives: { section: "goals" },
  other_marketing_spend: { section: "goals" },
};
