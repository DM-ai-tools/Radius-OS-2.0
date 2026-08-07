/** Clean, stacked renderer for playground shared-memory cards (no nested grids). */

import { NumericSplitBars, humanLabel, numericEntries } from "./cards/PresentableValue";
import { statusTone } from "../lib/statusTone";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function looksLikePlatform(row: Record<string, unknown>): boolean {
  return (
    ("label" in row || "key" in row || "system" in row) &&
    ("status" in row || "detail" in row || "note" in row || "mode" in row)
  );
}

function platformCard(row: Record<string, unknown>) {
  const name = String(row.system || row.label || row.key || "System");
  const statusRaw = String(row.status || "");
  const status = statusRaw
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const note = String(row.note || row.detail || "").trim();
  return { name, status, note };
}

function BlockValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null || value === "") {
    return <span className="pv-muted">—</span>;
  }
  if (typeof value === "boolean") {
    return <span>{value ? "Yes" : "No"}</span>;
  }
  if (typeof value === "number") {
    return <span className="pv-num">{Number.isInteger(value) ? value : value.toFixed(1)}</span>;
  }
  if (typeof value === "string") {
    return <p className="pg-text">{value}</p>;
  }

  if (Array.isArray(value)) {
    if (!value.length) return <span className="pv-muted">None</span>;

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

    if (value.every(isPlainObject)) {
      if (value.every(looksLikePlatform)) {
        return (
          <ul className="pg-access-list">
            {value.map((row, i) => {
              const p = platformCard(row);
              return (
                <li key={i} className="pg-access-item">
                  <div className="pg-access-top">
                    <strong>{p.name}</strong>
                    <span className={`status-pill ${statusTone(p.status)}`}>{p.status || "—"}</span>
                  </div>
                  {p.note ? <p className="pg-text muted">{p.note}</p> : null}
                </li>
              );
            })}
          </ul>
        );
      }

      return (
        <ul className="pg-stack-list">
          {value.slice(0, 16).map((row, i) => (
            <li key={i} className="pg-stack-item">
              {Object.entries(row)
                .filter(([k]) => !["key", "mode", "automated", "raw"].includes(k))
                .map(([k, v]) => (
                  <div key={k} className="pg-kv">
                    <span className="pg-k">{humanLabel(k)}</span>
                    <BlockValue value={v} depth={depth + 1} />
                  </div>
                ))}
            </li>
          ))}
        </ul>
      );
    }

    return (
      <ul className="pg-stack-list">
        {value.slice(0, 20).map((v, i) => (
          <li key={i}>
            <BlockValue value={v} depth={depth + 1} />
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

    // Flat string/number map (e.g. phase statuses) — simple rows
    const entries = Object.entries(value).filter(
      ([k, v]) =>
        k !== "source" &&
        k !== "crawl_source" &&
        k !== "_draft" &&
        v != null &&
        v !== "" &&
        !(typeof v === "object" && !Array.isArray(v) && !Object.keys(v as object).length)
    );
    const allScalar = entries.every(
      ([, v]) => v == null || ["string", "number", "boolean"].includes(typeof v)
    );

    if (allScalar) {
      return (
        <dl className="pg-kv-list">
          {entries.map(([k, v]) => (
            <div key={k} className="pg-kv-row">
              <dt>{humanLabel(k)}</dt>
              <dd>
                {typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      );
    }

    // Nested groups — stack as titled blocks (never side-by-side columns)
    return (
      <div className={`pg-groups${depth > 0 ? " nested" : ""}`}>
        {entries.map(([k, v]) => (
          <section key={k} className="pg-group">
            <h4 className="pg-group-title">{humanLabel(k)}</h4>
            <BlockValue value={v} depth={depth + 1} />
          </section>
        ))}
      </div>
    );
  }

  return <span>{String(value)}</span>;
}

export default function PlaygroundBlocks({ value }: { value: unknown }) {
  return (
    <div className="pg-blocks">
      <BlockValue value={value} />
    </div>
  );
}
