import { useCallback, useEffect, useState } from "react";
import { timeAgo } from "../lib/time";
import { api, EnginePhase, EngineProblem, EngineProvider, EngineRoomPayload, EngineRun } from "../api";

type Props = {
  token: string | null;
  onError?: (msg: string) => void;
};

const WINDOWS: Array<{ days: number; label: string }> = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
];

function fmtCount(n: number | null | undefined): string {
  return Number(n || 0).toLocaleString();
}

const fmtWhen = (iso: string | null | undefined) => timeAgo(iso) || "—";

function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

const PROVIDER_STATE: Record<string, { label: string; tone: string }> = {
  working: { label: "Working", tone: "good" },
  intermittent: { label: "Some failures", tone: "warn" },
  failing: { label: "Failing", tone: "bad" },
  idle: { label: "No calls yet", tone: "muted" },
  no_key: { label: "No key", tone: "muted" },
  test_mode: { label: "Test mode", tone: "warn" },
  set_up: { label: "Key set", tone: "muted" },
};

const RUN_STATE: Record<string, { label: string; tone: string }> = {
  succeeded: { label: "Finished", tone: "good" },
  running: { label: "Running", tone: "info" },
  failed: { label: "Failed", tone: "bad" },
  stopped: { label: "Stopped", tone: "bad" },
};

/* ----------------------------------------------------------------- pieces */

function ProblemList({ problems }: { problems: EngineProblem[] }) {
  return (
    <section className="er-section" aria-label="Problems">
      <h3>Needs attention</h3>
      <ul className="er-problems">
        {problems.map((p, i) => (
          <li key={`${p.title}-${i}`} className={`er-problem is-${p.severity}`}>
            <div className="er-problem-text">
              <strong>{p.title}</strong>
              {p.detail ? <span className="ct-muted">{p.detail}</span> : null}
              {p.clients?.length ? (
                <span className="ct-muted">Clients: {p.clients.join(", ")}</span>
              ) : null}
            </div>
            <div className="er-problem-meta">
              {p.count > 1 ? <span className="er-count">{fmtCount(p.count)} times</span> : null}
              {p.last_at ? <span className="ct-muted">last {fmtWhen(p.last_at)}</span> : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProviderGrid({ providers }: { providers: EngineProvider[] }) {
  return (
    <section className="er-section" aria-label="Data providers">
      <h3>Providers</h3>
      <ul className="er-providers">
        {providers.map((p) => {
          const st = PROVIDER_STATE[p.state] || { label: p.state, tone: "muted" };
          return (
            <li key={p.provider} className={`er-provider is-${st.tone}`}>
              <div className="er-provider-head">
                <span className="er-provider-name">{p.name}</span>
                <span className={`er-pill is-${st.tone}`}>{st.label}</span>
              </div>
              {p.calls ? (
                <p className="er-provider-line">
                  {fmtCount(p.calls)} calls
                  {p.failed ? <span className="ct-warn">, {fmtCount(p.failed)} failed</span> : null}
                </p>
              ) : null}
              {p.state === "failing" || p.state === "intermittent" ? (
                <>
                  {p.last_error ? (
                    <p className="er-provider-error" title={p.last_error.message}>
                      {p.last_error.message}
                    </p>
                  ) : null}
                  <p className="er-provider-line ct-muted">
                    Last worked {p.last_success_at ? fmtWhen(p.last_success_at) : "never in this period"}
                  </p>
                </>
              ) : p.state === "working" && p.last_success_at ? (
                <p className="er-provider-line ct-muted">Last call {fmtWhen(p.last_success_at)}</p>
              ) : p.state === "set_up" ? (
                <p className="er-provider-line ct-muted">Calls are not tracked</p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function PhaseHealth({ phases }: { phases: EnginePhase[] }) {
  return (
    <section className="er-section er-panel" aria-label="Phases">
      <h3>Phases</h3>
      <ol className="er-phases">
        {phases.map((p) => {
          const st = p.last_state ? RUN_STATE[p.last_state] : undefined;
          return (
            <li key={p.agent_key} className={p.enabled ? undefined : "is-off"}>
              <span className="er-phase-no ct-muted">{p.phase ?? "—"}</span>
              <span className="er-phase-name">
                {p.label}
                <span className="ct-muted">
                  {!p.enabled
                    ? "Turned off"
                    : p.runs
                      ? `${fmtCount(p.runs)} ${p.runs === 1 ? "run" : "runs"}${
                          p.typical_seconds != null ? `, usually ${fmtDuration(p.typical_seconds)}` : ""
                        }`
                      : "Not run in this period"}
                </span>
              </span>
              {st ? (
                <span className={`er-dot is-${st.tone}`} title={`Last run ${st.label.toLowerCase()} ${fmtWhen(p.last_run_at)}`}>
                  {p.failed ? `${p.failed} failed` : st.label}
                </span>
              ) : (
                <span />
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function RecentRuns({ runs }: { runs: EngineRun[] }) {
  return (
    <section className="er-section er-panel" aria-label="Recent runs">
      <h3>Recent runs</h3>
      {runs.length ? (
        <ul className="er-runs">
          {runs.map((r) => {
            const st = RUN_STATE[r.state] || { label: r.state, tone: "muted" };
            return (
              <li key={r.id}>
                <span className="er-run-main">
                  {r.label}
                  <span className="ct-muted">
                    {r.client_name || "No client"}, {fmtWhen(r.created_at)}
                  </span>
                </span>
                <span className="ct-num ct-muted">{fmtDuration(r.seconds)}</span>
                <span className={`er-pill is-${st.tone}`} title={r.error || undefined}>
                  {st.label}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="ct-muted">No runs in this period.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------- panel */

export default function EngineRoomPanel({ token, onError }: Props) {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<EngineRoomPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      setData(await api.engineRoom(token, days));
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "The engine room could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [token, days, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !data) {
    return (
      <div className="ct" aria-busy="true">
        <div className="ct-skeleton" />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="ct">
        <p className="ct-empty">
          The engine room could not be loaded.{" "}
          <button type="button" className="btn btn-ghost" onClick={() => void load()}>
            Try again
          </button>
        </p>
      </div>
    );
  }

  const s = data.summary;
  const problems = (data.problems || []).filter((p) => p.severity !== "info");
  const notes = (data.problems || []).filter((p) => p.severity === "info");
  const errors = problems.filter((p) => p.severity === "error").length;
  const failedRate = s.api_calls ? Math.round(((s.failed_calls || 0) / s.api_calls) * 100) : 0;
  const period = WINDOWS.find((w) => w.days === days)?.label.toLowerCase() || `${days} days`;

  return (
    <div className="ct er">
      <header className="ct-head">
        <div>
          <h2>Engine room</h2>
          <p className="ct-muted">Health of the agents, data providers and runs.</p>
        </div>
        <div className="ct-controls">
          <div className="ct-seg" role="radiogroup" aria-label="Period">
            {WINDOWS.map((w) => (
              <button
                key={w.days}
                type="button"
                role="radio"
                aria-checked={days === w.days}
                className={days === w.days ? "is-on" : undefined}
                onClick={() => setDays(w.days)}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button type="button" className="btn btn-ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      <section className={`er-hero ${errors ? "is-bad" : problems.length ? "is-warn" : "is-good"}`} aria-label="Status">
        <p className="er-status">
          <span className="er-status-light" aria-hidden="true" />
          {problems.length
            ? `${problems.length} ${problems.length === 1 ? "problem needs" : "problems need"} attention`
            : "Everything is running normally"}
        </p>
        <p className="ct-facts">
          <span>
            <strong>{fmtCount(s.runs)}</strong> runs {days === 1 ? "today" : `in the last ${period}`}
            {s.failed_runs ? <span className="ct-warn">, {fmtCount(s.failed_runs)} did not finish</span> : null}
          </span>
          <span>
            <strong>{fmtCount(s.api_calls)}</strong> API calls
            {s.failed_calls ? (
              <span className="ct-warn">
                , {fmtCount(s.failed_calls)} failed ({failedRate < 1 ? "<1" : failedRate}%)
              </span>
            ) : null}
          </span>
          <span>
            <strong>
              {s.agents_ok} of {s.agents_total}
            </strong>{" "}
            agents on
          </span>
        </p>
      </section>

      {problems.length ? <ProblemList problems={problems} /> : null}

      <ProviderGrid providers={data.providers || []} />

      <div className="er-body">
        <PhaseHealth phases={data.phases || []} />
        <RecentRuns runs={data.recent_runs || []} />
      </div>

      {notes.length ? (
        <p className="ct-muted er-notes">{notes.map((n) => n.title).join(". ")}.</p>
      ) : null}
    </div>
  );
}
