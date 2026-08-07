import PlaygroundBlocks from "./PlaygroundBlocks";
import type { Playground } from "../api";

type Props = {
  playground: Playground | null;
  loading?: boolean;
  /** Sidebar/drawer layout — single-column cards, compact header */
  variant?: "page" | "sidebar";
  onClose?: () => void;
};

export default function RolePlayground({
  playground,
  loading,
  variant = "page",
  onClose,
}: Props) {
  if (loading || !playground) {
    return (
      <div className={`playground${variant === "sidebar" ? " is-sidebar" : ""}`}>
        <div className="playground-loading">Loading team memory…</div>
      </div>
    );
  }

  return (
    <div className={`playground${variant === "sidebar" ? " is-sidebar" : ""}`}>
      <header className="playground-hero">
        <div className="playground-hero-row">
          <div>
            <h2>Team shared memory</h2>
          </div>
          {onClose ? (
            <button type="button" className="btn btn-ghost playground-close" onClick={onClose}>
              Close
            </button>
          ) : null}
        </div>
      </header>

      <div className="playground-grid">
        {playground.sections.map((sec) => (
          <article
            key={sec.title}
            className={`playground-card status-${sec.status || "waiting"}`}
          >
            <div className="playground-card-head">
              <h3>{sec.title}</h3>
              <span className="playground-source">
                {sec.source_label}
                {sec.status === "ready"
                  ? " · ready"
                  : sec.status === "planned"
                    ? " · later"
                    : " · waiting"}
              </span>
            </div>
            {sec.data != null ? (
              <div className="playground-body">
                <PlaygroundBlocks value={sec.data} />
              </div>
            ) : (
              <p className="playground-empty">{sec.empty_hint}</p>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
