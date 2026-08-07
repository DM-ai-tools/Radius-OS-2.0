/** Plain-language labels for HTTP status codes (non-technical operators). */

export function httpStatusLabel(status: number | string | null | undefined): string {
  if (status == null || status === "") return "Unknown";
  const code = Number(status);
  if (!Number.isFinite(code)) {
    const raw = String(status).replaceAll("_", " ").trim();
    return raw || "Unknown";
  }

  const known: Record<number, string> = {
    200: "Working",
    201: "Created",
    204: "Empty response",
    301: "Moved permanently",
    302: "Temporarily redirected",
    304: "Not modified",
    307: "Temporarily redirected",
    308: "Moved permanently",
    400: "Bad request",
    401: "Login required",
    403: "Access blocked",
    404: "Page not found",
    405: "Method not allowed",
    408: "Timed out",
    410: "Page removed",
    429: "Too many requests",
    500: "Server error",
    502: "Bad gateway",
    503: "Service unavailable",
    504: "Gateway timeout",
  };

  if (known[code]) return known[code];
  if (code >= 200 && code < 300) return "Working";
  if (code >= 300 && code < 400) return "Redirected";
  if (code === 404 || code === 410) return "Page not found";
  if (code >= 400 && code < 500) return "Client error";
  if (code >= 500) return "Server error";
  return `Code ${code}`;
}

export function httpStatusTone(
  status: number | string | null | undefined
): "info" | "warning" | "critical" | "pass" | "fail" {
  const code = Number(status);
  if (!Number.isFinite(code)) return "info";
  if (code >= 200 && code < 300) return "pass";
  if (code >= 300 && code < 400) return "warning";
  if (code >= 400) return "fail";
  return "info";
}
