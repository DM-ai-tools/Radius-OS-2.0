import React from "react";
import BenchmarkReportPanel from "./BenchmarkReportPanel";
import ServiceCompetitorsPanel from "./ServiceCompetitorsPanel";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onConfirm: (selection: Record<string, unknown>) => void | Promise<void>;
  /** Present enables the per-service competitor and benchmark panels. */
  token?: string | null;
  clientId?: string | null;
};

function asCatalog(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function isOpportunity(svc: Record<string, unknown>): boolean {
  const source = String(svc.source || "").toLowerCase();
  return svc.candidate_new_service === true || source === "competitor";
}

type SubItem = { id: string; name: string };
type ScopeEntry = { name: string; offered: SubItem[]; excluded: SubItem[] };
type ScopeMap = Record<string, ScopeEntry>;

function subItems(raw: unknown): SubItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((x): SubItem => {
      if (typeof x === "string") return { id: x.trim().toLowerCase(), name: x.trim() };
      if (x && typeof x === "object") {
        const o = x as Record<string, unknown>;
        return { id: String(o.id || o.name || ""), name: String(o.name || o.id || "") };
      }
      return { id: "", name: "" };
    })
    .filter((x) => x.id && x.name);
}

function asScopeMap(raw: unknown): ScopeMap {
  const out: ScopeMap = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!value || typeof value !== "object") continue;
    const v = value as Record<string, unknown>;
    out[key] = {
      name: String(v.name || key),
      offered: subItems(v.offered),
      excluded: subItems(v.excluded),
    };
  }
  return out;
}

// Mirrors the backend default: sub-services seen only on competitor sites are
// not something the client offers until the operator says so.
function defaultScopeFor(svc: Record<string, unknown>): ScopeEntry {
  const subs = asCatalog(svc.subservices).map((s) => ({
    id: String(s.id),
    name: String(s.name || s.id),
    // Suggestions (competitor sites, the industry) are not offered until the operator says so.
    competitor: ["competitor", "industry"].includes(String(s.source || "").toLowerCase()),
  }));
  return {
    name: String(svc.name || svc.id),
    offered: subs.filter((s) => !s.competitor).map(({ id, name }) => ({ id, name })),
    excluded: subs.filter((s) => s.competitor).map(({ id, name }) => ({ id, name })),
  };
}

export default function ServicePrioritizationCard({
  payload,
  canAct,
  onConfirm,
  token,
  clientId,
}: Props) {
  const initialCatalog = asCatalog(payload.service_catalog);
  const initialClientServices = initialCatalog.filter((svc) => !isOpportunity(svc));
  const defaults =
    payload.defaults && typeof payload.defaults === "object"
      ? (payload.defaults as Record<string, unknown>)
      : {};
  const existing =
    payload.existing_selection && typeof payload.existing_selection === "object"
      ? (payload.existing_selection as Record<string, unknown>)
      : {};

  const initialServices = new Set<string>(
    (Array.isArray(existing.selected_service_ids)
      ? existing.selected_service_ids
      : defaults.selected_service_ids ||
        initialClientServices.map((s) => String(s.id))) as string[],
  );
  const initialPrimary = new Set<string>(
    (Array.isArray(existing.primary_service_ids)
      ? existing.primary_service_ids
      : defaults.primary_service_ids || []) as string[],
  );

  const initialTopPriority = String(
    existing.top_priority_service_id || defaults.top_priority_service_id || "",
  );
  const competitorKeywords = asCatalog(payload.competitor_page_keywords);
  // Depth is no longer user-selectable on this card — keep the server default.
  const poolTarget = Number(
    existing.keyword_pool_target || payload.keyword_pool_target || 4500,
  );

  const [catalog, setCatalog] = React.useState(initialCatalog);
  const [manualName, setManualName] = React.useState("");
  const clientServices = catalog.filter((svc) => !isOpportunity(svc));
  const opportunityServices = catalog.filter((svc) => isOpportunity(svc));
  const [selectedServices, setSelectedServices] = React.useState(initialServices);
  const [primaryServices, setPrimaryServices] = React.useState(initialPrimary);
  const [topPriority, setTopPriority] = React.useState(initialTopPriority);
  const [saving, setSaving] = React.useState(false);
  const [confirmError, setConfirmError] = React.useState("");
  // Which service's competitor/benchmark panels are expanded, and a key that
  // changes whenever its selection does so a stale benchmark can flag itself.
  const [openService, setOpenService] = React.useState<string | null>(null);
  const [selectionKey, setSelectionKey] = React.useState("");

  // Which sub-services the client actually offers, per service. Saved with the
  // selection and enforced by keyword research and content generation.
  const [scope, setScope] = React.useState<ScopeMap>(() => ({
    ...asScopeMap(defaults.service_scope),
    ...asScopeMap(existing.service_scope),
  }));
  // Services whose sub-services the operator has looked at. Anything already
  // saved with a scope counts as reviewed; fresh defaults do not.
  const [reviewed, setReviewed] = React.useState<Set<string>>(
    () => new Set(Object.keys(asScopeMap(existing.service_scope))),
  );
  const [openScope, setOpenScope] = React.useState<string | null>(null);
  const [newSub, setNewSub] = React.useState("");
  const [newExcluded, setNewExcluded] = React.useState("");

  const scopeFor = (svc: Record<string, unknown>): ScopeEntry =>
    scope[String(svc.id)] || defaultScopeFor(svc);

  const needsReview = (svc: Record<string, unknown>): boolean =>
    asCatalog(svc.subservices).length > 0 ||
    (asScopeMap(defaults.service_scope)[String(svc.id)]?.excluded.length || 0) > 0;

  const pendingReview = catalog.filter(
    (svc) =>
      selectedServices.has(String(svc.id)) && needsReview(svc) && !reviewed.has(String(svc.id)),
  );

  const writeScope = (svc: Record<string, unknown>, next: ScopeEntry) => {
    const id = String(svc.id);
    setScope((prev) => ({ ...prev, [id]: next }));
    setReviewed((prev) => new Set(prev).add(id));
  };

  const toggleSub = (svc: Record<string, unknown>, sub: SubItem, offer: boolean) => {
    const cur = scopeFor(svc);
    const rest = (list: SubItem[]) => list.filter((x) => x.id !== sub.id);
    writeScope(svc, {
      ...cur,
      offered: offer ? [...rest(cur.offered), sub] : rest(cur.offered),
      excluded: offer ? rest(cur.excluded) : [...rest(cur.excluded), sub],
    });
  };

  const addOfferedSub = (svc: Record<string, unknown>) => {
    const name = newSub.trim().replace(/\s+/g, " ");
    if (!name) return;
    const sub = { id: name.toLowerCase(), name };
    const id = String(svc.id);
    setCatalog((prev) =>
      prev.map((row) =>
        String(row.id) === id && !asCatalog(row.subservices).some((s) => String(s.id) === sub.id)
          ? { ...row, subservices: [...asCatalog(row.subservices), { ...sub, source: "manual" }] }
          : row,
      ),
    );
    const cur = scopeFor(svc);
    writeScope(svc, {
      ...cur,
      offered: [...cur.offered.filter((x) => x.id !== sub.id), sub],
      excluded: cur.excluded.filter((x) => x.id !== sub.id),
    });
    setNewSub("");
  };

  const addExcluded = (svc: Record<string, unknown>) => {
    const name = newExcluded.trim().replace(/\s+/g, " ");
    if (!name) return;
    const sub = { id: name.toLowerCase(), name };
    const cur = scopeFor(svc);
    if (cur.offered.some((x) => x.id === sub.id)) return;
    writeScope(svc, {
      ...cur,
      excluded: [...cur.excluded.filter((x) => x.id !== sub.id), sub],
    });
    setNewExcluded("");
  };

  const removeExcluded = (svc: Record<string, unknown>, subId: string) => {
    const cur = scopeFor(svc);
    writeScope(svc, { ...cur, excluded: cur.excluded.filter((x) => x.id !== subId) });
  };

  const toggleService = (id: string) => {
    setSelectedServices((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        setPrimaryServices((p) => {
          const n = new Set(p);
          n.delete(id);
          return n;
        });
        // A #1 service that is no longer selected must not stay #1.
        setTopPriority((current) => (current === id ? "" : current));
      } else {
        next.add(id);
      }
      return next;
    });
  };

  function addManualService() {
    const name = manualName.trim().replace(/\s+/g, " ");
    if (!name || !canAct) return;
    const id = name.toLowerCase();
    const exists = catalog.some(
      (svc) =>
        String(svc.id).toLowerCase() === id || String(svc.name).trim().toLowerCase() === id,
    );
    if (exists) {
      setSelectedServices((prev) => new Set(prev).add(id));
      setManualName("");
      return;
    }
    setCatalog((prev) => [
      ...prev,
      { id, name, source: "manual", subservices: [], candidate_new_service: false },
    ]);
    setSelectedServices((prev) => new Set(prev).add(id));
    setManualName("");
  }

  const togglePrimary = (id: string) => {
    setPrimaryServices((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  async function handleConfirm() {
    if (!canAct || saving || selectedServices.size === 0) return;
    setSaving(true);
    setConfirmError("");
    try {
      const scopePayload: ScopeMap = {};
      for (const svc of catalog) {
        const id = String(svc.id);
        if (selectedServices.has(id) && (scope[id] || needsReview(svc))) {
          scopePayload[id] = scopeFor(svc);
        }
      }
      const offeredIds = Object.values(scopePayload).flatMap((e) => e.offered.map((o) => o.id));
      const adoptedServiceIds = catalog
        .filter((svc) => {
          const source = String(svc.source || "").toLowerCase();
          const isCandidate =
            svc.candidate_new_service === true ||
            source === "competitor" ||
            source === "adopted";
          return isCandidate && selectedServices.has(String(svc.id));
        })
        .map((svc) => String(svc.id));
      await onConfirm({
        selected_service_ids: Array.from(selectedServices),
        selected_subservice_ids: offeredIds,
        service_scope: scopePayload,
        primary_service_ids: Array.from(primaryServices),
        adopt_competitor_service_ids: adoptedServiceIds,
        service_catalog: catalog,
        competitor_trees: Array.isArray(payload.competitor_trees)
          ? payload.competitor_trees
          : [],
        top_priority_service_id: topPriority || null,
        keyword_pool_target: poolTarget,
        confirmed: true,
      });
    } catch (e) {
      setConfirmError(
        e instanceof Error ? e.message : "Could not start keyword research.",
      );
    } finally {
      setSaving(false);
    }
  }

  function sourceLabel(svc: Record<string, unknown>): string {
    const source = String(svc.source || "").toLowerCase();
    if (source === "website_page") return "Page on your site, not a service yet";
    if (source === "industry") return "Typical for this industry";
    if (source === "competitor" || svc.candidate_new_service === true) {
      const n = Number(svc.competitor_count || 0);
      return n > 1 ? `Offered by ${n} competitors` : "Offered by competitors";
    }
    if (source === "manual") return "Added by you";
    if (source === "adopted") return "Adopted";
    if (source === "cdd") return "From the client brief";
    return "On your site";
  }

  // ---- outline data ------------------------------------------------------------
  // Top to bottom: each primary service is a bullet and its sub-services are nested
  // sub-bullets. The client's own services come first, then a labelled break and the
  // suggestions. Sub-services show only for a service the operator has included.
  const BRANCHES = 6;

  type OutlineSub = {
    key: string;
    id: string;
    name: string;
    offered: boolean;
    suggested: boolean;
    origin: "competitor" | "industry" | "site";
  };

  const orderedServices = [...clientServices, ...opportunityServices];
  const branches = orderedServices.map((svc, index) => {
    const id = String(svc.id);
    const checked = selectedServices.has(id);
    const entry = scopeFor(svc);
    const catalogSubs = asCatalog(svc.subservices);
    const nodes: OutlineSub[] = checked
      ? catalogSubs.map((sub) => {
          const item = { id: String(sub.id), name: String(sub.name || sub.id) };
          const source = String(sub.source || "").toLowerCase();
          const suggested = ["competitor", "industry"].includes(source);
          return {
            key: `${id}::${item.id}`,
            id: item.id,
            name: item.name,
            offered: entry.offered.some((o) => o.id === item.id),
            suggested,
            origin: (suggested ? source : "site") as "competitor" | "industry" | "site",
          };
        })
      : [];
    // Names the operator blocked (or we suggested blocking) are not catalog
    // sub-services, so they are managed in the details panel, not listed here.
    const blockedCount = checked
      ? entry.excluded.filter((x) => !catalogSubs.some((s) => String(s.id) === x.id)).length
      : 0;
    return { svc, id, index, checked, opportunity: isOpportunity(svc), nodes, blockedCount };
  });

  const offeredTotal = Object.entries(scope)
    .filter(([id]) => selectedServices.has(id))
    .reduce((sum, [, e]) => sum + e.offered.length, 0);
  const primaryCount = Array.from(primaryServices).filter((id) => selectedServices.has(id)).length;
  const clientLabel =
    String(payload.client || "").trim() ||
    String(payload.title || "").split(":").slice(1).join(":").trim() ||
    "Your services";
  const focused = branches.find((b) => b.id === openScope && b.checked) || null;
  const [addingService, setAddingService] = React.useState(false);
  function renderService(b: (typeof branches)[number]) {
    const { svc, id, checked, opportunity } = b;
    const primary = primaryServices.has(id);
    const isTop = topPriority === id;
    const pending = checked && needsReview(svc) && !reviewed.has(id);
    const offeredCount = b.nodes.filter((n) => n.offered).length;
    return (
      <li
        key={id}
        className={`ol-svc mm-b${b.index % BRANCHES}${checked ? " is-on" : ""}${
          opportunity ? " is-ghost" : ""
        }${openScope === id ? " is-focus" : ""}`}
      >
        <div className="ol-row">
          <button
            type="button"
            className="ol-main"
            aria-pressed={checked}
            disabled={!canAct}
            onClick={() => toggleService(id)}
          >
            <span className="ol-bullet" aria-hidden="true">
              <svg viewBox="0 0 16 16" width="11" height="11">
                <path d="M3.6 8.4l3 3 5.8-6.4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <span className="ol-text">
              <span className="ol-name">{String(svc.name)}</span>
              <span className="ol-tag" title={sourceLabel(svc)}>
                {sourceLabel(svc)}
              </span>
            </span>
          </button>
          {checked && b.nodes.length ? (
            <span className="ol-count">
              {offeredCount} of {b.nodes.length} offered
            </span>
          ) : null}
          {pending ? (
            <button type="button" className="ol-flag" onClick={() => setOpenScope(id)}>
              Review
            </button>
          ) : null}
          {checked ? (
            <span className="ol-tools">
              <button
                type="button"
                className={`mm-tool${primary ? " is-on" : ""}`}
                aria-pressed={primary}
                aria-label={`${primary ? "Remove" : "Mark"} ${String(svc.name)} as a primary focus area`}
                title="Primary focus area"
                disabled={!canAct}
                onClick={() => togglePrimary(id)}
              >
                <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                  <path
                    d="M8 1.6l1.9 4 4.4.5-3.3 3 .9 4.3L8 11.2 4.1 13.4 5 9.1 1.7 6.1l4.4-.5z"
                    fill={primary ? "currentColor" : "none"}
                    stroke="currentColor"
                    strokeWidth="1.3"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
              <button
                type="button"
                className={`mm-tool${isTop ? " is-on" : ""}`}
                aria-pressed={isTop}
                aria-label={`Make ${String(svc.name)} the number one focus`}
                title="#1 focus"
                disabled={!canAct}
                onClick={() => setTopPriority(isTop ? "" : id)}
              >
                1
              </button>
              <button
                type="button"
                className={`mm-tool${openScope === id ? " is-on" : ""}`}
                aria-expanded={openScope === id}
                aria-label={`Details for ${String(svc.name)}`}
                title="Details"
                onClick={() => setOpenScope(openScope === id ? null : id)}
              >
                <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                  <circle cx="3.5" cy="8" r="1.3" fill="currentColor" />
                  <circle cx="8" cy="8" r="1.3" fill="currentColor" />
                  <circle cx="12.5" cy="8" r="1.3" fill="currentColor" />
                </svg>
              </button>
            </span>
          ) : null}
        </div>

        {b.nodes.length ? (
          <ul className="ol-subs" aria-label={`Sub-services of ${String(svc.name)}`}>
            {b.nodes.map((n) => (
              <li key={n.key} className={`ol-sub${n.offered ? " is-on" : ""}`}>
                <button
                  type="button"
                  className="ol-sub-btn"
                  aria-pressed={n.offered}
                  aria-label={`${n.name}, ${n.offered ? "offered" : "not offered"}`}
                  title={
                    n.suggested && !n.offered
                      ? n.origin === "industry"
                        ? `${n.name} — typical for this industry, not found on your site`
                        : `${n.name} — seen on competitor sites only`
                      : n.name
                  }
                  disabled={!canAct}
                  onClick={() => toggleSub(b.svc, { id: n.id, name: n.name }, !n.offered)}
                >
                  <span className="ol-check" aria-hidden="true">
                    <svg viewBox="0 0 16 16" width="9" height="9">
                      <path d="M3.6 8.4l3 3 5.8-6.4" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                  <span className="ol-sub-name">{n.name}</span>
                  {!n.offered ? <span className="ol-sub-src">{n.suggested ? "Suggested" : "Not offered"}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </li>
    );
  }

  const firstSuggestion = branches.findIndex((b) => b.opportunity);
  const showSuggestionBreak = firstSuggestion > 0;

  return (
    <div className="structured-card structured-card--report sp-card">
      <header className="sp-intro">
        <h3 className="card-title">{String(payload.title || "Service prioritization")}</h3>
        <p className="sp-lede">
          {String(payload.principle || "Select services to target before keyword expansion.")}
        </p>
      </header>

      <section className="sp-section">
        <div className="sp-section-head">
          <h4>Services and sub-services</h4>
          <span className="sp-count">{orderedServices.length}</span>
        </div>
        <p className="sp-note">
          Click a service to include it in keyword research, then click its sub-services to
          switch on the ones you actually provide. Star a service for primary focus and pick
          one #1. Items marked suggested are ideas you have not adopted.
        </p>

        {(() => {
          const status = String(payload.industry_taxonomy_status || "");
          const reason =
            status === "no_industry"
              ? "No industry is set for this client, so its primary services could not be suggested. Set the client's industry, then run this step again."
              : status === "timeout"
                ? "Looking up this industry's primary services took too long, so this is the list found on the site. Run this step again to retry."
                : status === "failed"
                  ? "The primary services for this industry could not be loaded, so this is the list found on the site. Run this step again to retry."
                  : "";
          return reason ? (
            <p className="dp-notice dp-notice--warn" role="status">
              {reason}
            </p>
          ) : null;
        })()}

        <div className="ol-wrap">
          <div className="ol-root">
            <span className="ol-root-name">{clientLabel}</span>
            <span className="ol-root-meta">
              {selectedServices.size} {selectedServices.size === 1 ? "service" : "services"}
            </span>
            <span className="ol-root-meta">{offeredTotal} sub-services</span>
          </div>

          <ul className="ol-list">
            {branches.map((b, i) => (
              <React.Fragment key={b.id}>
                {showSuggestionBreak && i === firstSuggestion ? (
                  <li className="ol-group" aria-hidden="true">
                    <span>Suggested for this industry</span>
                  </li>
                ) : null}
                {renderService(b)}
              </React.Fragment>
            ))}
          </ul>

          {canAct ? (
            <div className="ol-add">
              {addingService ? (
                <form
                  className="sp-inputgroup"
                  onSubmit={(event) => {
                    event.preventDefault();
                    addManualService();
                    setAddingService(false);
                  }}
                >
                  <input
                    type="text"
                    autoFocus
                    value={manualName}
                    onChange={(event) => setManualName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") setAddingService(false);
                    }}
                    placeholder="Service name"
                    aria-label="Add a service"
                  />
                  <button type="submit" className="btn btn-ghost" disabled={!manualName.trim()}>
                    Add
                  </button>
                </form>
              ) : (
                <button type="button" className="mm-add-btn" onClick={() => setAddingService(true)}>
                  <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                    <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                  </svg>
                  Add a service
                </button>
              )}
            </div>
          ) : null}
        </div>

        {Array.isArray(payload.unplaced_offerings) && payload.unplaced_offerings.length ? (
          <p className="sp-note sp-note--foot">
            Found for this client but not under any primary service, so left off the map:{" "}
            {(payload.unplaced_offerings as unknown[]).map((x) => String(x)).join(", ")}. Use
            "Add a service" or a service's details to add one.
          </p>
        ) : null}

        {(() => {
          const excluded = Array.isArray(payload.excluded_offerings)
            ? (payload.excluded_offerings as Array<Record<string, unknown>>)
            : [];
          if (!excluded.length) return null;
          const why: Record<string, string> = {
            content: "articles",
            unrelated: "pages unrelated to these services",
            location: "locations",
            modifier: "marketing phrases",
            segment: "customer types",
            language: "languages",
          };
          const groups = new Map<string, string[]>();
          for (const item of excluded) {
            const label = why[String(item.reason)] || "not services";
            groups.set(label, [...(groups.get(label) || []), String(item.name)]);
          }
          return (
            <p className="sp-note sp-note--foot">
              Not listed as services:{" "}
              {Array.from(groups.entries())
                .map(([label, names]) => `${names.join(", ")} (${label})`)
                .join("; ")}
              .
            </p>
          );
        })()}

        {focused ? (
          <section className="mm-dock" aria-label={`Details for ${String(focused.svc.name)}`}>
            <header className="mm-dock-head">
              <h5>{String(focused.svc.name)}</h5>
              <span className="sp-tag">{sourceLabel(focused.svc)}</span>
              <button type="button" className="btn btn-ghost sp-summary-btn" onClick={() => setOpenScope(null)}>
                Close
              </button>
            </header>
            {(() => {
              const entry = scopeFor(focused.svc);
              const catalogIds = new Set(asCatalog(focused.svc.subservices).map((s) => String(s.id)));
              const blockedOnly = entry.excluded.filter((x) => !catalogIds.has(x.id));
              const isReviewed = reviewed.has(focused.id);
              return (
                <div className="mm-dock-grid">
                  <div className="mm-dock-col">
                    <p className="sp-note">
                      {entry.offered.length} offered
                      {entry.excluded.length ? `, ${entry.excluded.length} not offered` : ""}.
                      Keyword research and content will only use the offered sub-services.
                    </p>
                    <h6 className="sp-panel-title">Never mention</h6>
                    {blockedOnly.length ? (
                      <ul className="sp-chips mm-dock-chips">
                        {blockedOnly.map((x) => (
                          <li key={x.id} className="sp-chip">
                            {x.name}
                            {canAct ? (
                              <button
                                type="button"
                                className="sp-chip-x"
                                aria-label={`Allow ${x.name}`}
                                onClick={() => removeExcluded(focused.svc, x.id)}
                              >
                                <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
                                  <path d="M3 3l6 6M9 3L3 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                                </svg>
                              </button>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="sp-empty">Nothing blocked beyond the sub-services switched off.</p>
                    )}
                    {canAct ? (
                      <div className="mm-dock-forms">
                        <div className="sp-inputgroup">
                          <input
                            type="text"
                            value={newSub}
                            onChange={(event) => setNewSub(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                addOfferedSub(focused.svc);
                              }
                            }}
                            placeholder="Add a sub-service you offer"
                            aria-label="Add a sub-service you offer"
                          />
                          <button type="button" className="btn btn-ghost" disabled={!newSub.trim()} onClick={() => addOfferedSub(focused.svc)}>
                            Add
                          </button>
                        </div>
                        <div className="sp-inputgroup">
                          <input
                            type="text"
                            value={newExcluded}
                            onChange={(event) => setNewExcluded(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                addExcluded(focused.svc);
                              }
                            }}
                            placeholder="Never mention, for example TikTok Ads"
                            aria-label="Never mention"
                          />
                          <button type="button" className="btn btn-ghost" disabled={!newExcluded.trim()} onClick={() => addExcluded(focused.svc)}>
                            Block
                          </button>
                        </div>
                        <button
                          type="button"
                          className="btn btn-primary"
                          onClick={() => {
                            writeScope(focused.svc, entry);
                            setOpenScope(null);
                          }}
                        >
                          {isReviewed ? "Done" : "Confirm sub-services"}
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {token && clientId ? (
                    <div className="mm-dock-col">
                      <button
                        type="button"
                        className="btn btn-ghost sp-compete-toggle"
                        onClick={() => setOpenService(openService === focused.id ? null : focused.id)}
                        aria-expanded={openService === focused.id}
                      >
                        {openService === focused.id ? "Hide competitors and benchmark" : "Competitors and benchmark"}
                      </button>
                      {openService === focused.id ? (
                        <div className="sp-compete-body">
                          <ServiceCompetitorsPanel
                            token={token}
                            clientId={clientId}
                            serviceId={focused.id}
                            serviceName={String(focused.svc.name || focused.id)}
                            canAct={canAct}
                            onSelectionChange={(ids) => setSelectionKey(`${focused.id}:${ids.join(",")}`)}
                          />
                          <BenchmarkReportPanel
                            token={token}
                            clientId={clientId}
                            serviceId={focused.id}
                            serviceName={String(focused.svc.name || focused.id)}
                            canAct={canAct}
                            selectionKey={selectionKey}
                          />
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            })()}
          </section>
        ) : null}
      </section>

      {competitorKeywords.length ? (
        <section className="sp-section">
          <div className="sp-section-head">
            <h4>What competitors target on their pages</h4>
          </div>
          <p className="sp-note">
            Taken from competitor page titles and headings and checked for volume. These join
            keyword clustering after you confirm.
          </p>
          <ul className="sp-chips sp-chips--plain">
            {competitorKeywords.slice(0, 15).map((row) => (
              <li
                key={String(row.keyword)}
                className="sp-chip"
                title={`${String(row.competitor_domain || "competitor")}, ${String(
                  row.evidence_field || "page",
                )}`}
              >
                {String(row.keyword)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {Array.isArray(payload.competitors) && payload.competitors.length ? (
        <p className="sp-note sp-note--foot">
          Services from all {payload.competitors.length} Phase 4 competitors are included above,
          including offerings the client does not have a page for yet.
        </p>
      ) : null}

      {canAct ? (
        <footer className="sp-footer">
          <div className="sp-summary">
            <strong>{selectedServices.size}</strong> selected
            <span className="sp-summary-sep" aria-hidden="true" />
            <strong>{primaryCount}</strong> primary
            {pendingReview.length ? (
              <>
                <span className="sp-summary-sep" aria-hidden="true" />
                <span className="sp-summary-warn">
                  {pendingReview.length} to review:{" "}
                  {pendingReview.map((s) => String(s.name)).join(", ")}
                </span>
                <button
                  type="button"
                  className="btn btn-ghost sp-summary-btn"
                  onClick={() =>
                    setReviewed((prev) => {
                      const next = new Set(prev);
                      pendingReview.forEach((s) => next.add(String(s.id)));
                      return next;
                    })
                  }
                >
                  Accept defaults for all
                </button>
              </>
            ) : null}
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={saving || selectedServices.size === 0}
            onClick={() => void handleConfirm()}
          >
            {saving ? "Saving…" : "Confirm selection and run keyword research"}
          </button>
          {confirmError ? <p className="sp-note">{confirmError}</p> : null}
        </footer>
      ) : (
        <p className="sp-note">
          Confirming requires Search Demand approve access (Head of Department or Content SEO
          Specialist). If you already have that role, refresh or sign in again so permissions
          reload.
        </p>
      )}
    </div>
  );
}
