import { useMemo, useState } from "react";
import { titleForMapRow } from "../../lib/headline";

type Row = Record<string, unknown>;

type Page = {
  key: string;
  group: string;
  level: string;
  l1: string;
  l2: string;
  l3: string;
  l4: string;
  primary: string;
  title: string;
  keywords: string[];
  currentUrl: string;
  proposedUrl: string;
  status: "LIVE" | "NEW";
  volume: string;
  cpc: string;
  combined: string;
  pageType: string;
  priority: string;
  notes: string;
  products: string;
  inScope: string;
  source: Row;
};

type Props = {
  rows: Row[];
  canAct: boolean;
  focused: (row: unknown) => boolean;
  onToggle: (row: unknown) => void;
};

function keywordList(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (item && typeof item === "object") return String((item as Row).keyword || "").trim();
        return "";
      })
      .filter(Boolean);
  }
  if (typeof raw === "string" && raw.trim() && raw !== "—") {
    return raw
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function cleanPath(raw: unknown): string {
  const text = String(raw || "").trim();
  if (!text || text === "/") return "";
  return text;
}

/** Workbook rule: a live page fills Current URL only. A new page fills Proposed URL only. */
function urlColumns(row: Row): { current: string; proposed: string } {
  const current = cleanPath(row.current_url);
  const proposed = cleanPath(row.proposed_url);
  if (current) return { current, proposed: "" };
  if (proposed) return { current: "", proposed };
  const action = String(row.action || "").toUpperCase();
  const selected = cleanPath(row.selected_url);
  const create = cleanPath(row.create_url);
  if (action.includes("CREATE")) return { current: "", proposed: create || selected };
  // Folds into a parent page that is not built yet: it lives on that new page.
  if (action === "FOLD_INTO_NEW_PARENT") return { current: "", proposed: cleanPath(row.target_url) || selected };
  return { current: selected, proposed: "" };
}

function levelOf(row: Row): string {
  const raw = String(row.level || "").trim();
  if (!raw) return "";
  if (/^l\d$/i.test(raw)) return raw.toUpperCase();
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? `L${n}` : raw;
}

function categoryName(row: Row): string {
  const parts = [row.l2_subcategory, row.l3_subsubcategory || row.l3_sub_subcategory, row.l4_attribution]
    .map((part) => String(part || "").trim())
    .filter(Boolean);
  if (parts.length) return parts.join(" / ");
  return String(row.l1_category || row.cluster || row.primary_keyword || "Page").trim();
}

function measured(raw: unknown): string {
  if (raw == null || raw === "") return "";
  const n = Number(raw);
  if (!Number.isFinite(n)) return String(raw);
  return n.toLocaleString();
}

function text(value: unknown): string {
  return value == null || value === "" ? "" : String(value);
}

const OUTCOME: Record<string, string> = {
  OPTIMIZE_EXISTING: "Optimize existing page",
  REVIEW_MERGE_REDIRECT: "Review / merge / redirect",
  CREATE: "Create new page",
  FOLD_INTO_NEW_PARENT: "Fold into new parent page",
};
const URL_STATUS: Record<string, string> = {
  existing_url: "existing page owns this",
  existing_url_needs_optimization: "existing page needs optimizing",
  existing_url_needs_consolidation: "competing pages to consolidate",
  new_proposed_url: "new URL proposed",
  no_dedicated_url: "no page of its own",
  fold_into_new_parent: "parent page not built yet",
};

function outcomeText(row: Row): string {
  const action = OUTCOME[text(row.action)] || text(row.action) || "—";
  const status = URL_STATUS[text(row.url_status)];
  let line = status ? `${action} (${status})` : action;
  const errors = Array.isArray(row.consistency_errors) ? row.consistency_errors.map(String) : [];
  if (errors.length) line += `. Rejected: ${errors.join(" ")}`;
  return line;
}

function scoreText(row: Row): string {
  const basis = (row.score_basis && typeof row.score_basis === "object" ? row.score_basis : {}) as Row;
  const available = Array.isArray(basis.available) ? basis.available.map(String) : [];
  const missing = Array.isArray(basis.unavailable) ? basis.unavailable.map(String) : [];
  const score = typeof row.url_score === "number" ? `${row.url_score}/100 ${text(row.score_band)}`.trim() : "—";
  const parts = [score];
  if (row.band_source === "prior_classification") parts[0] += " (from prior classification)";
  if (row.band_source === "no_match") return `${parts[0]}. No live page matched.`;
  if (available.length) parts.push(`based on ${available.join(", ")}`);
  if (missing.length) parts.push(`not available: ${missing.join(", ")}`);
  return `${parts.join("; ")}.`;
}

function confidenceText(row: Row): string {
  const bits: string[] = [];
  if (typeof row.match_confidence === "number") bits.push(`match ${Math.round(row.match_confidence * 100)}%`);
  if (typeof row.signal_coverage === "number") bits.push(`on ${Math.round(row.signal_coverage * 100)}% of signals`);
  if (typeof row.topic_confidence === "number") bits.push(`topic ${Math.round(row.topic_confidence * 100)}%`);
  return bits.join(", ") || "—";
}

function checksText(row: Row): string {
  const bits: string[] = [];
  const hygiene = (row.url_hygiene && typeof row.url_hygiene === "object" ? row.url_hygiene : {}) as Row;
  if (hygiene.verdict === "review") bits.push(`URL: ${(Array.isArray(hygiene.problems) ? hygiene.problems : []).join(" ")}`);
  else if (hygiene.verdict === "ok") bits.push("URL hygiene OK.");
  if (Array.isArray(row.collisions)) bits.push(...row.collisions.map(String));
  if (Array.isArray(row.canonical_conflicts)) bits.push(...row.canonical_conflicts.map((note) => `Canonical: ${note}`));
  if (Array.isArray(row.link_warnings)) bits.push(...row.link_warnings.map(String));
  return bits.join(" ") || "No conflicts.";
}

function linksText(row: Row): string {
  const bits: string[] = [];
  if (Array.isArray(row.redirect_sources) && row.redirect_sources.length) {
    bits.push(`Redirect into target: ${row.redirect_sources.slice(0, 4).join(", ")}`);
  }
  if (Array.isArray(row.internal_link_parents) && row.internal_link_parents.length) {
    bits.push(`Linked from: ${row.internal_link_parents.slice(0, 3).join(", ")}`);
  }
  if (row.locale) bits.push(`Locale ${row.locale}, hreflang group ${text(row.hreflang_group)}`);
  else if (row.market) bits.push(`Market: ${row.market}`);
  return bits.join("; ") || "—";
}

function trailText(row: Row): string {
  const steps = (Array.isArray(row.decision_trail) ? row.decision_trail : [])
    .map(String)
    .filter((step) => step && !step.startsWith("Priority "));
  if (!steps.length) return "—";
  const shown = steps.slice(0, 5).map((step, index) => `${index + 1}. ${step}`);
  if (steps.length > 5) shown.push(`+${steps.length - 5} more steps`);
  return shown.join(" ");
}

function hasDecision(row: Row): boolean {
  return Boolean(row.decision_trail || row.score_basis || row.band_source);
}

function clusterKeywords(row: Row, primary: string): string[] {
  const primaryKey = primary.toLowerCase();
  const members = Array.isArray(row.secondary_keywords)
    ? keywordList(row.secondary_keywords)
    : keywordList(row.secondary_keywords_sheet);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const word of members) {
    const key = word.toLowerCase();
    if (!key || key === primaryKey || seen.has(key)) continue;
    seen.add(key);
    out.push(word);
  }
  return out;
}

function buildPages(rows: Row[]): Page[] {
  const pages: Page[] = [];
  rows.forEach((row, index) => {
    if (row.row_type === "section") return;
    const primary = String(row.primary_keyword || row.keyword || "").trim();
    const { current, proposed } = urlColumns(row);
    const status: "LIVE" | "NEW" = current ? "LIVE" : "NEW";
    const group = String(row.l1_category || row.cluster || "Other").trim() || "Other";
    pages.push({
      key: `${group}:${primary || categoryName(row)}:${index}`,
      group,
      level: levelOf(row),
      l1: group,
      l2: String(row.l2_subcategory || "").trim(),
      l3: String(row.l3_subsubcategory || row.l3_sub_subcategory || "").trim(),
      l4: String(row.l4_attribution || "").trim(),
      primary,
      title: titleForMapRow(row),
      keywords: clusterKeywords(row, primary),
      currentUrl: current,
      proposedUrl: proposed,
      status,
      volume: measured(row.search_volume ?? row.search_volume_mo ?? row.volume),
      cpc: measured(row.cpc),
      combined: measured(row.combined_cluster_volume),
      pageType: String(row.page_type || "").trim(),
      priority: String(row.priority || "").trim(),
      notes: String(row.notes || "").trim(),
      products: measured(row.est_products),
      inScope: String(row.in_scope || "").trim(),
      source: row,
    });
  });
  return pages;
}

export default function UrlMapBoard({ rows, canAct, focused, onToggle }: Props) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"all" | "new" | "live">("all");
  const pages = useMemo(() => buildPages(rows), [rows]);
  const needle = query.trim().toLowerCase();
  const visible = pages.filter((page) => {
    if (mode === "live" && page.status !== "LIVE") return false;
    if (mode === "new" && page.status !== "NEW") return false;
    if (!needle) return true;
    const hay = `${page.group} ${page.l1} ${page.l2} ${page.primary} ${page.title} ${page.keywords.join(" ")} ${page.currentUrl} ${page.proposedUrl}`.toLowerCase();
    return hay.includes(needle);
  });
  if (!pages.length) {
    return <p className="umap-empty">No pages mapped yet. Run site architecture after keyword research.</p>;
  }

  const taxonomy = ["#", "Lvl", "L1", "L2", "L3", "L4", "Primary Keyword", "Title", "Secondary Keywords", "Current URL", "Proposed URL", "Status", "Page Type", "Priority"];
  const decision = ["#", "Outcome", "Score and basis", "Confidence", "Effort", "Checks", "Links and redirects", "Decision trail"];
  const decided = visible.filter((page) => hasDecision(page.source));

  return (
    <section className="umap">
      <div className="umap-bar">
        <p className="umap-note">{visible.length} of {pages.length} rows</p>
        <div className="umap-tools">
          <label className="umap-search">
            <span className="umap-sr">Find a page or keyword</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find a page or keyword"
            />
          </label>
          <div className="umap-modes" role="group" aria-label="Page status">
            {(
              [
                ["all", "All"],
                ["new", "NEW"],
                ["live", "LIVE"],
              ] as const
            ).map(([id, label]) => (
              <button key={id} type="button" aria-pressed={mode === id} onClick={() => setMode(id)}>
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {visible.length ? (
        <>
          <h3 className="umap-subtitle">Taxonomy & status</h3>
          <div className="umap-scroll">
            <table className="umap-table">
              <thead>
                <tr>
                  <th>Focus</th>
                  {taxonomy.map((header) => <th key={header}>{header}</th>)}
                </tr>
              </thead>
              <tbody>
                {visible.map((page, index) => {
                  const on = focused(page.source);
                  const number = String(index + 1);
                  const cells = [number, page.level, page.l1, page.l2, page.l3, page.l4, page.primary, page.title, page.keywords.join(", "), page.currentUrl, page.proposedUrl, page.status, page.pageType, page.priority];
                  return (
                    <tr key={page.key} className={on ? undefined : "is-off"}>
                      <td>
                        <input type="checkbox" checked={on} disabled={!canAct} aria-label={`Include ${page.primary || page.l1}`} onChange={() => onToggle(page.source)} />
                      </td>
                      {cells.map((value, cell) => (
                        <td key={taxonomy[cell]} className={cell === 6 || cell === 8 ? "umap-keywords" : cell === 7 ? "umap-title" : cell === 9 || cell === 10 ? "umap-url" : undefined}>{value || "—"}</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {decided.length ? (
            <>
              <h3 className="umap-subtitle">Decision & evidence</h3>
              <div className="umap-scroll">
                <table className="umap-table">
                  <thead>
                    <tr>{decision.map((header) => <th key={header}>{header}</th>)}</tr>
                  </thead>
                  <tbody>
                    {decided.map((page, index) => {
                      const number = String(index + 1);
                      const effort = text(page.source.effort) || "—";
                      const cells = [
                        number,
                        outcomeText(page.source),
                        scoreText(page.source),
                        confidenceText(page.source),
                        page.source.effort_basis ? `${effort}: ${page.source.effort_basis}` : effort,
                        checksText(page.source),
                        linksText(page.source),
                        trailText(page.source),
                      ];
                      return (
                        <tr key={page.key}>
                          {cells.map((value, cell) => <td key={decision[cell]}>{value || "—"}</td>)}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </>
      ) : (
        <p className="umap-empty">No pages match that.</p>
      )}
    </section>
  );
}
