import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  downloadHtmlFile,
  type SitePreviewDraft,
  type SitePreviewState,
} from "../../api";

const ACTIVE = new Set(["starting", "running", "waiting", "collecting"]);
const POLL_MS = 4000;
const MAX_SHOTS = 4;
const MAX_SHOT_BYTES = 8 * 1024 * 1024;

type Props = {
  token: string;
  clientId: string;
  draft: SitePreviewDraft;
  filename: string;
  canBuild: boolean;
  onClose: () => void;
};

function statusLine(state: SitePreviewState | null): string {
  if (!state) return "Checking for an earlier preview…";
  switch (state.status) {
    case "none":
      return "No preview yet";
    case "starting":
    case "running":
    case "collecting":
      return state.mode === "edit" ? "Applying your changes" : "Building on the client's site design";
    case "waiting":
      return "Answering the agent";
    case "done":
      return state.reviewNotes?.length ? "Ready, with notes to review" : "Ready";
    case "stopped":
      return "Stopped";
    case "failed":
      return "Build failed";
    default:
      return "";
  }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export default function SitePreviewModal({ token, clientId, draft, filename, canBuild, onClose }: Props) {
  const [state, setState] = useState<SitePreviewState | null>(null);
  const [html, setHtml] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [shots, setShots] = useState<Array<{ name: string; data: string }>>([]);
  const [showFeed, setShowFeed] = useState(true);
  const pageKey = useRef<string>("");
  const loadedFor = useRef<number | undefined>(undefined);
  const started = useRef(false);

  const active = Boolean(state && ACTIVE.has(state.status));

  const loadHtml = useCallback(
    async (next: SitePreviewState) => {
      if (!next.hasHtml || loadedFor.current === next.updatedAt) return;
      const full = await api.sitePreviewState(token, clientId, next.pageKey, true);
      loadedFor.current = next.updatedAt;
      setHtml(full.html || "");
    },
    [token, clientId],
  );

  const act = useCallback(
    async (
      action: "build" | "edit" | "stop" | "undo",
      extra?: { instructions?: string; screenshots?: string[] },
    ) => {
      setBusy(true);
      setError(null);
      try {
        const next = await api.sitePreviewAction(token, clientId, { action, draft, ...extra });
        pageKey.current = next.pageKey;
        setState(next);
        if (action === "undo") {
          loadedFor.current = undefined;
          await loadHtml(next);
        }
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "The preview request failed.");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [token, clientId, draft, loadHtml],
  );

  // Opening the preview: show the stored page, or start a build when there is none.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void (async () => {
      try {
        const first = await api.sitePreviewAction(token, clientId, { action: "status", draft });
        pageKey.current = first.pageKey;
        setState(first);
        if (first.hasHtml) await loadHtml(first);
        if (!first.hasHtml && !ACTIVE.has(first.status) && canBuild) {
          await act("build");
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not reach the preview service.");
      }
    })();
  }, [token, clientId, draft, canBuild, act, loadHtml]);

  // Poll while a run is active; fetch the page once it is done.
  useEffect(() => {
    if (!active || !pageKey.current) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.sitePreviewState(token, clientId, pageKey.current);
        setState(next);
        if (!ACTIVE.has(next.status)) await loadHtml(next);
      } catch {
        // A missed poll is retried on the next tick.
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, token, clientId, loadHtml]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function addShots(files: FileList | null) {
    if (!files) return;
    const picked: Array<{ name: string; data: string }> = [];
    for (const file of Array.from(files)) {
      if (shots.length + picked.length >= MAX_SHOTS) break;
      if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type) || file.size > MAX_SHOT_BYTES) {
        setError(`${file.name}: use a PNG, JPEG, WebP or GIF under 8 MB.`);
        continue;
      }
      picked.push({ name: file.name, data: await readAsDataUrl(file) });
    }
    setShots((prev) => [...prev, ...picked]);
  }

  async function applyChanges() {
    const text = instructions.trim();
    if (!text) return;
    const ok = await act("edit", { instructions: text, screenshots: shots.map((s) => s.data) });
    if (ok) {
      setInstructions("");
      setShots([]);
    }
  }

  const progress = Math.max(0, Math.min(100, state?.progress ?? 0));
  const feed = [...(state?.activity || [])].reverse();
  const notes = state?.reviewNotes || [];

  return (
    <div className="site-preview-backdrop" role="presentation" onClick={onClose}>
      <div
        className="site-preview-modal site-preview-modal--agent"
        role="dialog"
        aria-modal="true"
        aria-label="Client site preview"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="site-preview-toolbar">
          <div>
            <strong>Client site preview</strong>
            <span className="site-preview-sub">
              {statusLine(state)}
              {state?.designPageUrl ? (
                <>
                  {" · design from "}
                  <a href={state.designPageUrl} target="_blank" rel="noreferrer" title={state.designPageReason || undefined}>
                    {state.designPageUrl.replace(/^https?:\/\//, "")}
                  </a>
                  {state.designPageReason ? ` (${state.designPageReason})` : ""}
                </>
              ) : null}
            </span>
          </div>
          <div className="site-preview-toolbar-actions">
            {state?.canUndo && !active ? (
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => void act("undo")}>
                Undo last change
              </button>
            ) : null}
            {canBuild && !active ? (
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => void act("build")}>
                {state?.hasHtml ? "Rebuild" : "Build preview"}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!html}
              onClick={() => downloadHtmlFile(html, filename)}
            >
              Download HTML
            </button>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        <div className="sp-body">
          <div className="sp-stage">
            {html ? (
              <iframe className="site-preview-frame" title="Client site preview" sandbox="allow-same-origin" srcDoc={html} />
            ) : (
              <div className="sp-empty">
                {active ? (
                  <p>
                    The agent is studying the client's site and laying out the draft in its design. A build
                    usually takes 10 to 30 minutes; you can close this window and come back.
                  </p>
                ) : state?.status === "failed" || state?.status === "stopped" ? (
                  <p>No page was delivered. {canBuild ? "Rebuild to try again." : ""}</p>
                ) : (
                  <p>Loading…</p>
                )}
              </div>
            )}
          </div>

          <aside className="sp-panel" aria-label="Preview build">
            {active ? (
              <section className="sp-progress" aria-live="polite">
                <div className="sp-progress-head">
                  <span>{state?.stageLabel || "Starting"}</span>
                  <span>{progress}%</span>
                </div>
                <div
                  className="sp-bar"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={progress}
                >
                  <div className="sp-bar-fill" style={{ width: `${progress}%` }} />
                </div>
                {feed[0] ? <p className="sp-latest">{feed[0].text}</p> : null}
                {canBuild ? (
                  <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => void act("stop")}>
                    Stop build
                  </button>
                ) : null}
              </section>
            ) : null}

            {error || state?.error ? (
              <p className="dp-notice dp-notice--error" role="alert">
                {error || state?.error}
              </p>
            ) : null}

            {state?.status === "done" ? (
              <section>
                <h4 className="sp-heading">Review notes</h4>
                {notes.length ? (
                  <ul className="sp-notes">
                    {notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="sp-muted">
                    Every heading, link and sentence of the draft is on the page, and the agent compared it with
                    the live site at desktop and mobile widths.
                  </p>
                )}
                {state.report?.summary ? <p className="sp-muted">{state.report.summary}</p> : null}
                {state.credits != null ? (
                  <p className="sp-muted">Manus credits used: {state.credits}</p>
                ) : null}
              </section>
            ) : null}

            {canBuild && state?.hasHtml && !active ? (
              <section>
                <h4 className="sp-heading">Change the page</h4>
                <textarea
                  className="sp-input"
                  rows={4}
                  placeholder="e.g. Use the site's dark header and make the call button match the home page."
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  maxLength={4000}
                />
                <div className="sp-shots">
                  <label className="btn btn-ghost sp-attach">
                    Attach screenshot
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      multiple
                      hidden
                      disabled={shots.length >= MAX_SHOTS}
                      onChange={(e) => {
                        void addShots(e.target.files);
                        e.target.value = "";
                      }}
                    />
                  </label>
                  {shots.map((shot, i) => (
                    <button
                      key={`${shot.name}-${i}`}
                      type="button"
                      className="sp-shot"
                      title="Remove"
                      onClick={() => setShots((prev) => prev.filter((_, j) => j !== i))}
                    >
                      {shot.name} ×
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy || !instructions.trim()}
                  onClick={() => void applyChanges()}
                >
                  Apply changes
                </button>
                <p className="sp-muted">The draft's words stay as written; only the design changes.</p>
              </section>
            ) : null}

            {state?.questions?.length ? (
              <section>
                <h4 className="sp-heading">Questions the agent asked</h4>
                <ul className="sp-qa">
                  {state.questions.map((q) => (
                    <li key={q.at}>
                      <strong>{q.question}</strong>
                      <span>{q.answer}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {feed.length ? (
              <section>
                <button
                  type="button"
                  className="sp-feed-toggle"
                  aria-expanded={showFeed}
                  onClick={() => setShowFeed((v) => !v)}
                >
                  Agent activity ({feed.length})
                </button>
                {showFeed ? (
                  <ol className="sp-feed">
                    {feed.map((item) => (
                      <li key={item.id} className={`sp-feed-${item.kind}`}>
                        {item.text}
                      </li>
                    ))}
                  </ol>
                ) : null}
              </section>
            ) : null}
          </aside>
        </div>
      </div>
    </div>
  );
}
