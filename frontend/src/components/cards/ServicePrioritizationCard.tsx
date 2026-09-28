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
  // Which service's competitor/benchmark panels are expanded, and a key that
  // changes whenever its selection does so a stale benchmark can flag itself.
  const [openService, setOpenService] = React.useState<string | null>(null);
  const [selectionKey, setSelectionKey] = React.useState("");

  // Sub-services are no longer selectable here — this section lists top-level
  // services only. Everything under a selected service comes along with it.
  const subserviceIdsFor = React.useCallback(
    (serviceIds: Set<string>) =>
      catalog
        .filter((svc) => serviceIds.has(String(svc.id)))
        .flatMap((svc) => asCatalog(svc.subservices).map((sub) => String(sub.id)))
        .filter(Boolean),
    [catalog],
  );

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
    if (!canAct || saving) return;
    setSaving(true);
    try {
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
        selected_subservice_ids: subserviceIdsFor(selectedServices),
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
    } finally {
      setSaving(false);
    }
  }

  function renderServiceRow(svc: Record<string, unknown>, opportunity: boolean) {
    const id = String(svc.id);
    const subs = asCatalog(svc.subservices);
    const checked = selectedServices.has(id);
    const primary = primaryServices.has(id);
    const source = String(svc.source || "").toLowerCase();
    return (
      <div
        key={id}
        style={{
          border: "1px solid var(--line)",
          borderStyle: opportunity ? "dashed" : "solid",
          borderRadius: 8,
          padding: "10px 12px",
          marginBottom: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 600 }}>
            <input
              type="checkbox"
              checked={checked}
              disabled={!canAct}
              onChange={() => toggleService(id)}
            />
            {String(svc.name)}
          </label>
          {opportunity ? (
            <span
              style={{
                fontSize: 10,
                padding: "2px 7px",
                borderRadius: 999,
                color: "var(--warning, #9a6700)",
                background: "color-mix(in srgb, var(--warning, #d29922) 14%, transparent)",
              }}
            >
              Suggested — not currently on your site
              {Number(svc.competitor_count || 0) > 1
                ? ` · seen across ${Number(svc.competitor_count)} competitors`
                : ""}
            </span>
          ) : null}
          <button
            type="button"
            className="cc-btn cc-btn--ghost"
            style={{
              fontSize: 11,
              padding: "2px 8px",
              opacity: checked ? 1 : 0.4,
            }}
            disabled={!canAct || !checked}
            onClick={() => togglePrimary(id)}
          >
            {primary ? "★ Primary" : "☆ Mark primary"}
          </button>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              fontSize: 11,
              color: "var(--muted)",
              opacity: checked ? 1 : 0.4,
            }}
          >
            <input
              type="radio"
              name="top-priority-service"
              checked={topPriority === id}
              disabled={!canAct || !checked}
              onChange={() => setTopPriority(id)}
            />
            #1 focus
          </label>
          {!opportunity ? (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              {source === "manual"
                ? "added manually"
                : source === "website"
                  ? "on your site"
                  : source === "adopted"
                    ? "adopted"
                    : source || "on your site"}
            </span>
          ) : null}
        </div>
        {subs.length ? (
          <p
            style={{
              margin: "6px 0 0 24px",
              fontSize: 11,
              color: "var(--muted)",
            }}
          >
            includes {subs.length} sub-service{subs.length === 1 ? "" : "s"}
          </p>
        ) : null}

        {/* Competitor selection and benchmarking need a token + client id; when
            this card is rendered without them the rest of it still works. */}
        {token && clientId ? (
          <div style={{ marginTop: 8, marginLeft: 24 }}>
            <button
              type="button"
              className="cc-btn cc-btn--ghost"
              style={{ fontSize: 11 }}
              onClick={() => setOpenService(openService === id ? null : id)}
              aria-expanded={openService === id}
            >
              {openService === id ? "Hide" : "Competitors & benchmark"}
            </button>
            {openService === id ? (
              <div style={{ marginTop: 8 }}>
                <ServiceCompetitorsPanel
                  token={token}
                  clientId={clientId}
                  serviceId={id}
                  serviceName={String(svc.name || id)}
                  canAct={canAct}
                  onSelectionChange={(ids) => setSelectionKey(`${id}:${ids.join(",")}`)}
                />
                <BenchmarkReportPanel
                  token={token}
                  clientId={clientId}
                  serviceId={id}
                  serviceName={String(svc.name || id)}
                  canAct={canAct}
                  selectionKey={selectionKey}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Service prioritization")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {String(payload.principle || "Select services to target before keyword expansion.")}
      </p>

      <h4 style={{ marginBottom: 8 }}>Industry services (80/20)</h4>
      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
        Every service found for this industry: pages on the client's website, plus
        services competitors offer that are not on the site yet. Check which to include
        in Phase 5, star primary focus areas (~20%), and pick one #1 focus. Sub-services
        are included automatically with their parent service.
      </p>
      <div style={{ marginBottom: 16 }}>
        {clientServices.length ? (
          clientServices.map((svc) => renderServiceRow(svc, false))
        ) : (
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            No services on the client's website yet. Industry services from competitors
            are listed below, or add one manually.
          </p>
        )}
        {canAct ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              addManualService();
            }}
            style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}
          >
            <input
              type="text"
              value={manualName}
              onChange={(event) => setManualName(event.target.value)}
              placeholder="Add a service"
              aria-label="Add a service"
              style={{
                flex: "1 1 220px",
                minWidth: 180,
                padding: "8px 10px",
                borderRadius: 8,
                border: "1px solid var(--line)",
                background: "transparent",
                color: "inherit",
              }}
            />
            <button type="submit" className="cc-btn cc-btn--ghost" disabled={!manualName.trim()}>
              Add service
            </button>
          </form>
        ) : null}
      </div>

      {opportunityServices.length ? (
        <div style={{ marginBottom: 16 }}>
          <h4 style={{ marginBottom: 8 }}>Not on the client's site</h4>
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            Other services this industry offers, taken from competitor sites. They stay
            unchecked until you include them.
          </p>
          {opportunityServices.map((svc) => renderServiceRow(svc, true))}
        </div>
      ) : null}

      {competitorKeywords.length ? (
        <div style={{ marginBottom: 16 }}>
          <h4 style={{ marginBottom: 4 }}>What competitors target on their pages</h4>
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            Harvested from competitor page titles and headings, volume-checked. These join
            keyword clustering after you confirm.
          </p>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {competitorKeywords.slice(0, 15).map((row) => (
              <span
                key={String(row.keyword)}
                style={{
                  fontSize: 11,
                  padding: "3px 8px",
                  borderRadius: 999,
                  border: "1px solid var(--line)",
                }}
                title={`${String(row.competitor_domain || "competitor")} · ${String(
                  row.evidence_field || "page",
                )}`}
              >
                {String(row.keyword)}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {Array.isArray(payload.competitors) && payload.competitors.length ? (
        <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 14px" }}>
          Services from all {payload.competitors.length} Phase 4 competitors are included
          above, including offerings the client does not have a page for yet.
        </p>
      ) : null}

      {canAct ? (
        <button
          type="button"
          className="cc-btn cc-btn--primary"
          disabled={saving || selectedServices.size === 0}
          onClick={() => void handleConfirm()}
        >
          {saving ? "Saving…" : "Confirm selection & run keyword research"}
        </button>
      ) : (
        <p style={{ fontSize: 12, color: "var(--muted)" }}>
          Confirm requires Search Demand approve access (Head of Department or Content SEO
          Specialist). If you already have that role, refresh or sign in again so permissions
          reload.
        </p>
      )}
    </div>
  );
}
