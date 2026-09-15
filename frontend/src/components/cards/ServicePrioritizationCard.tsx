import React from "react";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onConfirm: (selection: Record<string, unknown>) => void | Promise<void>;
};

function asCatalog(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

export default function ServicePrioritizationCard({ payload, canAct, onConfirm }: Props) {
  const catalog = asCatalog(payload.service_catalog);
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
      : defaults.selected_service_ids || catalog.map((s) => String(s.id))) as string[],
  );
  const initialSubs = new Set<string>(
    (Array.isArray(existing.selected_subservice_ids)
      ? existing.selected_subservice_ids
      : defaults.selected_subservice_ids || []) as string[],
  );
  const initialPrimary = new Set<string>(
    (Array.isArray(existing.primary_service_ids)
      ? existing.primary_service_ids
      : defaults.primary_service_ids || []) as string[],
  );

  const [selectedServices, setSelectedServices] = React.useState(initialServices);
  const [selectedSubs, setSelectedSubs] = React.useState(initialSubs);
  const [primaryServices, setPrimaryServices] = React.useState(initialPrimary);
  const [saving, setSaving] = React.useState(false);

  const toggleService = (id: string, subs: Array<Record<string, unknown>>) => {
    setSelectedServices((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        setPrimaryServices((p) => {
          const n = new Set(p);
          n.delete(id);
          return n;
        });
        subs.forEach((sub) => {
          const sid = String(sub.id);
          setSelectedSubs((s) => {
            const sn = new Set(s);
            sn.delete(sid);
            return sn;
          });
        });
      } else {
        next.add(id);
        subs.forEach((sub) => {
          setSelectedSubs((s) => new Set(s).add(String(sub.id)));
        });
      }
      return next;
    });
  };

  const toggleSub = (id: string) => {
    setSelectedSubs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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
        selected_subservice_ids: Array.from(selectedSubs),
        primary_service_ids: Array.from(primaryServices),
        adopt_competitor_service_ids: adoptedServiceIds,
        service_catalog: catalog,
        competitor_trees: Array.isArray(payload.competitor_trees)
          ? payload.competitor_trees
          : [],
        confirmed: true,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Service prioritization")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {String(payload.principle || "Select services to target before keyword expansion.")}
      </p>

      <h4 style={{ marginBottom: 8 }}>Your services and opportunities (80/20)</h4>
      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
        Check services to include in Phase 5. Star primary focus areas (~20%).
      </p>
      <div style={{ marginBottom: 16 }}>
        {catalog.map((svc) => {
          const id = String(svc.id);
          const subs = asCatalog(svc.subservices);
          const checked = selectedServices.has(id);
          const primary = primaryServices.has(id);
          const source = String(svc.source || "").toLowerCase();
          const isCandidate = svc.candidate_new_service === true || source === "competitor";
          return (
            <div
              key={id}
              style={{
                border: "1px solid var(--line)",
                borderStyle: isCandidate ? "dashed" : "solid",
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
                    onChange={() => toggleService(id, subs)}
                  />
                  {String(svc.name)}
                </label>
                {isCandidate ? (
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
                {!isCandidate ? (
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>
                    {source === "adopted" ? "adopted opportunity" : String(svc.source || "cdd")}
                  </span>
                ) : null}
              </div>
              {subs.length ? (
                <ul style={{ margin: "8px 0 0 24px", padding: 0, listStyle: "none" }}>
                  {subs.map((sub) => {
                    const sid = String(sub.id);
                    return (
                      <li key={sid} style={{ marginBottom: 4 }}>
                        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
                          <input
                            type="checkbox"
                            checked={selectedSubs.has(sid)}
                            disabled={!canAct || !checked}
                            onChange={() => toggleSub(sid)}
                          />
                          {String(sub.name)}
                          {sub.source === "competitor" ? (
                            <span style={{ fontSize: 10, color: "var(--muted)" }}>
                              (from {String(sub.competitor || "competitor")})
                            </span>
                          ) : null}
                        </label>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>

      {Array.isArray(payload.competitors) && payload.competitors.length ? (
        <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 14px" }}>
          Competitor structure from all {payload.competitors.length} Phase 4 competitors is
          included in the service suggestions.
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
          Confirm requires Search Demand approve access (Head of Department or
          Content SEO Specialist). If you already have that role, refresh or
          sign in again so permissions reload.
        </p>
      )}
    </div>
  );
}
