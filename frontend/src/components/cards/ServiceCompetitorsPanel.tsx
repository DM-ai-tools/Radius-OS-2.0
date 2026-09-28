import React from "react";
import { api } from "../../api";

/**
 * Competitors attached to one service.
 *
 * Competitors are reusable and client-scoped, so this panel picks from the ones
 * already on the client and can add a new one inline. Adding a domain that
 * already exists reuses that competitor rather than creating a duplicate.
 */

type Competitor = {
  id: string;
  name: string;
  url: string;
  domain: string;
  notes?: string;
  source?: string;
  selected?: boolean;
};

type Props = {
  token: string;
  clientId: string;
  serviceId: string;
  serviceName: string;
  canAct: boolean;
  /** Told when the selection changes, so a benchmark panel can refresh. */
  onSelectionChange?: (ids: string[]) => void;
};

function asCompetitors(raw: unknown): Competitor[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((r) => r && typeof r === "object")
    .map((r) => {
      const row = r as Record<string, unknown>;
      return {
        id: String(row.id || ""),
        name: String(row.name || ""),
        url: String(row.url || ""),
        domain: String(row.domain || ""),
        notes: String(row.notes || ""),
        source: String(row.source || ""),
        selected: row.selected === true,
      };
    })
    .filter((c) => c.id);
}

export default function ServiceCompetitorsPanel({
  token,
  clientId,
  serviceId,
  serviceName,
  canAct,
  onSelectionChange,
}: Props) {
  const [available, setAvailable] = React.useState<Competitor[]>([]);
  const [selected, setSelected] = React.useState<string[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  const [showAdd, setShowAdd] = React.useState(false);
  const [newName, setNewName] = React.useState("");
  const [newSite, setNewSite] = React.useState("");
  const [newNotes, setNewNotes] = React.useState("");
  const [addError, setAddError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.serviceCompetitors(token, clientId, serviceId);
      setAvailable(asCompetitors(data.available));
      setSelected((data.selected_competitor_ids || []).map(String));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load competitors.");
    } finally {
      setLoading(false);
    }
  }, [token, clientId, serviceId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function persist(ids: string[]) {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.saveServiceCompetitors(token, clientId, serviceId, ids);
      const saved = (res.selected_competitor_ids || []).map(String);
      setSelected(saved);
      onSelectionChange?.(saved);
      if (res.warnings?.length) setNotice(res.warnings.join(" "));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the selection.");
      await load();
    } finally {
      setSaving(false);
    }
  }

  function toggle(id: string) {
    if (!canAct) return;
    const next = selected.includes(id)
      ? selected.filter((x) => x !== id)
      : [...selected, id];
    void persist(next);
  }

  async function addInline(event: React.FormEvent) {
    event.preventDefault();
    const name = newName.trim();
    const website = newSite.trim();
    if (!name) {
      setAddError("A name is required.");
      return;
    }
    if (!website) {
      setAddError("A website is required.");
      return;
    }
    setSaving(true);
    setAddError(null);
    try {
      const res = await api.addServiceCompetitor(token, clientId, serviceId, {
        name,
        website,
        notes: newNotes.trim() || undefined,
      });
      setNewName("");
      setNewSite("");
      setNewNotes("");
      setShowAdd(false);
      if (res.warnings?.length) setNotice(res.warnings.join(" "));
      else if (!res.created) setNotice(`${name} already existed — reused that competitor.`);
      await load();
      onSelectionChange?.(selected);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Could not add that competitor.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string, everywhere: boolean) {
    if (!canAct) return;
    const target = available.find((c) => c.id === id);
    if (everywhere) {
      const ok = window.confirm(
        `Delete ${target?.name || "this competitor"} from this client entirely? ` +
          "It will be removed from every service.",
      );
      if (!ok) return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.removeServiceCompetitor(token, clientId, serviceId, id, everywhere);
      await load();
      onSelectionChange?.(selected.filter((x) => x !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove that competitor.");
    } finally {
      setSaving(false);
    }
  }

  const selectedSet = new Set(selected);
  const chosen = selected
    .map((id) => available.find((c) => c.id === id))
    .filter((c): c is Competitor => Boolean(c));
  const rest = available.filter((c) => !selectedSet.has(c.id));

  return (
    <section className="svc-competitors">
      <header className="svc-competitors__head">
        <h4>Competitors for {serviceName}</h4>
        <span className="svc-competitors__count">
          {loading ? "loading…" : `${selected.length} selected`}
          {saving ? " · saving…" : ""}
        </span>
      </header>

      {error ? (
        <p className="svc-competitors__error" role="alert">
          {error}{" "}
          <button type="button" onClick={() => void load()}>
            Retry
          </button>
        </p>
      ) : null}
      {notice ? <p className="svc-competitors__notice">{notice}</p> : null}

      {loading ? (
        <p className="svc-competitors__muted">Loading competitors…</p>
      ) : (
        <>
          {chosen.length === 0 ? (
            <p className="svc-competitors__muted">
              None selected. Content for this service will generate without competitor
              context.
            </p>
          ) : (
            <ul className="svc-competitors__list">
              {chosen.map((c) => (
                <li key={c.id} className="svc-competitors__row is-selected">
                  <label>
                    <input
                      type="checkbox"
                      checked
                      disabled={!canAct || saving}
                      onChange={() => toggle(c.id)}
                    />
                    <span className="svc-competitors__name">{c.name}</span>
                    <span className="svc-competitors__domain">{c.domain}</span>
                  </label>
                  {c.notes ? (
                    <span className="svc-competitors__notes">{c.notes}</span>
                  ) : null}
                  <span className="svc-competitors__actions">
                    <button
                      type="button"
                      disabled={!canAct || saving}
                      onClick={() => void remove(c.id, false)}
                      title="Remove from this service only"
                    >
                      Detach
                    </button>
                    <button
                      type="button"
                      className="is-danger"
                      disabled={!canAct || saving}
                      onClick={() => void remove(c.id, true)}
                      title="Delete from the client entirely"
                    >
                      Delete
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}

          {rest.length > 0 ? (
            <details className="svc-competitors__pick">
              <summary>Pick from existing competitors ({rest.length})</summary>
              <ul className="svc-competitors__list">
                {rest.map((c) => (
                  <li key={c.id} className="svc-competitors__row">
                    <label>
                      <input
                        type="checkbox"
                        checked={false}
                        disabled={!canAct || saving}
                        onChange={() => toggle(c.id)}
                      />
                      <span className="svc-competitors__name">{c.name}</span>
                      <span className="svc-competitors__domain">{c.domain}</span>
                      {c.source && c.source !== "manual" ? (
                        <span className="svc-competitors__badge">AI-detected</span>
                      ) : null}
                    </label>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          {canAct ? (
            showAdd ? (
              <form className="svc-competitors__add" onSubmit={addInline}>
                <input
                  placeholder="Competitor name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  aria-label="Competitor name"
                />
                <input
                  placeholder="example.com"
                  value={newSite}
                  onChange={(e) => setNewSite(e.target.value)}
                  aria-label="Competitor website"
                />
                <input
                  placeholder="Notes (optional)"
                  value={newNotes}
                  onChange={(e) => setNewNotes(e.target.value)}
                  aria-label="Notes"
                />
                {addError ? (
                  <p className="svc-competitors__error" role="alert">
                    {addError}
                  </p>
                ) : null}
                <div className="svc-competitors__add-actions">
                  <button type="submit" disabled={saving}>
                    {saving ? "Adding…" : "Add competitor"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowAdd(false);
                      setAddError(null);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                type="button"
                className="svc-competitors__add-toggle"
                onClick={() => setShowAdd(true)}
              >
                + Add a competitor
              </button>
            )
          ) : null}
        </>
      )}
    </section>
  );
}
