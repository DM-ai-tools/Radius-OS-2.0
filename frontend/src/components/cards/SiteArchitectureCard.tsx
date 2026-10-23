import { useMemo, useState } from "react";
import SiteMindTree, { type TreeRow } from "./SiteMindTree";
import UrlMapBoard from "./UrlMapBoard";
import { useStageFocus } from "../../lib/stageFocus";

type Props = {
  payload: Record<string, unknown>;
  canAct: boolean;
  onAction: (action: string, edits?: Record<string, unknown>) => void;
};

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

const PAGE_TYPE_STYLES: Record<
  string,
  { bg: string; fg: string; icon: string; label: string }
> = {
  home: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "⌂", label: "Home" },
  hub: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "◎", label: "Hub" },
  spoke: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "○", label: "Spoke" },
  pillar: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "▮", label: "Pillar" },
  cluster: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "○", label: "Cluster" },
  supporting: {
    bg: "var(--primary-bg)",
    fg: "var(--primary)",
    icon: "○",
    label: "Supporting",
  },
  service_hub: {
    bg: "var(--primary-bg)",
    fg: "var(--primary)",
    icon: "◎",
    label: "Service hub",
  },
  service: { bg: "var(--coral-bg)", fg: "var(--coral)", icon: "◆", label: "Service" },
  subservice: {
    bg: "var(--amber-bg)",
    fg: "var(--amber)",
    icon: "↳",
    label: "Subservice",
  },
  sub_service: {
    bg: "var(--amber-bg)",
    fg: "var(--amber)",
    icon: "↳",
    label: "Subservice",
  },
  blog: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "✎", label: "Blog" },
  article: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "✎", label: "Article" },
  guide: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "✎", label: "Guide" },
  listicle: { bg: "var(--primary-bg)", fg: "var(--primary)", icon: "☷", label: "Listicle" },
  tool: { bg: "var(--green-bg)", fg: "var(--green)", icon: "⚙", label: "Tool" },
  comparison: {
    bg: "var(--amber-bg)",
    fg: "var(--amber)",
    icon: "⇄",
    label: "Comparison",
  },
  product: { bg: "var(--coral-bg)", fg: "var(--coral)", icon: "◆", label: "Product" },
  commercial: {
    bg: "var(--coral-bg)",
    fg: "var(--coral)",
    icon: "◆",
    label: "Commercial",
  },
  landing: { bg: "var(--coral-bg)", fg: "var(--coral)", icon: "◆", label: "Landing" },
  location: { bg: "var(--amber-bg)", fg: "var(--amber)", icon: "⌖", label: "Location" },
  faq: { bg: "var(--green-bg)", fg: "var(--green)", icon: "?", label: "FAQ" },
  utility: { bg: "var(--line)", fg: "var(--muted)", icon: "•", label: "Utility" },
  planned: { bg: "var(--line)", fg: "var(--muted)", icon: "•", label: "Planned" },
  page: { bg: "var(--line)", fg: "var(--muted)", icon: "•", label: "Page" },
};

function PageTypeChip({ value }: { value: unknown }) {
  const type = String(value || "page").trim().toLowerCase();
  const style = PAGE_TYPE_STYLES[type] || {
    ...PAGE_TYPE_STYLES.page,
    label: type.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
  };
  return (
    <span
      className="cs-chip"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        background: style.bg,
        color: style.fg,
        whiteSpace: "nowrap",
      }}
    >
      <span aria-hidden="true">{style.icon}</span>
      {style.label}
    </span>
  );
}

function treeIndent(depth: unknown): number {
  const parsed = Number(depth);
  return Number.isFinite(parsed) ? Math.min(Math.max(parsed, 0), 6) * 16 : 0;
}

function resolveUrlColumns(row: Record<string, unknown>): { current: string | null; proposed: string | null } {
  const cur = row.current_url ? String(row.current_url).trim() : "";
  const prop = row.proposed_url ? String(row.proposed_url).trim() : "";
  if (cur && cur !== "/") return { current: cur, proposed: null };
  if (prop && prop !== "/") return { current: null, proposed: prop };
  const action = String(row.action || row.status || "").toUpperCase();
  const sel = row.selected_url ? String(row.selected_url).trim() : "";
  const create = row.create_url ? String(row.create_url).trim() : "";
  // Folds into a parent page that is not built yet: it lives on that new page.
  if (action === "FOLD_INTO_NEW_PARENT") {
    const target = row.target_url ? String(row.target_url).trim() : "";
    return { current: null, proposed: target || sel || null };
  }
  if (action.includes("CREATE") || action === "NEW") {
    return { current: null, proposed: create || sel || null };
  }
  return { current: sel || null, proposed: null };
}

const DISTINCT_PAGE =
  /^(?:how to|how do|how does|why |when to|should i|best |top )\b|\b(?:pricing|prices?|costs?|how much|quotes?|rates?|buy|buying|purchase)\b|\b(?:vs|versus)\b/i;
const LINE_MODIFIERS = [
  "near me",
  "gold coast",
  "companies",
  "company",
  "businesses",
  "business",
  "agencies",
  "agency",
  "services",
  "service",
  "consultants",
  "consultant",
  "consultancy",
  "firms",
  "firm",
  "providers",
  "provider",
  "specialists",
  "specialist",
  "experts",
  "expert",
  "freelancers",
  "freelancer",
  "australia",
  "australian",
  "sydney",
  "melbourne",
  "brisbane",
  "perth",
  "adelaide",
  "canberra",
  "hobart",
  "darwin",
  "nsw",
  "vic",
  "qld",
  "tas",
  "act",
  "online",
  "meaning",
  "definition",
  "defined",
].sort((a, b) => b.length - a.length);
const LINE_FILLER = new Set([
  "in",
  "for",
  "the",
  "a",
  "an",
  "and",
  "of",
  "to",
  "with",
  "is",
  "are",
  "your",
  "our",
]);
/** Offer name after commercial, city, and "meaning" words are removed. */
function publishableHead(keyword: string): string | null {
  const raw = keyword.toLowerCase().replace(/\s+/g, " ").trim();
  if (!raw || DISTINCT_PAGE.test(raw)) return null;
  let text = raw.replace(/search engine optimization/g, "search engine optimisation");
  text = text.replace(/^(?:what is|what are)\s+/, "");
  for (const mod of LINE_MODIFIERS) {
    const pattern = mod.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+");
    text = text.replace(new RegExp(`\\b${pattern}\\b`, "g"), " ");
  }
  let tokens = text.split(/\s+/).filter((token) => token && !LINE_FILLER.has(token));
  if (tokens.includes("marketing")) {
    const without = tokens.filter((token) => token !== "marketing");
    const left = without.join(" ");
    if (left === "seo" || left === "search engine optimisation" || left === "") tokens = without;
  }
  text = tokens.join(" ");
  text = text.replace(/optimization/g, "optimisation").replace(/organization/g, "organisation");
  return text || null;
}

function offerIndex(raw: unknown): Map<string, string> {
  const index = new Map<string, string>();
  if (!Array.isArray(raw)) return index;
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const record = row as Record<string, unknown>;
    const service = String(record.service || "").trim();
    if (!service) continue;
    const canon = publishableHead(service) || service.toLowerCase();
    const names = [service, ...(Array.isArray(record.suggested) ? record.suggested : []), ...(Array.isArray(record.added) ? record.added : [])];
    for (const name of names) {
      const line = publishableHead(String(name)) || String(name).toLowerCase().trim();
      if (line) index.set(line, canon);
    }
  }
  return index;
}

/** One row when the offer is the same line, such as SEO and search engine optimisation. */
function foldSameOffer(
  rows: Array<Record<string, unknown>>,
  synonyms: unknown,
): Array<Record<string, unknown>> {
  const index = offerIndex(synonyms);
  const groups = new Map<string, number[]>();
  rows.forEach((row, indexNo) => {
    const line =
      publishableHead(String(row.primary_keyword || row.keyword || "")) ||
      publishableHead(String(row.l1_category || ""));
    if (!line) return;
    const head = index.get(line) || line;
    const list = groups.get(head) || [];
    list.push(indexNo);
    groups.set(head, list);
  });
  const drop = new Set<number>();
  for (const indexes of groups.values()) {
    if (indexes.length < 2) continue;
    indexes.sort((a, b) => {
      const liveA = rows[a].current_url ? 1 : 0;
      const liveB = rows[b].current_url ? 1 : 0;
      if (liveA !== liveB) return liveB - liveA;
      return Number(rows[b].search_volume || 0) - Number(rows[a].search_volume || 0);
    });
    const owner = rows[indexes[0]];
    const seconds = Array.isArray(owner.secondary_keywords)
      ? owner.secondary_keywords.map((item) => String(item).trim()).filter(Boolean)
      : [];
    const seen = new Set(seconds.map((item) => item.toLowerCase()));
    const primary = String(owner.primary_keyword || "").trim().toLowerCase();
    if (primary) seen.add(primary);
    for (const index of indexes.slice(1)) {
      const other = String(rows[index].primary_keyword || rows[index].keyword || "").trim();
      if (other && !seen.has(other.toLowerCase())) {
        seconds.unshift(other);
        seen.add(other.toLowerCase());
      }
      const extras = Array.isArray(rows[index].secondary_keywords) ? rows[index].secondary_keywords : [];
      for (const extra of extras) {
        const text = String(extra).trim();
        if (text && !seen.has(text.toLowerCase())) {
          seconds.push(text);
          seen.add(text.toLowerCase());
        }
      }
      drop.add(index);
    }
    owner.secondary_keywords = seconds.slice(0, 10);
  }
  return rows.filter((_, index) => !drop.has(index));
}

function isServiceNode(row: Record<string, unknown>): boolean {
  const kind = String(row.page_type || row.type || "").toLowerCase();
  return kind === "service" || kind === "subservice" || kind === "sub_service";
}

function serviceLine(label: string): string {
  const head = publishableHead(label);
  if (head) return head;
  const raw = label.toLowerCase().replace(/\s+/g, " ").trim();
  if (raw === "seo" || raw === "search engine optimisation" || raw === "search engine optimization") {
    return "search engine optimisation";
  }
  return "";
}

/** Drop "near me" / city boxes when the service page is already in the tree. */
function foldModifierPages(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const homes = new Set<string>();
  for (const row of rows) {
    if (!isServiceNode(row)) continue;
    const line = serviceLine(String(row.title || row.primary_keyword || row.keyword || ""));
    if (line) homes.add(line);
  }
  return rows.filter((row) => {
    const label = String(row.title || row.primary_keyword || row.keyword || "");
    const head = publishableHead(label);
    if (!head || isServiceNode(row)) return true;
    return !homes.has(head);
  });
}

function normalizeUrlMapRow(row: Record<string, unknown>): Record<string, unknown> {
  const { current, proposed } = resolveUrlColumns(row);
  return {
    ...row,
    current_url: current,
    proposed_url: proposed,
    l3_subsubcategory: row.l3_subsubcategory ?? row.l3_sub_subcategory,
    search_volume: row.search_volume ?? row.search_volume_mo ?? row.volume,
    secondary_keywords_sheet:
      row.secondary_keywords_sheet ?? row.secondary_keywords,
  };
}

function collectUrlMapRows(payload: Record<string, unknown>): Array<Record<string, unknown>> {
  const report =
    payload.url_map_report && typeof payload.url_map_report === "object"
      ? (payload.url_map_report as Record<string, unknown>)
      : {};
  const fromReport = asRows(report.final_url_map);
  const fromTop = asRows(payload.final_url_map);
  const fromCategory = asRows(payload.category_url_mapping).filter((r) => r.row_type !== "section");
  const raw = fromReport.length ? fromReport : fromTop.length ? fromTop : fromCategory;
  const seeding =
    payload.keyword_seeding && typeof payload.keyword_seeding === "object"
      ? (payload.keyword_seeding as Record<string, unknown>)
      : {};
  const synonyms = payload.offer_synonyms || report.offer_synonyms || seeding.seed_synonyms;
  return foldSameOffer(raw.map(normalizeUrlMapRow), synonyms);
}

function strList(raw: unknown, n = 4): string {
  if (!Array.isArray(raw) || !raw.length) return "";
  return raw
    .slice(0, n)
    .map((x) =>
      typeof x === "string"
        ? x
        : String(
            (x as Record<string, unknown>)?.name ||
              (x as Record<string, unknown>)?.domain ||
              (x as Record<string, unknown>)?.url ||
              x,
          ),
    )
    .filter(Boolean)
    .join(", ");
}

export default function SiteArchitectureCard({ payload, canAct, onAction }: Props) {
  const focus = useStageFocus(payload);
  const current =
    payload.current_state && typeof payload.current_state === "object"
      ? (payload.current_state as Record<string, unknown>)
      : {};
  const issues = asRows(current.issues);
  const depth4 = asRows(current.pages_depth_4_plus);
  const orphanSample = asRows(current.orphan_sample);
  const phantomRaw = Array.isArray(current.phantom_sample) ? current.phantom_sample : [];
  const phantomSample = phantomRaw.map((p) =>
    typeof p === "string"
      ? { path: p }
      : p && typeof p === "object"
        ? (p as Record<string, unknown>)
        : { path: String(p) },
  );
  const evidenceNotes = Array.isArray(current.evidence_notes)
    ? (current.evidence_notes as string[])
    : [];
  const depthDist =
    current.click_depth_distribution && typeof current.click_depth_distribution === "object"
      ? (current.click_depth_distribution as Record<string, number>)
      : null;

  const urlMap = collectUrlMapRows(payload);
  const mapRows = urlMap;
  const keywordsByPath = useMemo(() => {
    const index = new Map<string, { primary: string; secondary: string }>();
    const pathKey = (raw: unknown) => String(raw || "").trim().replace(/\/+$/, "").toLowerCase();
    for (const row of mapRows) {
      const primary = String(row.primary_keyword || row.keyword || "").trim();
      const secondary = (Array.isArray(row.secondary_keywords) ? row.secondary_keywords : [])
        .map((item) =>
          typeof item === "string"
            ? item.trim()
            : String((item as Record<string, unknown>)?.keyword || "").trim(),
        )
        .filter(Boolean)
        .slice(0, 10)
        .join(", ");
      for (const raw of [row.current_url, row.proposed_url, row.selected_url, row.target_url, row.create_url]) {
        const key = pathKey(raw);
        if (key) index.set(key, { primary, secondary });
      }
    }
    return index;
  }, [mapRows]);
  const tree = useMemo(
    () => foldModifierPages(asRows(payload.target_url_tree)),
    [payload.target_url_tree],
  );
  const [treeView, setTreeView] = useState<"map" | "table">("map");
  const treeRows = useMemo(
    (): TreeRow[] =>
      tree.map((n) => ({
        path: String(n.path || n.url || "/"),
        title: n.title ? String(n.title) : null,
        kind: String(n.page_type || n.type || ""),
        parent: n.parent ? String(n.parent) : null,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [payload.target_url_tree],
  );
  const treeRoot = (() => {
    const raw = String(payload.primary_url || payload.domain || "");
    try {
      return raw ? new URL(/^https?:/i.test(raw) ? raw : `https://${raw}`).host.replace(/^www\./, "") : String(payload.client || "Home");
    } catch {
      return String(payload.client || "Home");
    }
  })();
  const ownership = asRows(payload.cluster_ownership);
  const nav =
    payload.navigation && typeof payload.navigation === "object"
      ? (payload.navigation as Record<string, unknown>)
      : {};
  const primaryNav = asRows(nav.primary_nav);
  const remediation = asRows(nav.depth_remediation);
  const breadcrumbs =
    nav.breadcrumb_pattern && typeof nav.breadcrumb_pattern === "object"
      ? (nav.breadcrumb_pattern as Record<string, unknown>)
      : null;
  const types = asRows(payload.page_type_model);
  const redirects = asRows(payload.redirect_map);
  const handoffs = asRows(payload.handoffs);
  const competitorSites = asRows(payload.competitor_sites);
  const competitorIa = asRows(payload.competitor_ia);
  const moneyPages = Array.isArray(payload.money_pages)
    ? (payload.money_pages as string[])
    : [];
  const competitorNames = Array.isArray(payload.competitor_names)
    ? (payload.competitor_names as string[])
    : [];
  const competitorDomains = Array.isArray(payload.competitor_domains)
    ? (payload.competitor_domains as string[])
    : [];
  const urlRules =
    payload.url_convention_rules && typeof payload.url_convention_rules === "object"
      ? (payload.url_convention_rules as Record<string, unknown>)
      : null;
  const rollout =
    payload.rollout_plan && typeof payload.rollout_plan === "object"
      ? (payload.rollout_plan as Record<string, unknown>)
      : null;
  const rolloutSeq = Array.isArray(rollout?.sequence) ? (rollout!.sequence as string[]) : [];
  const riskFlags = Array.isArray(rollout?.high_risk_flags)
    ? (rollout!.high_risk_flags as string[])
    : [];
  const geo =
    payload.geographic_focus || payload.location_name
      ? [payload.geographic_focus, payload.location_name].filter(Boolean).map(String).join(" · ")
      : "";

  const depthDistBits = depthDist
    ? Object.entries(depthDist)
        .sort(([a], [b]) => Number(a) - Number(b))
        .map(([d, c]) => `d${d}: ${c}`)
        .join(" · ")
    : "";

  return (
    <div className="structured-card structured-card--report">
      <h3 className="card-title">{String(payload.title || "Site Architecture Blueprint")}</h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 0 }}>
        {payload.client ? `${String(payload.client)} · ` : ""}
        {payload.primary_url || payload.domain ? String(payload.primary_url || payload.domain) : ""}
        {geo ? ` · Geo: ${geo}` : ""}
        {competitorNames.length || competitorDomains.length
          ? ` · Competitors: ${(competitorNames.length ? competitorNames : competitorDomains)
              .slice(0, 5)
              .join(", ")}`
          : ""}
      </p>

      <UrlMapBoard
        rows={mapRows}
        canAct={canAct}
        focused={focus.focused}
        onToggle={focus.toggle}
      />

      <div className="cs-funnel-strip">
        <span>
          URLs <strong>{fmtNum(current.urls_crawled)}</strong>
        </span>
        <span>
          200s <strong>{fmtNum(current.status_200)}</strong>
        </span>
        <span>
          Max depth <strong>{fmtNum(current.max_click_depth)}</strong>
        </span>
        <span>
          ≤3 clicks <strong>{fmtNum(current.within_3_clicks)}</strong>
        </span>
        <span>
          Depth 4+ <strong>{fmtNum(current.depth_4_plus)}</strong>
        </span>
        <span>
          Orphans <strong>{fmtNum(current.orphans)}</strong>
        </span>
        <span>
          Phantoms <strong>{fmtNum(current.phantom_dirs)}</strong>
        </span>
        {payload.gate ? <span className="cs-badge">Gate</span> : null}
      </div>
      {depthDistBits ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
          Depth distribution: {depthDistBits}
        </p>
      ) : null}
      {payload.gate ? (
        <p style={{ fontSize: 12, fontWeight: 600, marginTop: 4 }}>{String(payload.gate)}</p>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Current-state issues</h4>
      {issues.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Issue</th>
                <th style={{ padding: "6px 8px" }}>Count</th>
                <th style={{ padding: "6px 8px" }}>Severity</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((i, idx) => (
                <tr key={idx} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(i.issue || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(i.count)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="cs-chip">{String(i.severity || "—")}</span>
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(i.note || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>No structural issues flagged</p>
      )}

      {depth4.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Pages beyond 3 clicks</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                  <th style={{ padding: "6px 8px" }}>Title</th>
                  <th style={{ padding: "6px 8px" }}>Depth</th>
                </tr>
              </thead>
              <tbody>
                {depth4.slice(0, 15).map((p, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(p.url || p.path || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(p.title || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtNum(p.depth)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {orphanSample.length || phantomSample.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Orphans & phantom directories</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>Kind</th>
                  <th style={{ padding: "6px 8px" }}>URL / path</th>
                  <th style={{ padding: "6px 8px" }}>Title / note</th>
                </tr>
              </thead>
              <tbody>
                {orphanSample.slice(0, 10).map((p, i) => (
                  <tr key={`o-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px" }}>
                      <span className="cs-chip">orphan</span>
                    </td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(p.url || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(p.title || "—")}</td>
                  </tr>
                ))}
                {phantomSample.slice(0, 10).map((p, i) => (
                  <tr key={`ph-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px" }}>
                      <span className="cs-chip">phantom</span>
                    </td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>
                      {String(p.path || p.url || "—")}
                    </td>
                    <td style={{ padding: "6px 8px", color: "var(--muted)" }}>
                      {p.note ? String(p.note) : "Missing hub at path"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {evidenceNotes.length ? (
        <ul className="missing-list" style={{ marginBottom: 14 }}>
          {evidenceNotes.slice(0, 6).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Page-type model</h4>
      {types.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Type</th>
                <th style={{ padding: "6px 8px" }}>URL pattern</th>
                <th style={{ padding: "6px 8px" }}>Parent</th>
                <th style={{ padding: "6px 8px" }}>Breadcrumb</th>
                <th style={{ padding: "6px 8px" }}>Indexable</th>
                <th style={{ padding: "6px 8px" }}>Count</th>
              </tr>
            </thead>
            <tbody>
              {types.map((t, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(t.type || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(t.url_pattern || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{String(t.parent || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(t.breadcrumb || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {t.indexable === true ? "Yes" : t.indexable === false ? "No" : "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(t.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}

      {moneyPages.length ? (
        <p style={{ fontSize: 13, marginTop: 0 }}>
          <strong>Money pages:</strong> {moneyPages.slice(0, 10).join(" · ")}
        </p>
      ) : null}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
        <h4 style={{ margin: 0 }}>Target URL tree</h4>
        {tree.length ? (
          <span className="dp-seg" role="group" aria-label="URL tree view">
            <button type="button" aria-pressed={treeView === "map"} onClick={() => setTreeView("map")}>
              Map
            </button>
            <button type="button" aria-pressed={treeView === "table"} onClick={() => setTreeView("table")}>
              Table
            </button>
          </span>
        ) : null}
      </div>
      {tree.length && treeView === "map" ? <SiteMindTree rows={treeRows} rootLabel={treeRoot} /> : null}
      {tree.length && treeView === "table" ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Type</th>
                <th style={{ padding: "6px 8px" }}>Path</th>
                <th style={{ padding: "6px 8px" }}>Depth</th>
                <th style={{ padding: "6px 8px" }}>Primary Keyword</th>
                <th style={{ padding: "6px 8px" }}>Secondary Keywords</th>
                <th style={{ padding: "6px 8px" }}>Cluster</th>
                <th style={{ padding: "6px 8px" }}>Parent</th>
                <th style={{ padding: "6px 8px" }}>Absolute URL</th>
              </tr>
            </thead>
            <tbody>
              {tree.slice(0, 40).map((n, i) => (
                <tr key={`${String(n.path)}-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px" }}>
                    <PageTypeChip value={n.page_type || n.type} />
                  </td>
                  <td
                    style={{
                      padding: "6px 8px",
                      paddingLeft: 8 + treeIndent(n.depth),
                      fontWeight: 600,
                      fontSize: 11,
                    }}
                  >
                    {treeIndent(n.depth) > 0 ? (
                      <span aria-hidden="true" style={{ color: "var(--muted)", marginRight: 5 }}>
                        └
                      </span>
                    ) : null}
                    {String(n.path || n.url || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(n.depth)}</td>
                  <td style={{ padding: "6px 8px" }}>
                    {String(
                      n.primary_keyword ||
                        n.keyword ||
                        keywordsByPath.get(String(n.path || n.url || "").trim().replace(/\/+$/, "").toLowerCase())?.primary ||
                        "—",
                    )}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {(() => {
                      const listed = Array.isArray(n.secondary_keywords)
                        ? n.secondary_keywords.map((item) => String(item).trim()).filter(Boolean).join(", ")
                        : "";
                      return (
                        listed ||
                        keywordsByPath.get(String(n.path || n.url || "").trim().replace(/\/+$/, "").toLowerCase())?.secondary ||
                        "—"
                      );
                    })()}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(n.cluster || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(n.parent || "—")}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(n.absolute_url || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {tree.length > 40 ? (
            <p style={{ fontSize: 11, color: "var(--muted)" }}>+{tree.length - 40} more URLs in payload</p>
          ) : null}
        </div>
      ) : null}
      {!tree.length ? <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p> : null}

      <h4 style={{ marginBottom: 8 }}>Primary navigation</h4>
      {primaryNav.length ? (
        <div style={{ overflowX: "auto", marginBottom: 10 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Label</th>
                <th style={{ padding: "6px 8px" }}>URL</th>
                <th style={{ padding: "6px 8px" }}>Depth</th>
              </tr>
            </thead>
            <tbody>
              {primaryNav.map((n, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(n.label || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(n.url || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(n.depth)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}
      {nav.rule ? (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>{String(nav.rule)}</p>
      ) : null}
      {nav.notes ? (
        <p style={{ fontSize: 13, marginTop: 4 }}>{String(nav.notes)}</p>
      ) : null}
      {breadcrumbs ? (
        <p style={{ fontSize: 12, marginTop: 4 }}>
          <strong>Breadcrumbs:</strong>{" "}
          {Object.entries(breadcrumbs)
            .map(([k, v]) => `${k}: ${String(v)}`)
            .join(" · ")}
        </p>
      ) : null}

      {remediation.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Depth remediation</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>URL</th>
                  <th style={{ padding: "6px 8px" }}>Current</th>
                  <th style={{ padding: "6px 8px" }}>Target</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {remediation.slice(0, 20).map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.url || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtNum(r.current_depth)}</td>
                    <td style={{ padding: "6px 8px" }}>{fmtNum(r.target_depth)}</td>
                    <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.action || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <h4 style={{ marginBottom: 8 }}>Cluster ownership</h4>
      {ownership.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Cluster</th>
                <th style={{ padding: "6px 8px" }}>Canonical owner</th>
                <th style={{ padding: "6px 8px" }}>Disposition</th>
                <th style={{ padding: "6px 8px" }}>Competing URLs</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {ownership.slice(0, 25).map((o, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(o.cluster || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {String(o.canonical_owner_url || o.owner_url || o.url || "—")}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{String(o.disposition || "keep")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>
                    {strList(o.competing_urls, 3) || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(o.note || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>—</p>
      )}

      <h4 style={{ marginBottom: 8 }}>Redirect map</h4>
      {redirects.length ? (
        <div style={{ overflowX: "auto", marginBottom: 14 }}>
          <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Old URL</th>
                <th style={{ padding: "6px 8px" }}>New URL</th>
                <th style={{ padding: "6px 8px" }}>Code</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {redirects.slice(0, 20).map((r, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.old_url || "—")}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11 }}>{String(r.new_url || "—")}</td>
                  <td style={{ padding: "6px 8px" }}>{fmtNum(r.code || 301)}</td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--muted)" }}>
                    {String(r.note || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14 }}>
          No URL moves proposed — prefer nav/hub fixes over restructures (Google doesn&apos;t count
          slashes).
        </p>
      )}

      {urlRules ? (
        <>
          <h4 style={{ marginBottom: 8 }}>URL convention rules</h4>
          <ul className="missing-list" style={{ marginBottom: 14 }}>
            {Object.entries(urlRules)
              .filter(([k, v]) => k !== "references" && typeof v === "string")
              .map(([k, v]) => (
                <li key={k}>
                  <strong>{k.replace(/_/g, " ")}:</strong> {String(v)}
                </li>
              ))}
          </ul>
        </>
      ) : null}

      {(competitorIa.length || competitorSites.length) ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Competitor IA / site snapshots</h4>
          {(competitorIa.length ? competitorIa : competitorSites).slice(0, 6).map((s, i) => {
            const hubs = Array.isArray(s.hub_paths) ? (s.hub_paths as string[]) : [];
            const examples = asRows(s.blog_examples);
            const blogs = examples.length ? examples : asRows(s.blogs);
            const pages = blogs.length ? blogs : asRows(s.pages);
            return (
              <div key={i} className="cs-pillar-block">
                <strong>
                  {String(s.name || s.domain || "Competitor")}
                  {s.domain ? ` · ${String(s.domain)}` : ""}
                </strong>
                <p className="cs-meta" style={{ margin: "4px 0" }}>
                  {s.page_count != null ? `Pages: ${String(s.page_count)}` : ""}
                  {s.blog_count != null ? ` · Blogs: ${String(s.blog_count)}` : ""}
                  {hubs.length ? ` · Hubs: ${hubs.slice(0, 6).join(", ")}` : ""}
                </p>
                {pages.length ? (
                  <ul className="missing-list" style={{ marginTop: 4 }}>
                    {pages.slice(0, 4).map((b, j) => (
                      <li key={j}>
                        {String(b.title || b.path || b.url || "—")}
                        {(b.path || b.url) && b.title ? (
                          <span style={{ color: "var(--muted)" }}>
                            {" "}
                            — {String(b.path || b.url)}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </>
      ) : null}

      {rollout ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Rollout plan</h4>
          {rolloutSeq.length ? (
            <ol className="missing-list" style={{ marginBottom: 8, paddingLeft: 18 }}>
              {rolloutSeq.map((step, i) => (
                <li key={i} style={{ listStyle: "decimal", marginBottom: 4 }}>
                  {step}
                </li>
              ))}
            </ol>
          ) : null}
          <ul className="missing-list">
            {rollout.freeze_window ? (
              <li>
                <strong>Freeze:</strong> {String(rollout.freeze_window)}
              </li>
            ) : null}
            {rollout.rollback_trigger ? (
              <li>
                <strong>Rollback:</strong> {String(rollout.rollback_trigger)}
              </li>
            ) : null}
            {riskFlags.length ? (
              <li>
                <strong>Risk flags:</strong> {riskFlags.join(" · ")}
              </li>
            ) : null}
          </ul>
        </>
      ) : null}

      {handoffs.length ? (
        <>
          <h4 style={{ marginBottom: 8 }}>Handoffs</h4>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                  <th style={{ padding: "6px 8px" }}>Deliverable</th>
                  <th style={{ padding: "6px 8px" }}>Receiving phase / owner</th>
                </tr>
              </thead>
              <tbody>
                {handoffs.map((h, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                    <td style={{ padding: "6px 8px", fontWeight: 600 }}>{String(h.item || "—")}</td>
                    <td style={{ padding: "6px 8px" }}>{String(h.receiving || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {canAct && (
        <div className="card-actions">
          <button type="button" className="btn btn-primary" onClick={() => onAction("approve", focus.edits)}>
            Approve architecture
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onAction("reject")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
