/** Page titles from the headline framework.

Mirrors `mapped_page_title` / `title_for_map_row` in
backend/app/services/headline_framework.py. The keyword stays in the title.
A question promises a clear guide, a how-to keeps its shape, a commercial
page promises a clear path, and a guide promises a practical guide.
No statistics, authorities, or outcomes are added.
*/

const ACRONYMS = new Set([
  "seo", "sem", "ppc", "crm", "b2b", "b2c", "ai", "api", "ux", "ui", "roi", "kpi",
  "cms", "saas", "ecommerce", "e-commerce", "aeo", "geo", "cro", "llm",
]);

const SMALL = new Set(["a", "an", "the", "to", "for", "of", "and", "or", "in", "on", "with", "vs", "versus"]);

export function phraseTitle(keyword: string): string {
  const parts: string[] = [];
  for (const word of keyword.trim().replace(/\s+/g, " ").split(" ")) {
    if (!word) continue;
    const low = word.toLowerCase();
    if (ACRONYMS.has(low) || ACRONYMS.has(low.replace(/-/g, ""))) {
      parts.push(low.includes("-") ? low.split("-").map((part) => part.toUpperCase()).join("-") : low.toUpperCase());
    } else if (SMALL.has(low) && parts.length) {
      parts.push(low === "versus" ? "vs" : low);
    } else {
      parts.push(word.length > 1 ? word[0].toUpperCase() + word.slice(1) : word.toUpperCase());
    }
  }
  return parts.join(" ") || "Untitled";
}

function cap(title: string, limit = 60): string {
  const clean = title.replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  return clean.slice(0, limit - 1).replace(/[-:|,]\s*$/, "").trimEnd() + "…";
}

export function mappedPageTitle(keyword: string, pageType: string): string {
  const phrase = phraseTitle(keyword);
  const low = phrase.toLowerCase();
  if (!keyword.trim()) return "Untitled";
  if (low.startsWith("what is ")) return cap(`${phrase}? A Clear Guide`);
  if (low.startsWith("how to ") || low.includes(" vs ") || low.startsWith("best ") || low.startsWith("top ")) {
    return cap(phrase);
  }
  const kind = pageType.toLowerCase();
  if (kind === "guide" || kind === "blog") return cap(`The Practical Guide to ${phrase}`);
  if (kind === "comparison") return cap(`${phrase}: Which Fits Your Situation?`);
  return cap(`A Clear Path To ${phrase}`);
}

export function titleForMapRow(row: Record<string, unknown>): string {
  const keyword = String(row.primary_keyword || row.keyword || row.l1_category || "").trim();
  const label = String(row.page_type || "").trim().toLowerCase();
  const job = String(row.page_job || "");
  let kind = "service";
  if (label === "blog" || (job === "draft_guide" && label === "blog")) kind = "blog";
  else if (job === "draft_guide" || label === "guide") kind = "guide";
  return mappedPageTitle(keyword, kind);
}
