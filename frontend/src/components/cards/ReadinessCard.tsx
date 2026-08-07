type Props = {
  payload: Record<string, unknown>;
  isQa: boolean;
  onGate: () => void;
};

export default function ReadinessCard({ payload, isQa, onGate }: Props) {
  const phases = (payload.phases || {}) as Record<string, { score: number; missing: string[] }>;
  return (
    <div className="structured-card">
      <h3 className="card-title">{String(payload.title || "Readiness Score")}</h3>
      <div style={{ fontSize: 28, fontWeight: 800, color: "var(--primary)" }}>
        {Math.round(Number(payload.overall || 0))}%
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>
        Threshold {Number(payload.threshold || 90)}%
      </div>
      {Object.entries(phases).map(([phase, data]) => (
        <div className="bar-row" key={phase}>
          <span>{phase}</span>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{
                width: `${data.score}%`,
                background: "var(--primary)",
              }}
            />
          </div>
          <span>{Math.round(data.score)}</span>
        </div>
      ))}
      <ul className="missing-list">
        {((payload.missing as string[]) || []).slice(0, 12).map((m) => (
          <li key={m}>{m}</li>
        ))}
      </ul>
      {isQa && Boolean(payload.can_gate) && (
        <div className="card-actions">
          <button className="btn btn-primary" onClick={onGate}>
            Ready for Phase 5
          </button>
        </div>
      )}
    </div>
  );
}
