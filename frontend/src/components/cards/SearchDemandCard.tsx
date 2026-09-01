import WorkbookTable from "./WorkbookTable";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string) => void;
};

function kwRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<
    Record<string, unknown>
  >;
}

function fmtVol(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString();
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

/** TOFU/MOFU/BOFU tag next to a keyword — awareness / consideration / decision. */
function FunnelBadge({ stage }: { stage: unknown }) {
  const s = String(stage || "")
    .trim()
    .toUpperCase();
  const cls = s === "TOFU" || s === "MOFU" || s === "BOFU" ? s.toLowerCase() : "unknown";
  return <span className={`funnel-badge ${cls}`}>{s || "—"}</span>;
}

function serpTitles(
  row: Record<string, unknown>,
): Array<Record<string, unknown>> {
  return kwRows(row.serp_titles);
}

/** Service → Seed 1, Seed 2 … → the seed's exact/phrase/related/broad keywords. */
function ServiceSeedGroups({
  services,
}: {
  services: Array<Record<string, unknown>>;
}) {
  return (
    <>
      {services.map((service, si) => {
        const seeds = kwRows(service.seeds);
        return (
          <details
            key={`${String(service.service)}-${si}`}
            style={{
              marginBottom: 8,
              border: "1px solid var(--line)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            <summary
              style={{
                cursor: "pointer",
                padding: "10px 12px",
                fontSize: 13,
                fontWeight: 700,
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  color: "var(--muted)",
                  marginRight: 6,
                }}
              >
                Service
              </span>
              {String(service.service || "Other website topics")}{" "}
              <span style={{ fontWeight: 500, color: "var(--muted)" }}>
                ({Number(service.seed_count ?? seeds.length)} seeds ·{" "}
                {Number(service.keyword_count ?? 0)} keywords
                {service.total_volume != null
                  ? ` · vol ${fmtVol(service.total_volume)}`
                  : ""}
                )
              </span>
            </summary>
            <div style={{ padding: "0 12px 10px" }}>
              {seeds.map((seed, sj) => {
                const keywords = kwRows(seed.keywords);
                const counts =
                  seed.class_counts && typeof seed.class_counts === "object"
                    ? (seed.class_counts as Record<string, number>)
                    : {};
                const missing = Array.isArray(seed.classes_missing)
                  ? (seed.classes_missing as string[])
                  : [];
                return (
                  <details
                    key={`${String(seed.seed)}-${sj}`}
                    style={{ marginTop: 8 }}
                  >
                    <summary
                      style={{
                        cursor: "pointer",
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >
                      {String(seed.seed_label || `Seed ${sj + 1}`)}:{" "}
                      {String(seed.seed || "—")}{" "}
                      <span style={{ fontWeight: 500, color: "var(--muted)" }}>
                        ({Number(seed.keyword_count ?? keywords.length)}{" "}
                        keywords)
                      </span>
                      <span
                        style={{
                          display: "block",
                          marginTop: 3,
                          marginLeft: 16,
                          fontSize: 11,
                          fontWeight: 500,
                          color: "var(--muted)",
                        }}
                      >
                        Exact {Number(counts.exact ?? 0)} · Phrase{" "}
                        {Number(counts.phrase ?? 0)} · Related{" "}
                        {Number(counts.related ?? 0)} · Broad{" "}
                        {Number(counts.broad ?? 0)}
                        {missing.length
                          ? ` · no results for: ${missing.join(", ")}`
                          : ""}
                      </span>
                    </summary>
                    <div style={{ overflowX: "auto", paddingBottom: 8 }}>
                      <table
                        className="kw-report-table"
                        style={{
                          width: "100%",
                          fontSize: 11,
                          borderCollapse: "collapse",
                        }}
                      >
                        <thead>
                          <tr
                            style={{
                              textAlign: "left",
                              borderBottom: "1px solid var(--line)",
                            }}
                          >
                            <th style={{ padding: "3px 6px" }}>Class</th>
                            <th style={{ padding: "3px 6px" }}>Keyword</th>
                            <th style={{ padding: "3px 6px" }}>Intent</th>
                            <th style={{ padding: "3px 6px" }}>Funnel</th>
                            <th style={{ padding: "3px 6px" }}>Volume</th>
                            <th style={{ padding: "3px 6px" }}>KD</th>
                          </tr>
                        </thead>
                        <tbody>
                          {keywords.map((k, kj) => (
                            <tr
                              key={`${String(k.keyword)}-${kj}`}
                              style={{ borderBottom: "1px solid var(--line)" }}
                            >
                              <td
                                style={{
                                  padding: "3px 6px",
                                  textTransform: "capitalize",
                                }}
                              >
                                {String(k.match_class || "—")}
                              </td>
                              <td
                                style={{ padding: "3px 6px", fontWeight: 600 }}
                              >
                                {String(k.keyword || "—")}
                              </td>
                              <td
                                style={{
                                  padding: "3px 6px",
                                  textTransform: "capitalize",
                                }}
                              >
                                {String(k.intent || "—")}
                              </td>
                              <td style={{ padding: "3px 6px" }}>
                                <FunnelBadge stage={k.funnel} />
                              </td>
                              <td style={{ padding: "3px 6px" }}>
                                {fmtVol(k.volume)}
                              </td>
                              <td style={{ padding: "3px 6px" }}>
                                {fmtNum(k.difficulty)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {keywords.length === 0 ? (
                        <p style={{ fontSize: 11, color: "var(--muted)" }}>
                          No keywords above the volume threshold were returned
                          for this seed.
                        </p>
                      ) : null}
                    </div>
                  </details>
                );
              })}
            </div>
          </details>
        );
      })}
    </>
  );
}

function KeywordDetailTable({
  rows,
  empty,
}: {
  rows: Array<Record<string, unknown>>;
  empty: string;
}) {
  if (!rows.length) {
    return <p style={{ fontSize: 13, color: "var(--muted)" }}>{empty}</p>;
  }
  return (
    <div style={{ overflowX: "auto", marginBottom: 14 }}>
      <table
        className="kw-report-table"
        style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
      >
        <thead>
          <tr
            style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}
          >
            <th style={{ padding: "6px 8px" }}>Keyword</th>
            <th style={{ padding: "6px 8px" }}>Volume</th>
            <th style={{ padding: "6px 8px" }}>KD</th>
            <th style={{ padding: "6px 8px" }}>CPC</th>
            <th style={{ padding: "6px 8px" }}>Score</th>
            <th style={{ padding: "6px 8px" }}>Intent</th>
            <th style={{ padding: "6px 8px" }}>Funnel</th>
            <th style={{ padding: "6px 8px" }}>Competitors</th>
            <th style={{ padding: "6px 8px", minWidth: 220 }}>
              Competitor ranking titles
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const titles = serpTitles(r);
            const comps = Array.isArray(r.competitor_domains)
              ? (r.competitor_domains as string[])
              : [];
            const positions = kwRows(r.competitor_positions);
            return (
              <tr
                key={`${String(r.keyword)}-${i}`}
                style={{ borderBottom: "1px solid var(--line)" }}
              >
                <td
                  style={{
                    padding: "8px",
                    verticalAlign: "top",
                    fontWeight: 600,
                  }}
                >
                  {String(r.keyword || "—")}
                  {r.gap_flag ? (
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--amber, #b45309)",
                        fontWeight: 500,
                      }}
                    >
                      Gap opportunity
                    </div>
                  ) : null}
                  {r.client_position != null ? (
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--muted)",
                        fontWeight: 400,
                      }}
                    >
                      Your pos: {String(r.client_position)}
                    </div>
                  ) : null}
                </td>
                <td
                  style={{
                    padding: "8px",
                    verticalAlign: "top",
                    whiteSpace: "nowrap",
                  }}
                >
                  {fmtVol(r.volume)}
                  {r.volume_ahrefs != null || r.volume_dataforseo != null ? (
                    <div style={{ fontSize: 10, color: "var(--muted)" }}>
                      {r.volume_ahrefs != null
                        ? `A ${fmtVol(r.volume_ahrefs)}`
                        : ""}
                      {r.volume_ahrefs != null && r.volume_dataforseo != null
                        ? " · "
                        : ""}
                      {r.volume_dataforseo != null
                        ? `D ${fmtVol(r.volume_dataforseo)}`
                        : ""}
                    </div>
                  ) : null}
                </td>
                <td style={{ padding: "8px", verticalAlign: "top" }}>
                  {fmtNum(r.difficulty)}
                </td>
                <td style={{ padding: "8px", verticalAlign: "top" }}>
                  {r.cpc != null ? `$${Number(r.cpc).toFixed(2)}` : "—"}
                </td>
                <td style={{ padding: "8px", verticalAlign: "top" }}>
                  {r.opportunity_score != null
                    ? `${r.opportunity_score}%`
                    : "—"}
                </td>
                <td style={{ padding: "8px", verticalAlign: "top" }}>
                  {String(r.intent || "—")}
                </td>
                <td style={{ padding: "8px", verticalAlign: "top" }}>
                  <FunnelBadge stage={r.funnel} />
                </td>
                <td
                  style={{ padding: "8px", verticalAlign: "top", fontSize: 11 }}
                >
                  {positions.length
                    ? positions
                        .slice(0, 4)
                        .map(
                          (p) => `${String(p.domain)} (#${String(p.position)})`,
                        )
                        .join(", ")
                    : comps.slice(0, 3).join(", ") || "—"}
                </td>
                <td style={{ padding: "8px", verticalAlign: "top" }}>
                  {titles.length ? (
                    <ul
                      style={{
                        margin: 0,
                        paddingLeft: 16,
                        fontSize: 11,
                        lineHeight: 1.35,
                      }}
                    >
                      {titles.slice(0, 4).map((t, ti) => (
                        <li
                          key={`${String(t.title)}-${ti}`}
                          style={{ marginBottom: 4 }}
                        >
                          <span style={{ fontWeight: 600 }}>
                            {String(t.title)}
                          </span>
                          <span style={{ color: "var(--muted)" }}>
                            {" "}
                            — {String(t.domain || "—")}
                            {t.position != null
                              ? ` · #${String(t.position)}`
                              : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span style={{ color: "var(--muted)" }}>—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function SearchDemandCard({ payload, canAct, onAction }: Props) {
  const bestTable = kwRows(payload.keyword_table);
  const topics = kwRows(payload.topics);
  const clusters = kwRows(payload.clusters);
  const competitorDomains = Array.isArray(payload.competitor_domains)
    ? (payload.competitor_domains as string[])
    : [];
  const clusterReport =
    payload.cluster_report && typeof payload.cluster_report === "object"
      ? (payload.cluster_report as Record<string, unknown>)
      : null;
  const reportClusters = kwRows(clusterReport?.clusters);
  const serviceClusters = kwRows(clusterReport?.service_clusters);
  const orphans = kwRows(clusterReport?.orphans);
  const roadmap = kwRows(clusterReport?.content_roadmap);
  const plan =
    payload.topic_plan && typeof payload.topic_plan === "object"
      ? (payload.topic_plan as Record<string, unknown>)
      : null;
  const topicIdeas = kwRows(plan?.topic_ideas);
  const funnelBal =
    (plan?.funnel_balance && typeof plan.funnel_balance === "object"
      ? (plan.funnel_balance as Record<string, unknown>)
      : null) ||
    (payload.funnel_balance && typeof payload.funnel_balance === "object"
      ? (payload.funnel_balance as Record<string, unknown>)
      : null);
  const funnelCounts =
    funnelBal?.counts && typeof funnelBal.counts === "object"
      ? (funnelBal.counts as Record<string, number>)
      : null;
  const funnelWarnings = Array.isArray(funnelBal?.warnings)
    ? (funnelBal!.warnings as string[])
    : [];
  const clusterMap =
    plan?.cluster_map && typeof plan.cluster_map === "object"
      ? (plan.cluster_map as Record<string, unknown>)
      : null;
  const publishing = kwRows(plan?.publishing_order);
  const linking = kwRows(plan?.internal_linking);
  const providers = Array.isArray(payload.providers_used)
    ? (payload.providers_used as string[]).join(", ")
    : "—";
  const seedClusters = kwRows(payload.seed_clusters);
  const keywordDataset = kwRows(payload.keyword_dataset);
  const seededKeywords = keywordDataset.length ? keywordDataset : bestTable;
  const evergreenBase = kwRows(payload.strong_evergreen);
  const trendsBase = kwRows(payload.trend_plays);
  const avoidBase = kwRows(payload.avoid);
  const evergreen = evergreenBase.length
    ? evergreenBase
    : seededKeywords
        .filter((r) => {
          const bucket = String(r.bucket || "").toLowerCase();
          const evergreenClass = String(r.evergreen_class || "").toLowerCase();
          return bucket === "evergreen" || evergreenClass === "evergreen";
        })
        .slice(0, 12);
  const trends = trendsBase.length
    ? trendsBase
    : seededKeywords
        .filter((r) => {
          const bucket = String(r.bucket || "").toLowerCase();
          const trend = String(r.trend || "").toLowerCase();
          return bucket === "trend" || trend === "rising";
        })
        .slice(0, 12);
  const avoid = avoidBase.length
    ? avoidBase
    : seededKeywords
        .filter((r) => {
          const bucket = String(r.bucket || "").toLowerCase();
          const priority = String(r.priority || r.priority_tier || "").toLowerCase();
          return bucket === "avoid" || priority === "avoid";
        })
        .slice(0, 12);
  const seedingMeta =
    payload.keyword_seeding && typeof payload.keyword_seeding === "object"
      ? (payload.keyword_seeding as Record<string, unknown>)
      : null;
  const classCounts =
    seedingMeta?.class_counts && typeof seedingMeta.class_counts === "object"
      ? (seedingMeta.class_counts as Record<string, number>)
      : null;
  const targetCounts =
    seedingMeta?.target_type_counts &&
    typeof seedingMeta.target_type_counts === "object"
      ? (seedingMeta.target_type_counts as Record<string, number>)
      : null;
  const cleaning =
    payload.keyword_cleaning && typeof payload.keyword_cleaning === "object"
      ? (payload.keyword_cleaning as Record<string, unknown>)
      : null;
  const cleaningReasons =
    cleaning?.removed_by_reason && typeof cleaning.removed_by_reason === "object"
      ? (cleaning.removed_by_reason as Record<string, number>)
      : null;
  const cleaningSample = kwRows(cleaning?.sample);
  const workbookRows = kwRows(payload.search_demand_analysis);
  const workbook =
    payload.workbook && typeof payload.workbook === "object"
      ? (payload.workbook as Record<string, unknown>)
      : null;
  const workbookColumns = Array.isArray(workbook?.columns)
    ? (workbook!.columns as string[])
    : [
        "keyword",
        "search_volume_mo",
        "cpc",
        "competition",
        "category_dimension",
        "parent_category",
        "target_url",
        "status",
        "funnel_stage",
        "secondary_keywords",
        "combined_cluster_volume",
        "priority",
      ];

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Search Demand")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Providers: {providers} · Keywords:{" "}
        {String(payload.keyword_count ?? "—")} · Country:{" "}
        {String(payload.country || "us")}
        {competitorDomains.length
          ? ` · Competitors: ${competitorDomains.slice(0, 5).join(", ")}`
          : ""}
      </p>
      {payload.note ? (
        <p style={{ fontSize: 13, marginTop: 4 }}>{String(payload.note)}</p>
      ) : null}

      <WorkbookTable
        title="Search Demand Analysis (workbook)"
        columns={workbookColumns}
        rows={workbookRows}
        maxRows={25}
      />

      {cleaning && cleaning.input_count != null ? (
        <details style={{ margin: "8px 0 12px", fontSize: 12 }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
            Cleaned to CDD / services / pages: kept {String(cleaning.kept_count ?? "—")} of{" "}
            {String(cleaning.input_count)} · removed {String(cleaning.removed_count ?? 0)}
          </summary>
          {cleaningReasons ? (
            <p style={{ color: "var(--muted)", margin: "6px 0 0" }}>
              {Object.entries(cleaningReasons)
                .map(([reason, count]) => `${reason.replace(/_/g, " ")} ${count}`)
                .join(" · ")}
            </p>
          ) : null}
          {cleaningSample.length ? (
            <ul className="missing-list" style={{ marginTop: 6 }}>
              {cleaningSample.slice(0, 8).map((row, i) => (
                <li key={`${String(row.keyword)}-${i}`}>
                  <strong>{String(row.keyword || "—")}</strong>
                  {row.reason ? ` · ${String(row.reason).replace(/_/g, " ")}` : ""}
                  {row.match_class ? ` · ${String(row.match_class)}` : ""}
                  {row.seed ? ` · seed ${String(row.seed)}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </details>
      ) : null}
      {funnelCounts ? (
        <p style={{ fontSize: 12, marginTop: 6 }}>
          Funnel balance: TOFU {funnelCounts.TOFU ?? 0} · MOFU{" "}
          {funnelCounts.MOFU ?? 0} · BOFU {funnelCounts.BOFU ?? 0}
          {funnelBal?.balanced ? " · balanced" : ""}
        </p>
      ) : null}
      {funnelWarnings.length ? (
        <ul className="missing-list">
          {funnelWarnings.slice(0, 3).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      {seedClusters.length > 0 ? (
        <>
          <h4 style={{ marginBottom: 6 }}>
            Multi-mode keyword seeding
            {seedingMeta
              ? ` · vol > ${String(seedingMeta.min_volume ?? 10)} · ${String(
                  seedingMeta.seeded_keyword_count ?? keywordDataset.length,
                )} keywords`
              : ""}
          </h4>
          {classCounts ? (
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
              Exact {Number(classCounts.exact ?? 0)} · Phrase{" "}
              {Number(classCounts.phrase ?? 0)} · Related{" "}
              {Number(classCounts.related ?? 0)} · Broad{" "}
              {Number(classCounts.broad ?? 0)}
              {targetCounts
                ? ` · Roots: services ${Number(targetCounts.service ?? 0)}, pages ${Number(
                    targetCounts.page ?? 0,
                  )}, keywords ${Number(targetCounts.keyword ?? 0)}`
                : ""}
              {seedingMeta?.seeds_with_all_classes !== undefined
                ? ` · All 4 classes: ${Number(seedingMeta.seeds_with_all_classes)}/${
                    seedClusters.length
                  } seeds`
                : ""}
            </p>
          ) : null}
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            {serviceClusters.length
              ? "Seeds are grouped under their service. Open a service, then a seed, to view its complete keyword set."
              : "Select a seed to view its complete keyword set."}
          </p>
          {serviceClusters.length ? (
            <ServiceSeedGroups services={serviceClusters} />
          ) : (
            seedClusters.map((cluster, i) => {
              const exact = kwRows(cluster.exact);
              const phrase = kwRows(cluster.phrase);
              const related = kwRows(cluster.related);
              const broad = kwRows(cluster.broad);
              return (
                <details
                  key={`${String(cluster.seed)}-${i}`}
                  style={{
                    marginBottom: 8,
                    border: "1px solid var(--line)",
                    borderRadius: 8,
                    overflow: "hidden",
                  }}
                >
                  <summary
                    style={{
                      cursor: "pointer",
                      padding: "10px 12px",
                      fontSize: 13,
                      fontWeight: 700,
                      background: "var(--surface, transparent)",
                    }}
                  >
                    {cluster.target_type ? (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          textTransform: "uppercase",
                          color: "var(--muted)",
                          marginRight: 6,
                        }}
                      >
                        {String(cluster.target_type)}
                      </span>
                    ) : null}
                    Seed: {String(cluster.seed || "—")}{" "}
                    {cluster.target && cluster.target !== cluster.seed ? (
                      <span style={{ fontWeight: 500, color: "var(--muted)" }}>
                        → {String(cluster.target)}
                      </span>
                    ) : null}{" "}
                    <span style={{ fontWeight: 500, color: "var(--muted)" }}>
                      ({Number(cluster.keyword_count ?? 0)} keywords)
                    </span>
                    <span
                      style={{
                        display: "block",
                        marginTop: 4,
                        marginLeft: 18,
                        fontSize: 11,
                        fontWeight: 500,
                        color: "var(--muted)",
                      }}
                    >
                      Exact {exact.length} · Phrase {phrase.length} · Related{" "}
                      {related.length} · Broad {broad.length}
                      {Array.isArray(cluster.classes_missing) &&
                      cluster.classes_missing.length
                        ? ` · no results for: ${(cluster.classes_missing as string[]).join(", ")}`
                        : ""}
                    </span>
                  </summary>
                  <div style={{ overflowX: "auto", padding: "0 12px 10px" }}>
                    <table
                      className="kw-report-table"
                      style={{
                        width: "100%",
                        fontSize: 12,
                        borderCollapse: "collapse",
                      }}
                    >
                      <thead>
                        <tr
                          style={{
                            textAlign: "left",
                            borderBottom: "1px solid var(--line)",
                          }}
                        >
                          <th style={{ padding: "4px 6px" }}>Class</th>
                          <th style={{ padding: "4px 6px" }}>Keyword</th>
                          <th style={{ padding: "4px 6px" }}>Intent</th>
                          <th style={{ padding: "4px 6px" }}>Funnel</th>
                          <th style={{ padding: "4px 6px" }}>Volume</th>
                          <th style={{ padding: "4px 6px" }}>KD</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(
                          [
                            ["exact", exact],
                            ["phrase", phrase],
                            ["related", related],
                            ["broad", broad],
                          ] as const
                        ).flatMap(([cls, rows]) =>
                          rows.map((r, j) => (
                            <tr
                              key={`${cls}-${String(r.keyword)}-${j}`}
                              style={{ borderBottom: "1px solid var(--line)" }}
                            >
                              <td
                                style={{
                                  padding: "6px 8px",
                                  textTransform: "capitalize",
                                }}
                              >
                                {cls}
                              </td>
                              <td
                                style={{ padding: "6px 8px", fontWeight: 600 }}
                              >
                                {String(r.keyword || "—")}
                              </td>
                              <td
                                style={{
                                  padding: "6px 8px",
                                  textTransform: "capitalize",
                                }}
                              >
                                {String(r.intent || "—")}
                              </td>
                              <td style={{ padding: "6px 8px" }}>
                                <FunnelBadge stage={r.funnel} />
                              </td>
                              <td style={{ padding: "6px 8px" }}>
                                {fmtVol(r.volume)}
                              </td>
                              <td style={{ padding: "6px 8px" }}>
                                {fmtNum(r.difficulty)}
                              </td>
                            </tr>
                          )),
                        )}
                      </tbody>
                    </table>
                    {exact.length +
                      phrase.length +
                      related.length +
                      broad.length ===
                    0 ? (
                      <p style={{ fontSize: 12, color: "var(--muted)" }}>
                        No keywords above the volume threshold were returned for
                        this seed.
                      </p>
                    ) : null}
                  </div>
                </details>
              );
            })
          )}
        </>
      ) : null}

      {plan ? (
        <>
          <h4 style={{ marginBottom: 6 }}>
            Topic Plan: {String(plan.seed || "Seed")}
            {plan.audience && typeof plan.audience === "string"
              ? ` · Audience: ${plan.audience}`
              : plan.audience && typeof plan.audience === "object"
                ? (() => {
                    const aud = plan.audience as Record<string, unknown>;
                    const primary =
                      aud.primary && typeof aud.primary === "object"
                        ? (aud.primary as Record<string, unknown>)
                        : aud;
                    const titles = Array.isArray(primary.job_titles)
                      ? (primary.job_titles as unknown[])
                          .slice(0, 3)
                          .map((t) => String(t))
                          .join(", ")
                      : "";
                    const age = primary.age_range
                      ? `ages ${String(primary.age_range)}`
                      : "";
                    const label = [titles, age].filter(Boolean).join(" · ");
                    return label ? ` · Audience: ${label}` : "";
                  })()
                : ""}
          </h4>
          {plan.selection_mode || plan.selection_version ? (
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
              Selection: {String(plan.selection_mode || "seeded")} ·{" "}
              {String(plan.selection_version || "")}
              {Array.isArray(plan.assigned_services) &&
              plan.assigned_services.length
                ? ` · Services: ${(plan.assigned_services as unknown[])
                    .slice(0, 8)
                    .map((s) => String(s))
                    .join(", ")}`
                : ""}
            </p>
          ) : (
            <p style={{ fontSize: 12, color: "var(--danger, #b42318)", marginTop: 0 }}>
              This Topic Plan is from an older run. Re-run Search Demand to rebuild
              topics one-per-service from Multi-mode seeding.
            </p>
          )}
          <div style={{ overflowX: "auto", marginBottom: 12 }}>
            <table
              style={{
                width: "100%",
                fontSize: 12,
                borderCollapse: "collapse",
              }}
            >
              <thead>
                <tr
                  style={{
                    textAlign: "left",
                    borderBottom: "1px solid var(--line)",
                  }}
                >
                  <th style={{ padding: "4px 6px" }}>#</th>
                  <th style={{ padding: "4px 6px" }}>Suggested title</th>
                  <th style={{ padding: "4px 6px" }}>Primary keyword</th>
                  <th style={{ padding: "4px 6px", minWidth: 180 }}>
                    Secondary keywords
                  </th>
                  <th style={{ padding: "4px 6px" }}>Type</th>
                  <th style={{ padding: "4px 6px" }}>Traffic</th>
                  <th style={{ padding: "4px 6px" }}>Volume</th>
                  <th style={{ padding: "4px 6px" }}>KD</th>
                  <th style={{ padding: "4px 6px" }}>Intent</th>
                  <th style={{ padding: "4px 6px" }}>Funnel</th>
                  <th style={{ padding: "4px 6px", minWidth: 180 }}>
                    Competitor titles
                  </th>
                </tr>
              </thead>
              <tbody>
                {topicIdeas.slice(0, 12).map((t, i) => {
                  const titles = serpTitles(t);
                  return (
                    <tr
                      key={`${String(t.title)}-${i}`}
                      style={{ borderBottom: "1px solid var(--line)" }}
                    >
                      <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                        {i + 1}
                      </td>
                      <td
                        style={{
                          padding: "6px 8px",
                          verticalAlign: "top",
                          fontWeight: 600,
                        }}
                      >
                        {String(t.title || "")}
                      </td>
                      <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                        {String(t.primary_keyword || t.keyword || "")}
                      </td>
                      <td
                        style={{
                          padding: "6px 8px",
                          verticalAlign: "top",
                          fontSize: 11,
                        }}
                      >
                        {Array.isArray(t.secondary_keywords) &&
                        t.secondary_keywords.length
                          ? (t.secondary_keywords as unknown[])
                              .slice(0, 4)
                              .map((x) => String(x))
                              .join(" · ")
                          : Array.isArray(t.supporting_keywords) &&
                              t.supporting_keywords.length
                            ? (t.supporting_keywords as unknown[])
                                .slice(0, 4)
                                .map((x) => String(x))
                                .join(" · ")
                            : "—"}
                      </td>
                      <td
                        style={{
                          padding: "6px 8px",
                          verticalAlign: "top",
                          textTransform: "capitalize",
                        }}
                      >
                        {String(t.match_class || "—")}
                      </td>
                      <td
                        style={{
                          padding: "6px 8px",
                          verticalAlign: "top",
                          textTransform: "capitalize",
                        }}
                      >
                        {String(t.traffic || "—")}
                      </td>
                      <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                        {fmtVol(t.volume)}
                      </td>
                      <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                        {fmtNum(t.difficulty)}
                      </td>
                      <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                        {String(t.intent || "")}
                      </td>
                      <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                        {String(t.funnel || "")}
                      </td>
                      <td
                        style={{
                          padding: "6px 8px",
                          verticalAlign: "top",
                          fontSize: 11,
                        }}
                      >
                        {titles.length
                          ? titles
                              .slice(0, 2)
                              .map((x) => String(x.title))
                              .join(" · ")
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {topicIdeas[0] ? (
            <details style={{ marginBottom: 12, fontSize: 13 }}>
              <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                Topic details (first idea)
              </summary>
              <ul className="missing-list" style={{ marginTop: 8 }}>
                <li>
                  <strong>{String(topicIdeas[0].title)}</strong>
                </li>
                <li>
                  Primary keyword: {String(topicIdeas[0].primary_keyword || topicIdeas[0].keyword || "—")} · Volume:{" "}
                  {fmtVol(topicIdeas[0].volume)} · KD:{" "}
                  {fmtNum(topicIdeas[0].difficulty)}
                </li>
                {(Array.isArray(topicIdeas[0].secondary_keywords) &&
                  topicIdeas[0].secondary_keywords.length) ||
                (Array.isArray(topicIdeas[0].supporting_keywords) &&
                  topicIdeas[0].supporting_keywords.length) ? (
                  <li>
                    Secondary keywords:{" "}
                    {(
                      (Array.isArray(topicIdeas[0].secondary_keywords)
                        ? (topicIdeas[0].secondary_keywords as unknown[])
                        : Array.isArray(topicIdeas[0].supporting_keywords)
                          ? (topicIdeas[0].supporting_keywords as unknown[])
                          : [])
                    )
                      .slice(0, 6)
                      .map((x) => String(x))
                      .join(" · ")}
                  </li>
                ) : null}
                <li>Why: {String(topicIdeas[0].why || "—")}</li>
                <li>Angle: {String(topicIdeas[0].angle || "—")}</li>
                {Array.isArray(topicIdeas[0].key_sections) ? (
                  <li>
                    Sections:{" "}
                    {(topicIdeas[0].key_sections as string[])
                      .slice(0, 5)
                      .join(" · ")}
                  </li>
                ) : null}
                {serpTitles(topicIdeas[0]).length ? (
                  <li>
                    Competitor titles:{" "}
                    {serpTitles(topicIdeas[0])
                      .slice(0, 3)
                      .map(
                        (t) =>
                          `${String(t.title)} (${String(t.domain || "?")})`,
                      )
                      .join(" · ")}
                  </li>
                ) : null}
              </ul>
            </details>
          ) : null}

          {clusterMap?.pillar ? (
            <p style={{ fontSize: 13, margin: "0 0 8px" }}>
              <strong>Pillar:</strong> {String(clusterMap.pillar)}
              {Array.isArray(clusterMap.supporting) &&
              clusterMap.supporting.length
                ? ` · ${clusterMap.supporting.length} supporting`
                : ""}
            </p>
          ) : null}

          {publishing.length ? (
            <>
              <h4 style={{ marginBottom: 6 }}>Publishing order</h4>
              <ol style={{ fontSize: 13, marginTop: 0, paddingLeft: 20 }}>
                {publishing.slice(0, 6).map((p, i) => (
                  <li key={`${String(p.title)}-${i}`}>
                    {String(p.title)}
                    {p.reason ? ` — ${String(p.reason)}` : ""}
                  </li>
                ))}
              </ol>
            </>
          ) : null}

          {linking.length ? (
            <>
              <h4 style={{ marginBottom: 6 }}>Internal linking</h4>
              <ul className="missing-list">
                {linking.slice(0, 5).map((l, i) => (
                  <li key={`${String(l.from)}-${i}`}>
                    {String(l.from)} →{" "}
                    {Array.isArray(l.to)
                      ? (l.to as string[]).slice(0, 3).join(", ")
                      : "—"}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}

      <h4 style={{ marginBottom: 6 }}>Strong evergreen</h4>
      <KeywordDetailTable rows={evergreen.slice(0, 8)} empty="—" />

      <h4 style={{ marginBottom: 6 }}>Trend plays</h4>
      <ul className="missing-list">
        {trends.slice(0, 6).map((r) => (
          <li key={`t-${String(r.keyword)}`}>
            <strong>{String(r.keyword)}</strong>
            {r.volume != null ? ` · vol ${fmtVol(r.volume)}` : ""}
            {r.opportunity_score != null ? ` · ${r.opportunity_score}%` : ""}
          </li>
        ))}
        {!trends.length ? <li>—</li> : null}
      </ul>

      {clusterReport ? (
        <>
          <h4 style={{ marginBottom: 6 }}>Keyword Cluster Report</h4>
          <p style={{ fontSize: 13, marginTop: 0 }}>
            Total: {String(clusterReport.total_keywords ?? "—")} · Clusters:{" "}
            {String(clusterReport.clusters_created ?? reportClusters.length)} ·
            Orphans: {String(clusterReport.orphan_count ?? orphans.length)}
          </p>
          {reportClusters.slice(0, 5).map((c, i) => {
            const kws = kwRows(c.keywords);
            return (
              <div
                key={`${String(c.name)}-${i}`}
                style={{ marginBottom: 10, fontSize: 13 }}
              >
                <strong>
                  Cluster {i + 1}:{" "}
                  {String(c.name || c.primary_keyword || "Untitled")}
                </strong>
                <div style={{ color: "var(--muted)" }}>
                  Intent: {String(c.intent || "—")} ·{" "}
                  {String(c.recommended_content || c.content_type || "—")} ·{" "}
                  {String(c.recommended_url || "—")}
                  {c.est_traffic != null || c.total_volume != null
                    ? ` · est. traffic/vol ${fmtVol(c.est_traffic ?? c.total_volume)}`
                    : ""}
                </div>
                <div style={{ overflowX: "auto", marginTop: 6 }}>
                  <table
                    style={{
                      width: "100%",
                      fontSize: 11,
                      borderCollapse: "collapse",
                    }}
                  >
                    <thead>
                      <tr
                        style={{
                          textAlign: "left",
                          borderBottom: "1px solid var(--line)",
                        }}
                      >
                        <th style={{ padding: "3px 6px" }}>Keyword</th>
                        <th style={{ padding: "3px 6px" }}>Volume</th>
                        <th style={{ padding: "3px 6px" }}>KD</th>
                        <th style={{ padding: "3px 6px" }}>Funnel</th>
                        <th style={{ padding: "3px 6px" }}>Role</th>
                      </tr>
                    </thead>
                    <tbody>
                      {kws.slice(0, 6).map((k) => (
                        <tr
                          key={String(k.keyword)}
                          style={{ borderBottom: "1px solid var(--line)" }}
                        >
                          <td style={{ padding: "3px 6px" }}>
                            {String(k.keyword)}
                          </td>
                          <td style={{ padding: "3px 6px" }}>
                            {fmtVol(k.volume)}
                          </td>
                          <td style={{ padding: "3px 6px" }}>
                            {fmtNum(k.difficulty)}
                          </td>
                          <td style={{ padding: "3px 6px" }}>
                            <FunnelBadge stage={k.funnel} />
                          </td>
                          <td style={{ padding: "3px 6px" }}>
                            {String(k.role || "—")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
          {roadmap.length ? (
            <>
              <h4 style={{ marginBottom: 6 }}>Content roadmap</h4>
              <ol style={{ fontSize: 13, marginTop: 0, paddingLeft: 20 }}>
                {roadmap.slice(0, 6).map((r, i) => (
                  <li key={`${String(r.cluster)}-${i}`}>
                    {String(r.cluster)} — {String(r.content_type || "")} ·{" "}
                    {String(r.target_keyword || "")}
                    {r.est_traffic != null
                      ? ` · est. ${fmtVol(r.est_traffic)}`
                      : ""}
                  </li>
                ))}
              </ol>
            </>
          ) : null}
          {orphans.length ? (
            <p style={{ fontSize: 12, color: "var(--muted)" }}>
              Orphans:{" "}
              {orphans
                .slice(0, 5)
                .map(
                  (o) =>
                    `${String(o.keyword)}${o.volume != null ? ` (${fmtVol(o.volume)})` : ""}`,
                )
                .join(", ")}
              {orphans.length > 5 ? "…" : ""}
            </p>
          ) : null}
        </>
      ) : null}

      {avoid.length ? (
        <>
          <h4 style={{ marginBottom: 6 }}>Avoid / low priority</h4>
          <ul className="missing-list">
            {avoid.slice(0, 6).map((r) => (
              <li key={`a-${String(r.keyword)}`}>
                {String(r.keyword)}
                {r.volume != null ? ` · vol ${fmtVol(r.volume)}` : ""}
                {r.rationale ? ` — ${String(r.rationale)}` : ""}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h4 style={{ marginBottom: 6 }}>Topics / clusters</h4>
      <p style={{ fontSize: 13 }}>
        {topics.length} topics · {clusters.length} clusters
        {avoid.length ? ` · ${avoid.length} avoid` : ""}
      </p>

      {canAct && (
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onAction("approve")}
          >
            Approve into memory
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => onAction("reject")}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
