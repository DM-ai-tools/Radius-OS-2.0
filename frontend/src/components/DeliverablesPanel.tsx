import { useEffect, useMemo, useState, type ReactNode } from "react";
import PhaseBrowser, { type BrowserEntry, type BrowserState } from "./PhaseBrowser";
import { timeAgo } from "../lib/time";

export type DeliverableItem = {
  id: string;
  /** Short name for the switcher, e.g. "Discovery confirmation gate". */
  label: string;
  /** "Revised" or "Earlier run 2": what sets this pack apart from the current one. */
  tag?: string;
  /** The current pack for its step, as opposed to an earlier run. */
  current: boolean;
  createdAt?: string;
};

export type DeliverablePhase = {
  key: string;
  number: string;
  name: string;
  /** The pipeline status for the phase: complete, pending_signoff, in_progress, ... */
  status: string;
  items: DeliverableItem[];
};

type Props = {
  phases: DeliverablePhase[];
  /** A phase someone asked to see (from the Overview), if any. */
  requestedKey: string | null;
  onSelect: (key: string) => void;
  actions?: ReactNode;
  renderItem: (id: string) => ReactNode;
  onOpenOperations: () => void;
};

const NEEDS_YOU = new Set(["pending_signoff", "awaiting_service_selection"]);

function describe(status: string): { state: BrowserState; text: string } {
  if (status === "complete") return { state: "done", text: "Approved" };
  if (status === "pending_signoff") return { state: "wait", text: "Needs approval" };
  if (status === "awaiting_service_selection") return { state: "wait", text: "Choose services" };
  if (status === "in_progress") return { state: "run", text: "In progress" };
  return { state: "todo", text: "Draft" };
}

/** Revised packs win, then the current pack, then whatever came last. */
function defaultItem(items: DeliverableItem[]): DeliverableItem | undefined {
  const revised = items.filter((i) => i.tag === "Revised");
  if (revised.length) return revised[revised.length - 1];
  const current = items.filter((i) => i.current);
  return current[current.length - 1] || items[items.length - 1];
}

export default function DeliverablesPanel({ phases, requestedKey, onSelect, actions, renderItem, onOpenOperations }: Props) {
  const withContent = phases.filter((p) => p.items.length);
  const requested = phases.find((p) => p.key === requestedKey) || null;

  // The phase that needs a person comes first; otherwise the most recent one.
  const attention = withContent.find((p) => NEEDS_YOU.has(p.status));
  const fallbackKey = (attention || withContent[withContent.length - 1])?.key ?? null;
  const selectedKey = requested && requested.items.length ? requested.key : fallbackKey;
  const phase = phases.find((p) => p.key === selectedKey) || null;

  const [picked, setPicked] = useState<string | null>(null);
  const signature = phase ? phase.items.map((i) => i.id).join("|") : "";
  useEffect(() => setPicked(null), [selectedKey, signature]);

  const item = phase
    ? phase.items.find((i) => i.id === picked) || defaultItem(phase.items) || null
    : null;

  const entries: BrowserEntry[] = useMemo(
    () =>
      phases.map((p) => {
        const d = describe(p.status);
        const last = p.items[p.items.length - 1];
        return {
          key: p.key,
          number: p.number,
          name: p.name,
          state: d.state,
          statusText: d.text,
          meta: timeAgo(last?.createdAt) || undefined,
          hasContent: p.items.length > 0,
        };
      }),
    [phases]
  );

  const waiting = withContent.filter((p) => NEEDS_YOU.has(p.status)).length;
  const summary =
    `${withContent.length} of ${phases.length} phases have a deliverable.` +
    (waiting ? ` ${waiting} ${waiting === 1 ? "is" : "are"} waiting on you.` : "");

  const notice =
    requested && !requested.items.length ? (
      <>
        No deliverable yet for {requested.name}.{" "}
        <button type="button" className="pb-link" onClick={onOpenOperations}>
          Open operations
        </button>
      </>
    ) : null;

  return (
    <PhaseBrowser
      title="Deliverables"
      summary={summary}
      actions={actions}
      entries={entries}
      selectedKey={selectedKey}
      onSelect={onSelect}
      missingNote="not started"
      empty={
        <div className="ct-empty">
          <p style={{ margin: "0 0 12px" }}>No deliverables yet. Each phase adds one when it finishes.</p>
          <button type="button" className="btn btn-primary" onClick={onOpenOperations}>
            Open operations
          </button>
        </div>
      }
      notice={notice}
      detailMeta={item?.createdAt ? `Updated ${timeAgo(item.createdAt)}` : undefined}
      tabs={phase?.items.map((i) => ({ key: i.id, label: i.label, tag: i.tag }))}
      activeTab={item?.id}
      onTab={setPicked}
    >
      {item ? <div key={item.id}>{renderItem(item.id)}</div> : null}
    </PhaseBrowser>
  );
}
