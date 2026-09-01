type CheckRow = {
  check_id?: string;
  parameter?: string;
  category?: string;
  status?: string;
  severity?: string;
  message?: string;
  evidence?: string | null;
  recommended_correction?: string | null;
  source?: string;
};

type Props = {
  payload: Record<string, unknown>;
  onRequestChange?: (draft: string) => void;
};

function statusClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "passed") return "ok";
  if (s === "failed") return "bad";
  if (s === "warning") return "warn";
  return "info";
}

function decisionClass(decision: string): string {
  if (decision === "pass") return "ok";
  if (decision === "pass_with_warnings") return "warn";
  if (decision === "needs_revision" || decision === "reject") return "bad";
  return "info";
}

export default function ValidationCard({ payload, onRequestChange }: Props) {
  const title = String(payload.title || `Validation: ${payload.phase_label || "Phase"}`);
  const decision = String(payload.decision || "unavailable");
  const summary = String(payload.summary || "");
  const iteration = Number(payload.iteration || 1);
  const checks = Array.isArray(payload.checks) ? (payload.checks as CheckRow[]) : [];
  const failed = checks.filter((c) => String(c.status).toLowerCase() === "failed");
  const passed = checks.filter((c) => String(c.status).toLowerCase() === "passed");

  const draft =
    failed.length > 0
      ? `Revise ${payload.phase_label || "this phase"}: ${failed
          .slice(0, 3)
          .map((c) => c.recommended_correction || c.message)
          .filter(Boolean)
          .join("; ")}`
      : `Change ${payload.phase_label || "this report"}: `;

  return (
    <article className="val-card">
      <header className="val-head">
        <div>
          <p className="val-kicker">Validation</p>
          <h3>{title}</h3>
          {summary ? <p className="val-summary">{summary}</p> : null}
        </div>
        <div className="val-head-meta">
          <span className={`val-pill ${decisionClass(decision)}`}>{decision.replaceAll("_", " ")}</span>
          <span className="val-meta">Attempt {iteration}</span>
          <span className="val-meta">
            {passed.length} passed · {failed.length} failed
          </span>
        </div>
      </header>

      <ul className="val-checks">
        {checks.map((c, i) => {
          const st = String(c.status || "");
          return (
            <li key={c.check_id || i} className={`val-check ${statusClass(st)}`}>
              <div className="val-check-top">
                <span className={`val-pill ${statusClass(st)}`}>{st || "—"}</span>
                <span className="val-check-id">{c.check_id || c.parameter || `check-${i + 1}`}</span>
                <span className="val-meta">{c.category}</span>
                {c.severity ? <span className="val-meta">{c.severity}</span> : null}
              </div>
              {c.message ? <p className="val-msg">{c.message}</p> : null}
              {c.evidence ? <p className="val-ev">{c.evidence}</p> : null}
              {c.recommended_correction ? (
                <p className="val-fix">Fix: {c.recommended_correction}</p>
              ) : null}
            </li>
          );
        })}
      </ul>

      {onRequestChange ? (
        <div className="val-actions">
          <button type="button" className="btn btn-primary" onClick={() => onRequestChange(draft)}>
            Request change
          </button>
        </div>
      ) : null}
    </article>
  );
}
