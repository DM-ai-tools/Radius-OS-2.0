type Props = {
  open: boolean;
  statuses: {
    discovery: string;
    tracking: string;
    website: string;
    competitor: string;
    search_demand: string;
    seo_strategy: string;
    site_architecture: string;
    technical_seo: string;
    content_audit: string;
    content_planning: string;
    content_production: string;
    on_page_seo: string;
    publishing: string;
  };
  readiness: {
    overall: number;
    missing: string[];
    can_gate: boolean;
    ready_for_phase5: boolean;
    threshold: number;
  };
};

function statusClass(status: string) {
  if (status === "complete") return "is-complete";
  if (status === "pending_signoff") return "is-pending";
  if (status === "in_progress") return "is-progress";
  return "";
}

function statusLabel(status: string) {
  if (status === "complete") return "Done";
  if (status === "pending_signoff") return "Sign-off";
  if (status === "in_progress") return "Active";
  return "Waiting";
}

type PhaseRow = { key: keyof Props["statuses"]; label: string };

/** One pipeline, numbered in execution order (01–12). */
const GROUPS: { title: string; note?: string; phases: PhaseRow[] }[] = [
  {
    title: "Sequential",
    note: "v1.9 phases 1–12. Inside Phase 6, URL mapping (06a) runs before titles and the calendar (06b).",
    phases: [
      { key: "discovery", label: "01 · Discovery" },
      { key: "tracking", label: "02 · Tracking" },
      { key: "website", label: "03 · Website audit + sitemap" },
      { key: "competitor", label: "04 · Competitors" },
      { key: "search_demand", label: "05 · Keywords" },
      { key: "site_architecture", label: "06a · URL mapping" },
      { key: "seo_strategy", label: "06b · Titles & calendar" },
      { key: "technical_seo", label: "07 · Technical SEO" },
      { key: "content_audit", label: "08 · Existing content audit" },
      { key: "content_planning", label: "09 · Content planning" },
      { key: "content_production", label: "10 · Draft & preview" },
      { key: "on_page_seo", label: "11 · On-page & linking" },
      { key: "publishing", label: "12 · Publish to WordPress" },
    ],
  },
];

export default function PhaseStatusPanel({ open, statuses, readiness }: Props) {
  return (
    <aside className={`phase-panel ${open ? "open" : ""}`} aria-label="Phase status">
      <h2>Phase status</h2>
      <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 0, lineHeight: 1.35 }}>
        Phases 1–12 in order — each step consumes the previous locked pack
      </p>

      {GROUPS.map((g) => (
        <div key={g.title} style={{ marginBottom: 12 }}>
          <div
            style={{
              fontSize: 10,
              fontWeight: 800,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--muted)",
              marginBottom: 4,
            }}
          >
            {g.title}
          </div>
          {g.note ? (
            <p style={{ fontSize: 10, color: "var(--muted)", margin: "0 0 6px" }}>{g.note}</p>
          ) : null}
          {g.phases.map((p) => (
            <div className="phase-item" key={p.key}>
              <span>{p.label}</span>
              <span
                className={`status-icon ${statusClass(statuses[p.key])}`}
                aria-label={statuses[p.key]}
              >
                {statusLabel(statuses[p.key])}
              </span>
            </div>
          ))}
        </div>
      ))}

      <div className="gate-box">
        <h3>Readiness</h3>
        <div
          style={{
            fontWeight: 800,
            fontSize: 26,
            fontFamily: "var(--font-display)",
            letterSpacing: "-0.03em",
          }}
        >
          {Math.round(readiness.overall)}%{" "}
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "var(--muted)",
              fontFamily: "var(--font-body)",
            }}
          >
            / {readiness.threshold}%
          </span>
        </div>
        {readiness.ready_for_phase5 ? (
          <p style={{ fontSize: 13, margin: "8px 0 0", color: "var(--green)", fontWeight: 700 }}>
            Phase 5–6 available
          </p>
        ) : (
          <>
            <p style={{ fontSize: 12, margin: "8px 0 0", color: "var(--muted)" }}>
              Score is informational — Phase 5–6 can run anytime.
            </p>
            <ul className="missing-list">
              {(readiness.missing.length ? readiness.missing : ["No blocking items listed"]).map(
                (m) => (
                  <li key={m}>{m}</li>
                )
              )}
            </ul>
          </>
        )}
      </div>
    </aside>
  );
}
