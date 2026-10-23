import { createContext, useContext, useMemo, useState } from "react";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string, edits?: Record<string, unknown>) => void;
};

type ManualCompetitorKeyword = { keyword: string; competitor: string };

type KeywordFocus = {
  focused: (keyword: string) => boolean;
  onToggle: (keyword: string) => void;
  disabled?: boolean;
};

const INTENTS = ["informational", "commercial", "transactional", "navigational"] as const;
type IntentFilter = "all" | (typeof INTENTS)[number];

/** The intent the keyword tables are filtered to ("all" shows everything). */
const IntentFilterContext = createContext<IntentFilter>("all");

function keywordIntent(row: Record<string, unknown>, fallback?: unknown): string {
  const intent = String(row.intent || fallback || "").trim().toLowerCase();
  return (INTENTS as readonly string[]).includes(intent) ? intent : intent;
}

function useIntentFilter<T extends Record<string, unknown>>(rows: T[]): T[] {
  const filter = useContext(IntentFilterContext);
  return filter === "all" ? rows : rows.filter((r) => keywordIntent(r) === filter);
}

function IntentFilterBar({
  value,
  onChange,
  counts,
}: {
  value: IntentFilter;
  onChange: (next: IntentFilter) => void;
  counts: Record<string, number> | null;
}) {
  const options: IntentFilter[] = ["all", ...INTENTS];
  return (
    <div className="sd-intent-filter" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "10px 0" }}>
      <span style={{ fontSize: 12, color: "var(--muted)" }}>Show keywords by intent</span>
      <div className="ct-seg" role="group" aria-label="Filter keywords by intent">
        {options.map((option) => {
          const count = option === "all" ? null : counts?.[option];
          return (
            <button
              key={option}
              type="button"
              className={value === option ? "is-on" : ""}
              aria-pressed={value === option}
              onClick={() => onChange(option)}
            >
              {option === "all" ? "All" : option[0].toUpperCase() + option.slice(1)}
              {count != null ? ` ${count}` : ""}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Other wordings of the same search, folded into this keyword. */
function VariantNote({ row }: { row: Record<string, unknown> }) {
  const variants = Array.isArray(row.close_variants)
    ? (row.close_variants as unknown[]).map(String).filter(Boolean)
    : [];
  if (!variants.length) return null;
  return (
    <span
      title={`Same search, also worded as: ${variants.join(", ")}`}
      style={{ display: "block", fontSize: 10, fontWeight: 500, color: "var(--muted)", cursor: "help", whiteSpace: "nowrap" }}
    >
      +{variants.length} {variants.length === 1 ? "variant" : "variants"}
    </span>
  );
}

function normKw(value: unknown): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

function kwRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<
    Record<string, unknown>
  >;
}

function fmtVol(v: unknown): string {
  if (v == null || v === "" || v === "N/A") return "N/A";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString();
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "N/A";
  return String(v);
}

function fmtCpc(v: unknown): string {
  if (v == null || v === "") return "N/A";
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : String(v);
}

const FUNNEL_ORDER = ["TOFU", "MOFU", "BOFU"];

function funnelRank(row: Record<string, unknown>): number {
  const stage = String(row.funnel_stage ?? row.funnel ?? "").toUpperCase();
  const index = FUNNEL_ORDER.indexOf(stage);
  return index === -1 ? FUNNEL_ORDER.length : index;
}

function volumeOf(row: Record<string, unknown>): number {
  const value = Number(row.search_volume_mo ?? row.volume ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function byFunnelThenVolume(rows: Array<Record<string, unknown>>) {
  return [...rows].sort((a, b) => {
    const stage = funnelRank(a) - funnelRank(b);
    if (stage) return stage;
    const volume = volumeOf(b) - volumeOf(a);
    if (volume) return volume;
    return String(a.keyword || "").localeCompare(String(b.keyword || ""));
  });
}

function FunnelBadge({ stage }: { stage: unknown }) {
  const s = String(stage || "")
    .trim()
    .toUpperCase();
  const cls = s === "TOFU" || s === "MOFU" || s === "BOFU" ? s.toLowerCase() : "unknown";
  return <span className={`funnel-badge ${cls}`}>{s || "—"}</span>;
}

function buildAnalysisIndex(
  workbookRows: Array<Record<string, unknown>>,
  dataset: Array<Record<string, unknown>>,
): Map<string, Record<string, unknown>> {
  const index = new Map<string, Record<string, unknown>>();
  for (const row of dataset) {
    const key = normKw(row.keyword);
    if (key) index.set(key, row);
  }
  // Workbook wins for the analysis fields when both exist.
  for (const row of workbookRows) {
    const key = normKw(row.keyword);
    if (!key) continue;
    index.set(key, { ...(index.get(key) || {}), ...row });
  }
  return index;
}

function enrichKeyword(
  row: Record<string, unknown>,
  analysis: Map<string, Record<string, unknown>>,
): Record<string, unknown> {
  const key = normKw(row.keyword);
  const extra = key ? analysis.get(key) : undefined;
  const merged: Record<string, unknown> = extra
    ? {
        ...row,
        ...extra,
        // Seed-specific fields must not be overwritten by the flat workbook.
        match_class: row.match_class ?? extra.match_class,
        gap_flag: row.gap_flag ?? extra.gap_flag,
        competitor_domains: row.competitor_domains ?? extra.competitor_domains,
        keyword: row.keyword || extra.keyword,
        search_volume_mo:
          extra.search_volume_mo ?? row.volume ?? row.search_volume_mo,
        funnel_stage: extra.funnel_stage ?? row.funnel ?? row.funnel_stage,
        competition: extra.competition ?? row.difficulty ?? row.competition,
      }
    : {
        ...row,
        search_volume_mo: row.volume ?? row.search_volume_mo,
        funnel_stage: row.funnel ?? row.funnel_stage,
        competition: row.difficulty ?? row.competition,
        parent_category: row.parent_topic || row.target || row.seed || row.parent_category,
        category_dimension: row.target_type || row.category_dimension,
      };

  // Seed target_type ("service") is the expansion root — not the page type.
  // Blog destinations must stay labelled blog even on a service seed.
  const targetUrl = String(merged.target_url || row.recommended_url || "");
  const path = targetUrl.toLowerCase();
  if (
    ["/blog/", "/blogs/", "/guides/", "/guide/", "/articles/", "/insights/", "/news/"].some(
      (m) => path.includes(m),
    )
  ) {
    merged.category_dimension = "blog";
  }
  return merged;
}

function HighlightList({
  title,
  rows,
  showRationale,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  showRationale?: boolean;
}) {
  if (!rows.length) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <h4 style={{ margin: "0 0 4px", fontSize: 13 }}>{title}</h4>
      <ul className="missing-list" style={{ margin: 0 }}>
        {rows.map((r, i) => (
          <li key={`${String(r.keyword)}-${i}`}>
            <strong>{String(r.keyword || "—")}</strong>
            {r.volume != null ? ` · vol ${fmtVol(r.volume)}` : ""}
            {r.opportunity_score != null ? ` · ${r.opportunity_score}%` : ""}
            {showRationale && r.rationale ? ` — ${String(r.rationale)}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SeedKeywordBlock({
  seed,
  analysis,
  keywordFocus,
}: {
  seed: Record<string, unknown>;
  analysis: Map<string, Record<string, unknown>>;
  keywordFocus: KeywordFocus;
}) {
  const allKeywords = byFunnelThenVolume(
    kwRows(seed.keywords).map((k) => enrichKeyword(k, analysis)),
  );
  const keywords = useIntentFilter(allKeywords);
  const filtered = keywords.length !== allKeywords.length;
  const counts =
    seed.class_counts && typeof seed.class_counts === "object"
      ? (seed.class_counts as Record<string, number>)
      : {};
  const missing = Array.isArray(seed.classes_missing)
    ? (seed.classes_missing as string[])
    : [];

  // With a filter on, seeds that have none of that intent are hidden.
  if (filtered && keywords.length === 0) return null;

  return (
    <details style={{ marginTop: 8 }}>
      <summary style={{ cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
        {String(seed.seed_label || "Seed")}: {String(seed.seed || "—")}{" "}
        <span style={{ fontWeight: 500, color: "var(--muted)" }}>
          (
          {filtered
            ? `${keywords.length} of ${allKeywords.length} keywords`
            : `${Number(seed.keyword_count ?? keywords.length)} keywords`}
          )
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
          Exact {Number(counts.exact ?? 0)} · Phrase {Number(counts.phrase ?? 0)} · Related{" "}
          {Number(counts.related ?? 0)} · Broad {Number(counts.broad ?? 0)}
          {missing.length ? ` · no results for: ${missing.join(", ")}` : ""}
        </span>
      </summary>
      <div style={{ overflowX: "auto", paddingBottom: 8 }}>
        <table
          className="kw-report-table"
          style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}
        >
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
              <th style={{ padding: "3px 6px" }}>Focus</th>
              <th style={{ padding: "3px 6px" }}>Class</th>
              <th style={{ padding: "3px 6px" }}>Keyword</th>
              <th style={{ padding: "3px 6px" }}>Volume</th>
              <th style={{ padding: "3px 6px" }}>Basis</th>
              <th style={{ padding: "3px 6px" }}>CPC</th>
              <th style={{ padding: "3px 6px" }}>Competition</th>
              <th style={{ padding: "3px 6px" }}>Category</th>
              <th style={{ padding: "3px 6px" }}>Parent</th>
              <th style={{ padding: "3px 6px" }}>Target URL</th>
              <th style={{ padding: "3px 6px" }}>Status</th>
              <th style={{ padding: "3px 6px" }}>Funnel</th>
              <th style={{ padding: "3px 6px" }}>Secondary keywords</th>
              <th style={{ padding: "3px 6px" }}>Cluster vol</th>
              <th style={{ padding: "3px 6px" }}>Priority</th>
              <th style={{ padding: "3px 6px" }}>Intent</th>
              <th style={{ padding: "3px 6px" }}>Gap</th>
            </tr>
          </thead>
          <tbody>
            {keywords.map((k, kj) => {
              const keyword = String(k.keyword || "");
              const focused = !keyword || keywordFocus.focused(keyword);
              return (
                <tr
                  key={`${keyword}-${kj}`}
                  style={{
                    borderBottom: "1px solid var(--line)",
                    opacity: focused ? 1 : 0.45,
                  }}
                >
                  <td style={{ padding: "3px 6px" }}>
                    {keyword ? (
                      <input
                        type="checkbox"
                        checked={focused}
                        disabled={keywordFocus.disabled}
                        aria-label={`Focus ${keyword}`}
                        onChange={() => keywordFocus.onToggle(keyword)}
                      />
                    ) : null}
                  </td>
                  <td style={{ padding: "3px 6px", textTransform: "capitalize" }}>
                    {String(k.match_class || "—")}
                  </td>
                  <td style={{ padding: "3px 6px", fontWeight: 600 }}>
                    {keyword || "—"}
                    {String(k.role || "") === "Primary" ? " · primary" : ""}
                    <VariantNote row={k} />
                  </td>
                  <td style={{ padding: "3px 6px" }}>
                    {fmtVol(k.search_volume_mo ?? k.volume)}
                  </td>
                  <td style={{ padding: "3px 6px" }}>
                    {String(k.volume_basis || "—")}
                  </td>
                  <td style={{ padding: "3px 6px" }}>{fmtCpc(k.cpc)}</td>
                  <td style={{ padding: "3px 6px" }}>
                    {fmtNum(k.competition ?? k.difficulty)}
                  </td>
                  <td style={{ padding: "3px 6px" }}>
                    {String(k.category_dimension || "—")}
                  </td>
                  <td style={{ padding: "3px 6px" }}>
                    {String(k.parent_category || "—")}
                  </td>
                  <td style={{ padding: "3px 6px", maxWidth: 160 }}>
                    {String(k.target_url || "—")}
                  </td>
                  <td style={{ padding: "3px 6px" }}>{String(k.status || "—")}</td>
                  <td style={{ padding: "3px 6px" }}>
                    <FunnelBadge stage={k.funnel_stage ?? k.funnel} />
                  </td>
                  <td style={{ padding: "3px 6px", maxWidth: 180 }}>
                    {String(k.secondary_keywords || "—")}
                  </td>
                  <td style={{ padding: "3px 6px" }}>
                    {fmtVol(k.combined_cluster_volume)}
                  </td>
                  <td style={{ padding: "3px 6px" }}>{String(k.priority || "—")}</td>
                  <td style={{ padding: "3px 6px", textTransform: "capitalize" }}>
                    {String(k.intent || "—")}
                  </td>
                  <td style={{ padding: "3px 6px" }}>
                    {k.gap_flag ? (
                      <span
                        title={String(
                          (k.competitor_domains as string[] | undefined)?.join(", ") || "",
                        )}
                      >
                        gap
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {keywords.length === 0 ? (
          <p style={{ fontSize: 11, color: "var(--muted)" }}>
            No keywords above the volume threshold were returned for this seed.
          </p>
        ) : null}
      </div>
    </details>
  );
}

function ServiceSeedGroups({
  services,
  analysis,
  keywordFocus,
}: {
  services: Array<Record<string, unknown>>;
  analysis: Map<string, Record<string, unknown>>;
  keywordFocus: KeywordFocus;
}) {
  return (
    <>
      {services.map((service, si) => {
        const seeds = kwRows(service.seeds);
        const subservices = kwRows(service.subservices);
        const categoryLeader =
          service.category_leader && typeof service.category_leader === "object"
            ? (service.category_leader as Record<string, unknown>)
            : null;
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
                ({Number(service.seed_count ?? seeds.length)} seeds
                {subservices.length ? ` · ${subservices.length} subservices` : ""} ·{" "}
                {Number(service.keyword_count ?? 0)} keywords
                {service.total_volume != null
                  ? ` · vol ${fmtVol(service.total_volume)}`
                  : ""}
                )
              </span>
              {categoryLeader?.competitor ? (
                <span
                  style={{
                    display: "block",
                    marginTop: 4,
                    fontSize: 11,
                    fontWeight: 500,
                    color: "var(--muted)",
                  }}
                >
                  Strongest competitor in{" "}
                  {String(service.competitor_category || "category").replace(/_/g, " ")}:{" "}
                  {String(categoryLeader.competitor)}
                </span>
              ) : null}
            </summary>
            <div style={{ padding: "0 12px 10px" }}>
              {subservices.map((sub, subIdx) => {
                const subSeeds = kwRows(sub.seeds);
                const gapCount = Number(sub.gap_keyword_count ?? 0);
                const leaders = kwRows(sub.competitor_leaders);
                return (
                  <details
                    key={`${String(sub.subservice)}-${subIdx}`}
                    style={{
                      marginTop: 10,
                      border: "1px dashed var(--line)",
                      borderRadius: 8,
                      padding: "0 8px 8px",
                    }}
                  >
                    <summary
                      style={{
                        cursor: "pointer",
                        fontSize: 12,
                        fontWeight: 700,
                        padding: "8px 4px",
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
                        Subservice
                      </span>
                      {String(sub.subservice || "—")}{" "}
                      <span style={{ fontWeight: 500, color: "var(--muted)" }}>
                        ({Number(sub.keyword_count ?? 0)} keywords
                        {gapCount ? ` · ${gapCount} gaps` : ""})
                      </span>
                      {sub.page_path ? (
                        <span
                          style={{
                            display: "block",
                            marginTop: 2,
                            fontSize: 10,
                            fontWeight: 500,
                            color: "var(--muted)",
                          }}
                        >
                          {String(sub.page_path)}
                        </span>
                      ) : null}
                      {leaders.length ? (
                        <span
                          style={{
                            display: "block",
                            marginTop: 2,
                            fontSize: 10,
                            fontWeight: 500,
                            color: "var(--muted)",
                          }}
                        >
                          Competitors ranking:{" "}
                          {leaders
                            .slice(0, 3)
                            .map((l) => String(l.name || l.domain || "—"))
                            .join(", ")}
                        </span>
                      ) : null}
                    </summary>
                    {subSeeds.map((seed, sj) => (
                      <SeedKeywordBlock
                        key={`${String(sub.subservice)}-${String(seed.seed)}-${sj}`}
                        seed={seed}
                        analysis={analysis}
                        keywordFocus={keywordFocus}
                      />
                    ))}
                  </details>
                );
              })}
              {seeds.map((seed, sj) => (
                <SeedKeywordBlock
                  key={`${String(seed.seed)}-${sj}`}
                  seed={seed}
                  analysis={analysis}
                  keywordFocus={keywordFocus}
                />
              ))}
            </div>
          </details>
        );
      })}
    </>
  );
}

function FlatSeedClusters({
  seeds,
  analysis,
  keywordFocus,
}: {
  seeds: Array<Record<string, unknown>>;
  analysis: Map<string, Record<string, unknown>>;
  keywordFocus: KeywordFocus;
}) {
  return (
    <>
      {seeds.map((cluster, i) => {
        const exact = kwRows(cluster.exact).map((r) => ({
          ...enrichKeyword(r, analysis),
          match_class: "exact",
        }));
        const phrase = kwRows(cluster.phrase).map((r) => ({
          ...enrichKeyword(r, analysis),
          match_class: "phrase",
        }));
        const related = kwRows(cluster.related).map((r) => ({
          ...enrichKeyword(r, analysis),
          match_class: "related",
        }));
        const broad = kwRows(cluster.broad).map((r) => ({
          ...enrichKeyword(r, analysis),
          match_class: "broad",
        }));
        const merged = [...exact, ...phrase, ...related, ...broad];
        return (
          <SeedKeywordBlock
            key={`${String(cluster.seed)}-${i}`}
            seed={{
              ...cluster,
              keywords: merged.length ? merged : kwRows(cluster.keywords),
              class_counts: {
                exact: exact.length,
                phrase: phrase.length,
                related: related.length,
                broad: broad.length,
              },
            }}
            analysis={analysis}
            keywordFocus={keywordFocus}
          />
        );
      })}
    </>
  );
}

function SubserviceCompetitorMatrix({
  rows,
}: {
  rows: Array<Record<string, unknown>>;
}) {
  if (!rows.length) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 6 }}>Subservices vs competitors</h4>
      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
        Gap counts and who ranks for those terms.
      </p>
      <div style={{ overflowX: "auto" }}>
        <table
          className="kw-report-table"
          style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}
        >
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
              <th style={{ padding: "6px 8px" }}>Service</th>
              <th style={{ padding: "6px 8px" }}>Subservice</th>
              <th style={{ padding: "6px 8px" }}>Keywords</th>
              <th style={{ padding: "6px 8px" }}>Gaps</th>
              <th style={{ padding: "6px 8px" }}>Competitors ranking</th>
              <th style={{ padding: "6px 8px" }}>Category leader</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const leaders = kwRows(row.competitor_leaders);
              const categoryLeader =
                row.category_leader && typeof row.category_leader === "object"
                  ? (row.category_leader as Record<string, unknown>)
                  : null;
              return (
                <tr
                  key={`${String(row.subservice)}-${i}`}
                  style={{ borderBottom: "1px solid var(--line)" }}
                >
                  <td style={{ padding: "6px 8px" }}>
                    {String(row.parent_service || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                    {String(row.subservice || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(row.keyword_count)}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(row.gap_keyword_count)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {leaders.length
                      ? leaders
                          .slice(0, 3)
                          .map((l) => String(l.name || l.domain || "—"))
                          .join(", ")
                      : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {categoryLeader?.competitor
                      ? String(categoryLeader.competitor)
                      : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function keywordStage(row: Record<string, unknown>, fallback: unknown): string {
  const stage = String(row.funnel || fallback || "").trim().toUpperCase();
  return FUNNEL_ORDER.includes(stage) ? stage : "—";
}

function focusViews(cluster: Record<string, unknown>): Array<Record<string, unknown>> {
  const keywords = kwRows(cluster.keywords);
  const groups = new Map<string, Array<Record<string, unknown>>>();
  for (const row of keywords) {
    const funnel = keywordStage(row, cluster.funnel);
    const intent = keywordIntent(row, cluster.intent);
    const key = `${funnel}|${intent}`;
    const bucket = groups.get(key) || [];
    bucket.push(row);
    groups.set(key, bucket);
  }
  if (groups.size <= 1) return [cluster];
  const base = String(cluster.name || cluster.primary_keyword || "Cluster");
  return [...groups.entries()]
    .sort((a, b) => {
      const [aFunnel, aIntent] = a[0].split("|");
      const [bFunnel, bIntent] = b[0].split("|");
      const stage = FUNNEL_ORDER.indexOf(aFunnel) - FUNNEL_ORDER.indexOf(bFunnel);
      if (stage) return stage;
      return INTENTS.indexOf(aIntent as (typeof INTENTS)[number]) - INTENTS.indexOf(bIntent as (typeof INTENTS)[number]);
    })
    .map(([key, rows]) => {
      const [funnel, intent] = key.split("|");
      const volume = rows.reduce((sum, row) => {
        const n = Number(row.volume);
        return sum + (Number.isFinite(n) ? n : 0);
      }, 0);
      return {
        ...cluster,
        name: `${base} (${funnel} · ${intent})`,
        funnel,
        intent,
        keywords: rows,
        keyword_count: rows.length,
        est_traffic: volume || null,
      };
    });
}

function ClusterKeywords({
  cluster: c,
  index: i,
  keywordFocus,
}: {
  cluster: Record<string, unknown>;
  index: number;
  keywordFocus: KeywordFocus;
}) {
  const all = kwRows(c.keywords);
  const kws = useIntentFilter(all);
  if (kws.length === 0 && all.length) return null;
  const funnel = keywordStage(kws[0] || {}, c.funnel);
  const intent = keywordIntent(kws[0] || {}, c.intent);
  return (
    <details open={i === 0} style={{ marginBottom: 8, fontSize: 13 }}>
      <summary style={{ cursor: "pointer" }}>
        <strong>{String(c.name || c.primary_keyword || `Cluster ${i + 1}`)}</strong>
        <span style={{ marginLeft: 8 }}>
          <FunnelBadge stage={funnel} />
        </span>
        <span style={{ color: "var(--muted)", fontWeight: 500 }}>
          {" "}
          · {intent}
          {" "}
          · {kws.length === all.length ? `${all.length} keywords` : `${kws.length} of ${all.length} keywords`}
          {c.est_traffic != null || c.total_volume != null
            ? ` · vol ${fmtVol(c.est_traffic ?? c.total_volume)}`
            : ""}
        </span>
      </summary>
      <div style={{ color: "var(--muted)", marginTop: 4, fontSize: 12 }}>
        {String(c.recommended_content || c.content_type || "—")} ·{" "}
        {String(c.recommended_url || "—")}
      </div>
      <ul className="missing-list" style={{ marginTop: 6 }}>
        {kws.map((k) => {
          const keyword = String(k.keyword || "");
          const focused = keywordFocus.focused(keyword);
          return (
            <li key={keyword} style={{ opacity: focused ? 1 : 0.45 }}>
              <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={focused}
                  disabled={keywordFocus.disabled || !keyword}
                  aria-label={`Focus ${keyword || "keyword"}`}
                  onChange={() => keywordFocus.onToggle(keyword)}
                />
                <span>
                  <FunnelBadge stage={keywordStage(k, funnel)} /> {keyword || "—"}
                  {k.volume != null ? ` · vol ${fmtVol(k.volume)}` : ""}
                  {k.role ? ` · ${String(k.role)}` : ""}
                  {` · ${keywordIntent(k, intent)}`}
                  <VariantNote row={k} />
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function ClusterSection({
  clusterReport,
  reportClusters,
  orphans,
  roadmap,
  keywordFocus,
}: {
  clusterReport: Record<string, unknown>;
  reportClusters: Array<Record<string, unknown>>;
  orphans: Array<Record<string, unknown>>;
  roadmap: Array<Record<string, unknown>>;
  keywordFocus: KeywordFocus;
}) {
  const shownOrphans = useIntentFilter(orphans);
  const focused = reportClusters
    .flatMap((cluster) => focusViews(cluster))
    .sort((a, b) => {
      const stage = funnelRank(a) - funnelRank(b);
      if (stage) return stage;
      const intent =
        INTENTS.indexOf(keywordIntent({}, a.intent) as (typeof INTENTS)[number]) -
        INTENTS.indexOf(keywordIntent({}, b.intent) as (typeof INTENTS)[number]);
      if (intent) return intent;
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
  return (
    <section style={{ marginTop: 8, marginBottom: 16 }}>
      <h4 style={{ marginBottom: 6 }}>Keyword clusters</h4>
      <p style={{ fontSize: 13, marginTop: 0 }}>
        Each cluster is one topic, one funnel stage, and one search intent. Total:{" "}
        {String(clusterReport.total_keywords ?? "—")} · Clusters:{" "}
        {String(focused.length || clusterReport.clusters_created || reportClusters.length)} · Orphans:{" "}
        {String(clusterReport.orphan_count ?? orphans.length)}
      </p>
      {focused.map((c, i) => (
        <ClusterKeywords
          key={`${String(c.name || c.primary_keyword)}-${i}`}
          cluster={c}
          index={i}
          keywordFocus={keywordFocus}
        />
      ))}
      {roadmap.length ? (
        <>
          <h4 style={{ marginBottom: 6, marginTop: 12 }}>Content roadmap</h4>
          <ol style={{ fontSize: 13, marginTop: 0, paddingLeft: 20 }}>
            {roadmap.slice(0, 8).map((r, i) => (
              <li key={`${String(r.cluster)}-${i}`}>
                {String(r.cluster)} — {String(r.content_type || "")}
                {r.target_keyword ? ` · ${String(r.target_keyword)}` : ""}
                {r.est_traffic != null ? ` · est. ${fmtVol(r.est_traffic)}` : ""}
              </li>
            ))}
          </ol>
        </>
      ) : null}
      {shownOrphans.length ? (
        <div style={{ marginTop: 8 }}>
          <h4 style={{ marginBottom: 6 }}>Orphan keywords</h4>
          <ul className="missing-list">
            {shownOrphans.map((o) => {
              const keyword = String(o.keyword || "");
              const focused = keywordFocus.focused(keyword);
              return (
                <li key={keyword} style={{ opacity: focused ? 1 : 0.45 }}>
                  <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={focused}
                      disabled={keywordFocus.disabled || !keyword}
                      aria-label={`Focus ${keyword || "keyword"}`}
                      onChange={() => keywordFocus.onToggle(keyword)}
                    />
                    <span>
                      <FunnelBadge stage={keywordStage(o, "")} /> {keyword || "—"}
                      {o.volume != null ? ` · vol ${fmtVol(o.volume)}` : ""}
                      {o.intent ? ` · ${keywordIntent(o, "")}` : ""}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

export default function SearchDemandCard({ payload, canAct, onAction }: Props) {
  const competitorDomains = Array.isArray(payload.competitor_domains)
    ? (payload.competitor_domains as string[])
    : [];
  const clusterReport =
    payload.cluster_report && typeof payload.cluster_report === "object"
      ? (payload.cluster_report as Record<string, unknown>)
      : null;
  const reportClusters = kwRows(clusterReport?.clusters);
  const serviceClusters = kwRows(clusterReport?.service_clusters);
  const subserviceMatrix = kwRows(payload.subservice_competitor_matrix);
  const orphans = kwRows(clusterReport?.orphans);
  const roadmap = kwRows(clusterReport?.content_roadmap);
  const topicPlan =
    payload.topic_plan && typeof payload.topic_plan === "object"
      ? (payload.topic_plan as Record<string, unknown>)
      : null;
  const funnelBal =
    (payload.funnel_balance && typeof payload.funnel_balance === "object"
      ? (payload.funnel_balance as Record<string, unknown>)
      : null) ||
    (topicPlan?.funnel_balance && typeof topicPlan.funnel_balance === "object"
      ? (topicPlan.funnel_balance as Record<string, unknown>)
      : null);
  const funnelCounts =
    funnelBal?.counts && typeof funnelBal.counts === "object"
      ? (funnelBal.counts as Record<string, number>)
      : null;
  const funnelWarnings = Array.isArray(funnelBal?.warnings)
    ? (funnelBal!.warnings as string[])
    : [];
  const intentBal =
    payload.intent_balance && typeof payload.intent_balance === "object"
      ? (payload.intent_balance as Record<string, unknown>)
      : null;
  const intentCounts =
    intentBal?.counts && typeof intentBal.counts === "object"
      ? (intentBal.counts as Record<string, number>)
      : null;
  const intentWarnings = Array.isArray(intentBal?.warnings)
    ? (intentBal!.warnings as string[])
    : [];
  const providers = Array.isArray(payload.providers_used)
    ? (payload.providers_used as string[]).join(", ")
    : "—";
  const seedClusters = kwRows(payload.seed_clusters);
  const keywordDataset = kwRows(payload.keyword_dataset);
  const workbookRows = kwRows(payload.search_demand_analysis);
  const analysis = useMemo(
    () => buildAnalysisIndex(workbookRows, keywordDataset),
    [workbookRows, keywordDataset],
  );

  const evergreen = (
    kwRows(payload.strong_evergreen).length
      ? kwRows(payload.strong_evergreen)
      : keywordDataset.filter((r) => {
          const bucket = String(r.bucket || "").toLowerCase();
          const evergreenClass = String(r.evergreen_class || "").toLowerCase();
          return bucket === "evergreen" || evergreenClass === "evergreen";
        })
  ).slice(0, 8);
  const trends = (
    kwRows(payload.trend_plays).length
      ? kwRows(payload.trend_plays)
      : keywordDataset.filter((r) => {
          const bucket = String(r.bucket || "").toLowerCase();
          const trend = String(r.trend || "").toLowerCase();
          return bucket === "trend" || trend === "rising";
        })
  ).slice(0, 6);
  const avoid = (
    kwRows(payload.avoid).length
      ? kwRows(payload.avoid)
      : keywordDataset.filter((r) => {
          const bucket = String(r.bucket || "").toLowerCase();
          const priority = String(r.priority || r.priority_tier || "").toLowerCase();
          return bucket === "avoid" || priority === "avoid";
        })
  ).slice(0, 6);

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
  const dataQuality =
    payload.keyword_data_quality && typeof payload.keyword_data_quality === "object"
      ? (payload.keyword_data_quality as {
          has_estimated_volumes?: boolean;
          estimated_count?: number;
          total?: number;
          warning?: string | null;
        })
      : null;

  const [deselected, setDeselected] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const item of kwRows(payload.deselected_keywords)) {
      const key = normKw(item.keyword || item);
      if (key) initial.add(key);
    }
    if (Array.isArray(payload.deselected_keywords)) {
      for (const item of payload.deselected_keywords) {
        const key = normKw(typeof item === "string" ? item : "");
        if (key) initial.add(key);
      }
    }
    return initial;
  });
  const [manualKeywords, setManualKeywords] = useState<ManualCompetitorKeyword[]>(() =>
    kwRows(payload.manual_competitor_keywords)
      .map((row) => ({
        keyword: String(row.keyword || "").trim(),
        competitor: String(row.competitor || "").trim(),
      }))
      .filter((row) => row.keyword),
  );
  const [intentFilter, setIntentFilter] = useState<IntentFilter>("all");
  const [manualKeyword, setManualKeyword] = useState("");
  const [manualCompetitor, setManualCompetitor] = useState("");

  function toggleFocus(keyword: string) {
    const key = normKw(keyword);
    if (!key) return;
    setDeselected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function addManualCompetitorKeyword() {
    const keyword = manualKeyword.trim();
    const competitor = manualCompetitor.trim();
    if (!keyword) return;
    const key = normKw(keyword);
    setManualKeywords((rows) => {
      if (rows.some((row) => normKw(row.keyword) === key)) return rows;
      return [...rows, { keyword, competitor }];
    });
    setDeselected((current) => {
      const next = new Set(current);
      next.delete(key);
      return next;
    });
    setManualKeyword("");
    setManualCompetitor("");
  }

  const keywordFocus: KeywordFocus = {
    focused: (keyword) => !deselected.has(normKw(keyword)),
    onToggle: toggleFocus,
    disabled: !canAct,
  };

  const focusEdits = {
    deselected_keywords: [...deselected],
    manual_competitor_keywords: manualKeywords,
  };

  return (
    <IntentFilterContext.Provider value={intentFilter}>
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Search Demand")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        Providers: {providers} · Keywords:{" "}
        {String(payload.keyword_count ?? keywordDataset.length ?? "—")} · Country:{" "}
        {String(payload.country || "us")}
        {competitorDomains.length
          ? ` · Competitors: ${competitorDomains.slice(0, 5).join(", ")}`
          : ""}
      </p>
      {dataQuality?.has_estimated_volumes ? (
        <div
          role="status"
          style={{
            margin: "10px 0 6px",
            padding: "10px 12px",
            borderRadius: 6,
            border: "1px solid var(--warning, #d29922)",
            background: "color-mix(in srgb, var(--warning, #d29922) 12%, transparent)",
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          <strong>Estimated search volumes.</strong>{" "}
          {dataQuality.warning ||
            `${dataQuality.estimated_count ?? 0} of ${dataQuality.total ?? 0} keywords carry estimated volume.`}
        </div>
      ) : null}
      {payload.note ? (
        <p style={{ fontSize: 13, marginTop: 4 }}>{String(payload.note)}</p>
      ) : null}

      {(funnelCounts || intentCounts) && (
        <div style={{ fontSize: 12, margin: "8px 0" }}>
          {funnelCounts ? (
            <p style={{ margin: "0 0 4px" }}>
              Funnel: TOFU {funnelCounts.TOFU ?? 0} · MOFU {funnelCounts.MOFU ?? 0} · BOFU{" "}
              {funnelCounts.BOFU ?? 0}
              {funnelBal?.balanced ? " · balanced" : ""}
            </p>
          ) : null}
          {intentCounts ? (
            <p style={{ margin: 0 }}>
              Intent: informational {intentCounts.informational ?? 0} · navigational{" "}
              {intentCounts.navigational ?? 0} · commercial {intentCounts.commercial ?? 0} ·
              transactional {intentCounts.transactional ?? 0}
              {intentBal?.balanced ? " · balanced" : ""}
            </p>
          ) : null}
        </div>
      )}
      {funnelWarnings.length || intentWarnings.length ? (
        <ul className="missing-list">
          {[...funnelWarnings, ...intentWarnings].slice(0, 4).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      {cleaning && cleaning.input_count != null ? (
        <details style={{ margin: "8px 0 12px", fontSize: 12 }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
            Cleaning: kept {String(cleaning.kept_count ?? "—")} of{" "}
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
                </li>
              ))}
            </ul>
          ) : null}
        </details>
      ) : null}

      <SubserviceCompetitorMatrix rows={subserviceMatrix} />

      {seedClusters.length > 0 || serviceClusters.length > 0 || clusterReport ? (
        <IntentFilterBar value={intentFilter} onChange={setIntentFilter} counts={intentCounts} />
      ) : null}

      {seedClusters.length > 0 || serviceClusters.length > 0 ? (
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
                ? ` · Roots: services ${Number(targetCounts.service ?? 0)}, subservices ${Number(
                    targetCounts.sub_service ?? 0,
                  )}, pages ${Number(targetCounts.page ?? 0)}, keywords ${Number(
                    targetCounts.keyword ?? 0,
                  )}`
                : ""}
              {seedingMeta?.seeds_with_all_classes !== undefined
                ? ` · All 4 classes: ${Number(seedingMeta.seeds_with_all_classes)}/${
                    seedClusters.length
                  } seeds`
                : ""}
            </p>
          ) : null}
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
            Open a service → seed to see every keyword with Search Demand Analysis
            fields (volume, CPC, competition, category, URL, status, funnel,
            secondaries, priority). Uncheck Focus to drop a term.
          </p>
          {serviceClusters.length ? (
            <ServiceSeedGroups
              services={serviceClusters}
              analysis={analysis}
              keywordFocus={keywordFocus}
            />
          ) : (
            <FlatSeedClusters
              seeds={seedClusters}
              analysis={analysis}
              keywordFocus={keywordFocus}
            />
          )}
        </>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          No multi-mode seed clusters in this report yet.
        </p>
      )}

      {evergreen.length + trends.length + avoid.length > 0 && (
        <section style={{ margin: "16px 0 12px" }}>
          <h4 style={{ marginBottom: 6 }}>Priority highlights</h4>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: 12,
            }}
          >
            <HighlightList title="Evergreen" rows={evergreen} />
            <HighlightList title="Trend plays" rows={trends} />
            <HighlightList title="Avoid" rows={avoid} showRationale />
          </div>
        </section>
      )}

      {clusterReport ? (
        <ClusterSection
          clusterReport={clusterReport}
          reportClusters={reportClusters}
          orphans={orphans}
          roadmap={roadmap}
          keywordFocus={keywordFocus}
        />
      ) : null}

      <section className="report-section" style={{ marginTop: 8 }}>
        <h4 style={{ marginBottom: 6 }}>Add a competitor keyword</h4>
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
          Add a term research missed. Drop terms via Focus in the seed tables.
          {deselected.size ? ` ${deselected.size} deselected.` : ""}
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
          <input
            placeholder="Competitor keyword"
            value={manualKeyword}
            disabled={!canAct}
            onChange={(e) => setManualKeyword(e.target.value)}
            style={{ flex: 2, minWidth: 160 }}
          />
          <input
            placeholder="Competitor name or domain"
            value={manualCompetitor}
            disabled={!canAct}
            onChange={(e) => setManualCompetitor(e.target.value)}
            style={{ flex: 2, minWidth: 160 }}
          />
          <button
            type="button"
            className="btn btn-ghost"
            disabled={!canAct || !manualKeyword.trim()}
            onClick={addManualCompetitorKeyword}
          >
            Add competitor keyword
          </button>
        </div>
        {manualKeywords.length ? (
          <ul className="missing-list">
            {manualKeywords.map((row) => {
              const focused = !deselected.has(normKw(row.keyword));
              return (
                <li key={normKw(row.keyword)} style={{ opacity: focused ? 1 : 0.45 }}>
                  <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={focused}
                      disabled={!canAct}
                      aria-label={`Focus ${row.keyword}`}
                      onChange={() => toggleFocus(row.keyword)}
                    />
                    <span>
                      <strong>{row.keyword}</strong>
                      {row.competitor ? ` · ${row.competitor}` : ""}
                      <span style={{ color: "var(--muted)" }}> · added manually</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      {canAct && (
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onAction("approve", focusEdits)}
          >
            Approve into memory
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
    </IntentFilterContext.Provider>
  );
}
