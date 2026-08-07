/** Shared structured-value renderer — no raw JSON dumps in cards. */

export function humanLabel(key: string): string {
  return key
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

export function numericEntries(obj: Record<string, unknown>): [string, number][] | null {
  const entries = Object.entries(obj);
  if (!entries.length || entries.length > 8) return null;
  if (!entries.every(([, v]) => typeof v === "number" && Number.isFinite(v))) return null;
  return entries as [string, number][];
}

/** Bar-chart renderer for all-numeric objects (e.g. score breakdowns) —
 * shared so the same data renders identically in review cards and Playground. */
export function NumericSplitBars({ entries }: { entries: [string, number][] }) {
  const total = entries.reduce((s, [, n]) => s + Math.abs(n), 0) || 1;
  const looksLikePercent = entries.every(([, n]) => n >= 0 && n <= 100) && total >= 90 && total <= 110;
  return (
    <div className="split-bars">
      {entries.map(([k, n]) => {
        const pct = looksLikePercent ? n : (Math.abs(n) / total) * 100;
        return (
          <div key={k} className="split-row">
            <div className="split-meta">
              <span>{humanLabel(k)}</span>
              <strong>{looksLikePercent ? `${n}%` : n}</strong>
            </div>
            <div className="split-track">
              <i style={{ width: `${Math.min(100, Math.max(2, pct))}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function PresentableValue({ value }: { value: unknown }) {
  if (value == null || value === "") {
    return <span className="pv-muted">—</span>;
  }
  if (typeof value === "boolean") {
    return <span>{value ? "Yes" : "No"}</span>;
  }
  if (typeof value === "number") {
    return <span className="pv-num">{Number.isInteger(value) ? value : value.toFixed(2)}</span>;
  }
  if (typeof value === "string") {
    return <span className="pv-text">{value}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="pv-muted">None</span>;
    }
    if (value.every((v) => typeof v === "string" || typeof v === "number")) {
      return (
        <div className="chip-row">
          {value.map((v, i) => (
            <span key={`${v}-${i}`} className="data-chip">
              {String(v)}
            </span>
          ))}
        </div>
      );
    }
    // Array of objects — compact rows
    if (value.every(isPlainObject)) {
      return (
        <div className="pv-obj-list">
          {value.slice(0, 12).map((row, i) => (
            <div key={i} className="pv-obj-item">
              {Object.entries(row).map(([k, v]) => (
                <div key={k} className="pv-inline">
                  <span className="pv-k">{humanLabel(k)}</span>
                  <PresentableValue value={v} />
                </div>
              ))}
            </div>
          ))}
          {value.length > 12 ? (
            <span className="pv-muted">+{value.length - 12} more</span>
          ) : null}
        </div>
      );
    }
    return (
      <ul className="pv-list">
        {value.slice(0, 20).map((v, i) => (
          <li key={i}>
            <PresentableValue value={v} />
          </li>
        ))}
      </ul>
    );
  }
  if (isPlainObject(value)) {
    const nums = numericEntries(value);
    if (nums) {
      return <NumericSplitBars entries={nums} />;
    }
    return (
      <div className="nested-fields">
        {Object.entries(value).map(([k, v]) => (
          <div key={k} className="nested-field">
            <span className="nested-key">{humanLabel(k)}</span>
            <div className="nested-val">
              <PresentableValue value={v} />
            </div>
          </div>
        ))}
      </div>
    );
  }
  return <span>{String(value)}</span>;
}

export function MetricGrid({
  items,
}: {
  items: { label: string; value: unknown; tone?: "ok" | "warn" | "bad" | "neutral" }[];
}) {
  const visible = items.filter((i) => i.value != null && i.value !== "");
  if (!visible.length) return null;
  return (
    <div className="metric-grid">
      {visible.map((m) => (
        <div key={m.label} className={`metric-tile tone-${m.tone || "neutral"}`}>
          <div className="metric-label">{m.label}</div>
          <div className="metric-value">
            {typeof m.value === "number" || typeof m.value === "string" ? (
              m.value
            ) : (
              <PresentableValue value={m.value} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export function FieldGrid({
  data,
  skipKeys = [],
}: {
  data: Record<string, unknown>;
  skipKeys?: string[];
}) {
  const skip = new Set(skipKeys);
  const entries = Object.entries(data).filter(([k]) => !skip.has(k));
  if (!entries.length) {
    return <p className="pv-muted">No details for this section.</p>;
  }
  return (
    <div className="present-fields">
      {entries.map(([k, v]) => (
        <div key={k} className="present-field">
          <div className="present-key">{humanLabel(k)}</div>
          <div className="present-val">
            <PresentableValue value={v} />
          </div>
        </div>
      ))}
    </div>
  );
}
