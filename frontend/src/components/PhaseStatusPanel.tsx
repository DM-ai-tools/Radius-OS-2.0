type Props = {
  open: boolean;
  statuses: {
    discovery: string;
    tracking: string;
    website: string;
    competitor: string;
  };
  readiness: {
    overall: number;
    missing: string[];
    can_gate: boolean;
    ready_for_phase5: boolean;
    threshold: number;
  };
  isQa: boolean;
  onGate: () => void;
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

export default function PhaseStatusPanel({
  open,
  statuses,
  readiness,
  isQa,
  onGate,
}: Props) {
  const phases = [
    { key: "discovery", label: "01 · Discovery" },
    { key: "tracking", label: "02 · Tracking" },
    { key: "website", label: "03 · Website" },
    { key: "competitor", label: "04 · Competitor" },
  ] as const;

  return (
    <aside className={`phase-panel ${open ? "open" : ""}`} aria-label="Phase status">
      <h2>Phase status</h2>
      {phases.map((p) => (
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

      <div className="gate-box">
        <h3>Readiness gate</h3>
        <div style={{ fontWeight: 800, fontSize: 26, fontFamily: "var(--font-display)", letterSpacing: "-0.03em" }}>
          {Math.round(readiness.overall)}%{" "}
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--muted)", fontFamily: "var(--font-body)" }}>
            / {readiness.threshold}%
          </span>
        </div>
        {readiness.ready_for_phase5 ? (
          <p style={{ fontSize: 13, margin: "8px 0 0", color: "var(--green)", fontWeight: 700 }}>
            Ready for Phase 5
          </p>
        ) : (
          <>
            <ul className="missing-list">
              {(readiness.missing.length ? readiness.missing : ["No blocking items listed"]).map(
                (m) => (
                  <li key={m}>{m}</li>
                )
              )}
            </ul>
            {isQa && (
              <button
                className="btn btn-primary"
                style={{ marginTop: 12, width: "100%" }}
                disabled={!readiness.can_gate}
                onClick={onGate}
              >
                Run readiness gate
              </button>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
