/** Rotating status lines while an agent turn is in flight — Claude-like progress copy. */
export function statusLinesForPrompt(prompt: string): string[] {
  const p = prompt.toLowerCase();
  if (
    p.includes("track") ||
    p.includes("ga4") ||
    p.includes("gtm") ||
    p.includes("oauth") ||
    p.includes("known change")
  ) {
    return [
      "Verifying platform access (T1)…",
      "Auditing tags and conversions (T2–T3)…",
      "Extracting historical baseline (T4)…",
      "Preparing known-changes & readiness (T5–T6)…",
    ];
  }
  if (
    p.includes("keyword research") ||
    p.includes("search demand") ||
    p.includes("create topic") ||
    p.includes("cluster") ||
    p.includes("evergreen")
  ) {
    return [
      "Seeding topics from Discovery & competitors…",
      "Pulling Ahrefs + DataForSEO keyword metrics…",
      "Scoring best / evergreen / trend opportunities…",
    ];
  }
  if (
    p.includes("site architecture") ||
    p.includes("click depth") ||
    p.includes("url hierarchy") ||
    p.includes("information architecture")
  ) {
    return [
      "Measuring click depth from the homepage…",
      "Mapping hubs and URL hierarchy…",
      "Assigning cluster ownership…",
    ];
  }
  if (
    p.includes("content strategy") ||
    p.includes("seo strategy") ||
    p.includes("content calendar") ||
    p.includes("phase 6")
  ) {
    return [
      "Reading Phase 5 opportunities…",
      "Mapping pillars and priority queue…",
      "Building the 12-week calendar…",
    ];
  }
  if (p.includes("technical seo") || p.includes("phase 7")) {
    return [
      "Auditing crawlability and indexation…",
      "Checking broken links and IA handoffs…",
      "Building the technical SEO backlog…",
    ];
  }
  if (p.includes("content audit") || p.includes("existing content") || p.includes("phase 8")) {
    return [
      "Inventorying live and IA pages…",
      "Assigning keep / refresh / retire dispositions…",
      "Flagging cannibalization clusters…",
    ];
  }
  if (p.includes("content planning") || p.includes("page roadmap") || p.includes("phase 9")) {
    return [
      "Locking the page roadmap from IA + strategy…",
      "Assigning funnel and create/refresh actions…",
      "Sequencing waves for production…",
    ];
  }
  if (p.includes("write the full draft") || p.includes("write the content for")) {
    return [
      "Opening the selected brief…",
      "Drafting a topic-specific article from the brief…",
      "Generating strategy images with Nano Banana…",
      "Assembling the full preview for review…",
    ];
  }
  if (
    p.includes("content production") ||
    p.includes("content brief") ||
    p.includes("draft stub") ||
    p.includes("phase 10")
  ) {
    return [
      "Writing briefs for top-priority roadmap pages…",
      "Preparing clickable topic choices…",
      "Waiting for you to pick one page to draft…",
    ];
  }
  if (p.includes("on-page") || p.includes("on page") || p.includes("schema") || p.includes("phase 11")) {
    return [
      "Optimizing titles, H1s, and meta…",
      "Building schema JSON-LD packages…",
      "Suggesting internal links…",
    ];
  }
  if (p.includes("publish") || p.includes("indexnow") || p.includes("phase 12")) {
    return [
      "Building the mock publish queue…",
      "Previewing IndexNow + GSC recrawl…",
      "Assembling the QA checklist…",
    ];
  }
  if (p.includes("competitor") || p.includes("landscape") || p.includes("keyword gap")) {
    return [
      "Mapping competitor set…",
      "Pulling ranking and authority signals…",
      "Scoring threat tiers…",
    ];
  }
  if (
    p.includes("broken") ||
    p.includes("seo audit") ||
    p.includes("website") ||
    p.includes("crawl")
  ) {
    return [
      "Fetching live pages…",
      "Running crawl and SEO checks…",
      "Assembling the website report…",
    ];
  }
  if (p.includes("discover") || p.includes("onboard") || p.includes("questionnaire")) {
    return [
      "Researching the public footprint…",
      "Drafting discovery findings…",
      "Preparing the questionnaire…",
    ];
  }
  if (p.includes("readiness") || p.includes("gate") || p.includes("ready for phase")) {
    return [
      "Scoring the foundations…",
      "Checking missing fields…",
      "Building the readiness gate…",
    ];
  }
  return [
    "Routing your request…",
    "Gathering live signals…",
    "Preparing the response…",
  ];
}

export function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}
