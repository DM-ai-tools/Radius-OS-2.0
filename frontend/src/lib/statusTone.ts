/** Shared tone for semantic status strings (e.g. "connected", "pending_setup",
 * "fail") used in status pills across cards and the Playground. Not for raw
 * HTTP status codes — see httpStatusLabel.ts's httpStatusTone for that. */
export function statusTone(status: string | null | undefined): string {
  const s = (status || "").toLowerCase();
  if (s.includes("connected") || s === "done" || s === "pass" || s === "ready") return "pass";
  if (s.includes("pending") || s.includes("setup") || s.includes("review") || s.includes("await"))
    return "warning";
  if (s.includes("not ") || s.includes("fail") || s.includes("block")) return "fail";
  return "unverified";
}
