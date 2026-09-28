import React from "react";
import { api, type BenchmarkReport } from "../../api";

/**
 * Side-by-side benchmark of our content against a service's competitors.
 *
 * A competitor whose content could not be retrieved renders as a failed row with
 * its reason; the rest of the report still shows. Dimensions that could not be
 * measured say why rather than showing a zero — a cached snapshot has no body
 * text, so depth and readability genuinely cannot be scored from it.
 */

type Props = {
  token: string;
  clientId: string;
  serviceId: string;
  serviceName: string;
  canAct: boolean;
  /** Changes when the competitor selection changes, to invalidate a stale report. */
  selectionKey?: string;
};

function scoreClass(score: number | null | undefined): string {
  if (score === null || score === undefined) return "is-unknown";
  if (score >= 75) return "is-ahead";
  if (score >= 45) return "is-level";
  return "is-behind";
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export default function BenchmarkReportPanel({
  token,
  clientId,
  serviceId,
  serviceName,
  canAct,
  selectionKey,
}: Props) {
  const [report, setReport] = React.useState<BenchmarkReport | null>(null);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [allowLive, setAllowLive] = React.useState(true);
  const [stale, setStale] = React.useState(false);

  // A changed competitor selection makes an existing report out of date.
  const firstRender = React.useRef(true);
  React.useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (report) setStale(true);
  }, [selectionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const data = await api.benchmarkService(token, clientId, serviceId, {
        allow_live_fetch: allowLive,
      });
      setReport(data);
      setStale(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The benchmark could not be run.");
    } finally {
      setRunning(false);
    }
  }

  const dimensionKeys = React.useMemo(() => {
    const keys: Array<{ key: string; label: string }> = [];
    for (const row of report?.competitors || []) {
      for (const dim of row.dimensions || []) {
        if (!keys.some((k) => k.key === dim.key)) {
          keys.push({ key: dim.key, label: dim.label });
        }
      }
    }
    return keys;
  }, [report]);

  const retrieved = (report?.competitors || []).filter((c) => c.ok);
  const failed = (report?.competitors || []).filter((c) => !c.ok);

  return (
    <section className="benchmark">
      <header className="benchmark__head">
        <h4>Competitor benchmark — {serviceName}</h4>
        <div className="benchmark__controls">
          <label className="benchmark__toggle">
            <input
              type="checkbox"
              checked={allowLive}
              disabled={running}
              onChange={(e) => setAllowLive(e.target.checked)}
            />
            Fetch competitor pages live
            <span
              className="benchmark__hint"
              title="Live fetching is the only way to measure depth and readability. Off uses pages already crawled in Phase 5."
            >
              ?
            </span>
          </label>
          <button type="button" disabled={!canAct || running} onClick={() => void run()}>
            {running ? "Benchmarking…" : report ? "Re-run" : "Run benchmark"}
          </button>
        </div>
      </header>

      {error ? (
        <p className="benchmark__error" role="alert">
          {error}{" "}
          <button type="button" onClick={() => void run()}>
            Retry
          </button>
        </p>
      ) : null}

      {stale ? (
        <p className="benchmark__notice">
          The competitor selection changed — re-run to refresh this report.
        </p>
      ) : null}

      {running && !report ? (
        <p className="benchmark__muted">
          Retrieving competitor content and comparing… this can take up to a minute.
        </p>
      ) : null}

      {!report && !running ? (
        <p className="benchmark__muted">
          Run a benchmark to compare this service's content against its selected
          competitors.
        </p>
      ) : null}

      {report ? (
        <>
          {report.overall ? (
            <div className={`benchmark__overall ${scoreClass(report.overall.score)}`}>
              <strong>
                {report.overall.score === null ? "—" : `${report.overall.score}/100`}
              </strong>
              <span>{report.overall.verdict}</span>
              <span className="benchmark__muted">
                {report.overall.competitors_compared} compared
                {report.overall.competitors_failed
                  ? `, ${report.overall.competitors_failed} unavailable`
                  : ""}
                {" · our draft "}
                {report.overall.our_word_count} words
              </span>
            </div>
          ) : null}

          {report.notes?.length ? (
            <ul className="benchmark__notes">
              {report.notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          ) : null}

          {retrieved.length > 0 ? (
            <div className="benchmark__table-wrap">
              <table className="benchmark__table">
                <thead>
                  <tr>
                    <th scope="col">Dimension</th>
                    <th scope="col">Ours</th>
                    {retrieved.map((c) => (
                      <th key={c.competitor_id} scope="col">
                        {c.name}
                        <span className="benchmark__source">{c.source}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {dimensionKeys.map(({ key, label }) => {
                    const first = retrieved[0]?.dimensions.find((d) => d.key === key);
                    return (
                      <tr key={key}>
                        <th scope="row">{label}</th>
                        <td>{renderValue(first?.ours)}</td>
                        {retrieved.map((c) => {
                          const dim = c.dimensions.find((d) => d.key === key);
                          if (!dim) return <td key={c.competitor_id}>—</td>;
                          if (!dim.measurable) {
                            return (
                              <td key={c.competitor_id} className="is-unknown">
                                <span title={dim.reason}>not measurable</span>
                              </td>
                            );
                          }
                          return (
                            <td key={c.competitor_id} className={scoreClass(dim.score)}>
                              {renderValue(dim.theirs)}
                              <span className="benchmark__score">{dim.score}</span>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                  <tr className="benchmark__verdict-row">
                    <th scope="row">Verdict</th>
                    <td>—</td>
                    {retrieved.map((c) => (
                      <td key={c.competitor_id} className={scoreClass(c.score)}>
                        {c.score === null ? "—" : `${c.score}/100`}
                        <span className="benchmark__muted"> {c.verdict}</span>
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          ) : null}

          {failed.length > 0 ? (
            <div className="benchmark__failed">
              <h5>Could not retrieve ({failed.length})</h5>
              <ul>
                {failed.map((c) => (
                  <li key={c.competitor_id}>
                    <strong>{c.name || c.domain}</strong> — {c.error || "unknown error"}
                  </li>
                ))}
              </ul>
              <p className="benchmark__muted">
                Paste their content manually, or re-run with live fetching enabled.
              </p>
            </div>
          ) : null}

          {report.gaps?.length ? (
            <div className="benchmark__gaps">
              <h5>Content gaps ({report.gaps.length})</h5>
              <ul>
                {report.gaps.slice(0, 12).map((gap, i) => (
                  <li key={i}>
                    <span className="benchmark__gap-text">{gap.gap}</span>
                    <span className="benchmark__muted">
                      {" "}
                      — covered by {gap.competitors.join(", ")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {report.recommendations?.length ? (
            <div className="benchmark__recs">
              <h5>What to fix, in order</h5>
              <ol>
                {report.recommendations.map((rec) => (
                  <li key={rec.priority}>
                    {rec.recommendation}
                    <span className="benchmark__muted">
                      {" "}
                      ({rec.dimension}
                      {rec.raised_by.length > 1
                        ? ` · raised by ${rec.raised_by.length} competitors`
                        : ""}
                      )
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
