import type { ReactNode } from "react";

export type BrowserState = "done" | "wait" | "run" | "todo";

export type BrowserEntry = {
  key: string;
  /** Position in the pipeline, e.g. "05" or "06a". Omit for entries outside the sequence. */
  number?: string;
  name: string;
  state: BrowserState;
  /** Words next to the status dot, e.g. "Approved" or "Needs approval". */
  statusText: string;
  /** Right-hand detail in the list, e.g. "42 min ago". */
  meta?: string;
  /** Entries without content are not selectable; they are summed up in one line. */
  hasContent: boolean;
};

export type BrowserTab = { key: string; label: string; tag?: string };

type Props = {
  title: string;
  summary: ReactNode;
  actions?: ReactNode;
  entries: BrowserEntry[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  /** Completes "N phases ..." for the entries that have no content yet. */
  missingNote: string;
  /** Shown instead of the browser when no entry has content. */
  empty?: ReactNode;
  /** Shown above the detail, e.g. when the requested phase has nothing. */
  notice?: ReactNode;
  detailMeta?: string;
  detailActions?: ReactNode;
  tabs?: BrowserTab[];
  activeTab?: string;
  onTab?: (key: string) => void;
  children: ReactNode;
};

/**
 * One list of phases and one detail pane. Used by Deliverables and Client memory so the
 * two read the same: pick a phase on the left, read just that phase on the right.
 */
export default function PhaseBrowser(props: Props) {
  const { entries, selectedKey } = props;
  const available = entries.filter((e) => e.hasContent);
  const missing = entries.filter((e) => !e.hasContent);
  const selected = entries.find((e) => e.key === selectedKey) || null;

  return (
    <div className="ct pb">
      <header className="ct-head">
        <div>
          <h2>{props.title}</h2>
          <p className="ct-muted">{props.summary}</p>
        </div>
        {props.actions ? <div className="ct-controls">{props.actions}</div> : null}
      </header>

      {!available.length ? (
        props.empty || <p className="ct-empty">Nothing here yet.</p>
      ) : (
        <div className="pb-body">
          <nav className="pb-rail" aria-label={`${props.title} by phase`}>
            <ul>
              {available.map((e) => (
                <li key={e.key}>
                  <button
                    type="button"
                    className={`pb-row is-${e.state}${e.key === selectedKey ? " is-selected" : ""}`}
                    aria-current={e.key === selectedKey ? "true" : undefined}
                    onClick={() => props.onSelect(e.key)}
                  >
                    <span className="pb-no">{e.number || ""}</span>
                    <span className="pb-row-main">
                      <span className="pb-row-name">{e.name}</span>
                      <span className="pb-row-sub">{e.statusText}</span>
                    </span>
                    {e.meta ? <span className="pb-row-meta">{e.meta}</span> : null}
                  </button>
                </li>
              ))}
            </ul>
            {missing.length ? (
              <p className="pb-missing ct-muted">
                {missing.length} {missing.length === 1 ? "phase" : "phases"} {props.missingNote}:{" "}
                {missing.map((m) => m.name).join(", ")}.
              </p>
            ) : null}
          </nav>

          <section className="pb-detail" aria-label={selected ? selected.name : props.title}>
            {props.notice ? <p className="pb-notice">{props.notice}</p> : null}
            {selected ? (
              <>
                <header className="pb-detail-head">
                  <div>
                    <h3>{selected.name}</h3>
                    <p className="pb-detail-sub">
                      <span className={`pb-state is-${selected.state}`}>{selected.statusText}</span>
                      {props.detailMeta ? <span className="ct-muted">{props.detailMeta}</span> : null}
                    </p>
                  </div>
                  {props.detailActions ? <div className="pb-detail-actions">{props.detailActions}</div> : null}
                </header>
                {props.tabs && props.tabs.length > 1 ? (
                  <div className="pb-tabs" role="tablist" aria-label={`${selected.name} contents`}>
                    {props.tabs.map((t) => (
                      <button
                        key={t.key}
                        type="button"
                        role="tab"
                        aria-selected={t.key === props.activeTab}
                        className={t.key === props.activeTab ? "is-on" : undefined}
                        onClick={() => props.onTab?.(t.key)}
                      >
                        {t.label}
                        {t.tag ? <span className="pb-tab-tag">{t.tag}</span> : null}
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className="pb-content">{props.children}</div>
              </>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}
