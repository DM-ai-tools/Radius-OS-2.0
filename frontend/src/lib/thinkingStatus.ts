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
  if (p.includes("competitor") || p.includes("landscape") || p.includes("keyword gap")) {
    return [
      "Mapping competitor set…",
      "Pulling ranking and authority signals…",
      "Scoring threat tiers…",
    ];
  }
  if (
    p.includes("broken") ||
    p.includes("on-page") ||
    p.includes("technical seo") ||
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
  if (p.includes("readiness") || p.includes("gate") || p.includes("phase 5")) {
    return [
      "Scoring all four phases…",
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
