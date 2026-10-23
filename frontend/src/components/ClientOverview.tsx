import { useEffect, useState } from "react";
import { api } from "../api";
import { timeAgo as fmtWhen } from "../lib/time";

export type OverviewPhase = {
  key: string;
  number: string;
  name: string;
  status: string;
  hasDeliverable: boolean;
};

export type OverviewAction = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
};

export type OverviewNeed = {
  key: string;
  phase: string;
  detail: string;
  action?: OverviewAction;
};

export type OverviewDeliverable = {
  id: string;
  phaseKey: string;
  phase: string;
  title: string;
  createdAt?: string;
};

export type OverviewEvent = {
  id: string;
  text: string;
  createdAt?: string;
};

type Props = {
  token: string | null;
  clientId?: string;
  clientUrl?: string | null;
  industry?: string | null;
  phases: OverviewPhase[];
  readiness: number;
  nextStep: string;
  running: string | null;
  primary: OverviewAction | null;
  needs: OverviewNeed[];
  deliverables: OverviewDeliverable[];
  activity: OverviewEvent[];
  showSpend: boolean;
  onOpenPhase: (phaseKey: string) => void;
  onOpenDeliverables: () => void;
  onOpenCosts: () => void;
};

/** More than this many items would bury the approvals, so the rest wait behind a toggle. */
const NEEDS_SHOWN = 5;

const STATUS_TEXT: Record<string, string> = {
  complete: "Approved",
  pending_signoff: "Needs approval",
  awaiting_service_selection: "Choose services",
  in_progress: "In progress",
};

function toneFor(status: string): string {
  if (status === "complete") return "done";
  if (status === "pending_signoff" || status === "awaiting_service_selection") return "wait";
  if (status === "in_progress") return "run";
  return "todo";
}

function fmtUsd(n: number): string {
  if (n > 0 && n < 0.01) return "<$0.01";
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function hostOf(url?: string | null): string {
  if (!url) return "";
  try {
    return new URL(url.includes("://") ? url : `https://${url}`).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** The client's 30-day spend, shown only to roles that can see costs. */
function useClientSpend(token: string | null, clientId: string | undefined, enabled: boolean) {
  const [spend, setSpend] = useState<number | null>(null);
  useEffect(() => {
    if (!enabled || !clientId) {
      setSpend(null);
      return;
    }
    let live = true;
    api
      .clientCosts(token, clientId, 30)
      .then((r) => live && setSpend(r.estimated_cost_usd || 0))
      .catch(() => live && setSpend(null));
    return () => {
      live = false;
    };
  }, [token, clientId, enabled]);
  return spend;
}

export default function ClientOverview(props: Props) {
  const {
    phases,
    readiness,
    nextStep,
    running,
    primary,
    needs,
    deliverables,
    activity,
    onOpenPhase,
  } = props;
  const spend = useClientSpend(props.token, props.clientId, props.showSpend);
  const [showAllNeeds, setShowAllNeeds] = useState(false);
  const visibleNeeds = showAllNeeds ? needs : needs.slice(0, NEEDS_SHOWN);
  const done = phases.filter((p) => p.status === "complete").length;
  const host = hostOf(props.clientUrl);

  return (
    <div className={`ov${running ? " is-live" : ""}`}>
      <section className="ov-hero" aria-label="Where this client stands">
        {(host || props.industry) && (
          <p className="ov-client">
            {host ? <span>{host}</span> : null}
            {props.industry ? <span>{props.industry}</span> : null}
          </p>
        )}
        <div className="ov-next">
          <p className="ov-next-text">
            {running ? (
              <>
                <span className="ov-live" aria-hidden="true" />
                {running}
              </>
            ) : (
              nextStep
            )}
          </p>
          {primary ? (
            <button type="button" className="btn btn-primary ov-cta" onClick={primary.onClick} disabled={primary.disabled}>
              {primary.label}
            </button>
          ) : null}
        </div>

        <ol className="ov-track" aria-label={`${done} of ${phases.length} phases approved`}>
          {phases.map((p) => {
            const tone = toneFor(p.status);
            const state = STATUS_TEXT[p.status] || "Not started";
            return (
              <li key={p.key} className={`is-${tone}`}>
                <button
                  type="button"
                  onClick={() => onOpenPhase(p.key)}
                  disabled={!p.hasDeliverable}
                  title={`${p.number} ${p.name}: ${state}`}
                  aria-label={`${p.name}, ${state}${p.hasDeliverable ? ", open deliverable" : ""}`}
                >
                  <span className="ov-seg" />
                  <span className="ov-seg-no">{p.number}</span>
                </button>
              </li>
            );
          })}
        </ol>

        <p className="ov-facts">
          <span>
            <strong>
              {done} of {phases.length}
            </strong>{" "}
            phases approved
          </span>
          <span>
            Readiness <strong>{Math.round(readiness)}</strong>/100
          </span>
          {spend != null ? (
            <button type="button" className="ov-link" onClick={props.onOpenCosts}>
              <strong>{fmtUsd(spend)}</strong> spent in the last 30 days
            </button>
          ) : null}
        </p>
      </section>

      <div className="ov-body">
        <div className="ov-col">
          <section className="ov-panel" aria-label="Needs you">
            <h3>Needs you</h3>
            {needs.length ? (
              <ul className="ov-needs">
                {visibleNeeds.map((n) => (
                  <li key={n.key}>
                    <span className="ov-need-text">
                      <strong>{n.phase}</strong>
                      <span className="ov-muted">{n.detail}</span>
                    </span>
                    {n.action ? (
                      <button
                        type="button"
                        className="btn btn-ghost ov-need-go"
                        onClick={n.action.onClick}
                        disabled={n.action.disabled}
                      >
                        {n.action.label}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ov-muted ov-empty">Nothing is waiting on you.</p>
            )}
            {needs.length > NEEDS_SHOWN ? (
              <button type="button" className="ov-link ov-more" onClick={() => setShowAllNeeds((v) => !v)}>
                {showAllNeeds ? "Show fewer" : `Show all ${needs.length}`}
              </button>
            ) : null}
          </section>

          <section className="ov-panel" aria-label="Latest deliverables">
            <div className="ov-panel-head">
              <h3>Latest deliverables</h3>
              {deliverables.length ? (
                <button type="button" className="ov-link" onClick={props.onOpenDeliverables}>
                  See all
                </button>
              ) : null}
            </div>
            {deliverables.length ? (
              <ul className="ov-list">
                {deliverables.map((d) => (
                  <li key={d.id}>
                    <button type="button" className="ov-row" onClick={() => onOpenPhase(d.phaseKey)}>
                      <span className="ov-row-main">
                        {d.title}
                        <span className="ov-muted">{d.phase}</span>
                      </span>
                      <span className="ov-muted ov-when">{fmtWhen(d.createdAt)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ov-muted ov-empty">Deliverables appear here as each phase finishes.</p>
            )}
          </section>
        </div>

        <section className="ov-panel" aria-label="Recent activity">
          <h3>Recent activity</h3>
          {activity.length ? (
            <ul className="ov-activity">
              {activity.map((e) => (
                <li key={e.id}>
                  <span>{e.text}</span>
                  <span className="ov-muted ov-when">{fmtWhen(e.createdAt)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ov-muted ov-empty">No activity yet for this client.</p>
          )}
        </section>
      </div>
    </div>
  );
}
