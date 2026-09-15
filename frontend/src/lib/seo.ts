/**
 * Route-aware document SEO for the Radius OS SPA.
 *
 * Public: `/` only. Authenticated workspace routes stay noindex so client
 * reports, chat, and IDs never become crawlable surfaces.
 */

export type SeoConfig = {
  title: string;
  description: string;
  /** Path for canonical (public pages only). Query strings are stripped. */
  path?: string;
  robots?: string;
  imagePath?: string;
  jsonLd?: Record<string, unknown> | Array<Record<string, unknown>>;
};

const META_MARK = "data-radius-seo";

export const PUBLIC_TITLE =
  "Radius OS — SEO onboarding that earns sign-off";
export const PUBLIC_DESCRIPTION =
  "Role-gated SEO skills with shared Client Digital Profile memory — research first, human sign-off before anything goes live.";

export const PRIVATE_TITLE = "Radius OS Workspace";
export const PRIVATE_DESCRIPTION =
  "Authenticated Radius OS workspace. Sign in to continue.";

function upsertMeta(
  attr: "name" | "property",
  key: string,
  content: string,
): void {
  if (typeof document === "undefined") return;
  // Match by attr+key alone (no [data-radius-seo] requirement) so this finds
  // and updates the static tag already baked into index.html instead of
  // appending a second, conflicting one next to it.
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute(META_MARK, "1");
  el.content = content;
}

function upsertLink(rel: string, href: string | null): void {
  if (typeof document === "undefined") return;
  let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
  if (!href) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement("link");
    el.rel = rel;
    document.head.appendChild(el);
  }
  el.setAttribute(META_MARK, "1");
  el.href = href;
}

function upsertJsonLd(
  data: Record<string, unknown> | Array<Record<string, unknown>> | null,
): void {
  if (typeof document === "undefined") return;
  const id = "radius-seo-jsonld";
  const existing = document.getElementById(id);
  if (!data) {
    existing?.remove();
    return;
  }
  const script =
    (existing as HTMLScriptElement | null) ||
    document.createElement("script");
  script.id = id;
  script.type = "application/ld+json";
  script.setAttribute(META_MARK, "1");
  script.textContent = JSON.stringify(data);
  if (!existing) document.head.appendChild(script);
}

/** Public origin for canonicals — prefers Vite env, else current host. */
export function publicOrigin(): string {
  const fromEnv = (import.meta.env.VITE_PUBLIC_ORIGIN as string | undefined)?.trim();
  if (fromEnv) return fromEnv.replace(/\/$/, "");
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin.replace(/\/$/, "");
  }
  return "";
}

export function absoluteUrl(path = "/"): string {
  const origin = publicOrigin();
  const clean = path.startsWith("/") ? path : `/${path}`;
  const noQuery = clean.split("?")[0].split("#")[0] || "/";
  if (!origin) return noQuery;
  return `${origin}${noQuery === "/" ? "/" : noQuery}`;
}

export function applySeo(config: SeoConfig): void {
  if (typeof document === "undefined") return;

  document.title = config.title;
  upsertMeta("name", "description", config.description);
  upsertMeta(
    "name",
    "robots",
    config.robots || "index,follow,max-image-preview:large",
  );

  const isPrivate = (config.robots || "").toLowerCase().includes("noindex");
  const canonicalPath = isPrivate ? null : config.path ?? "/";
  const canonical = canonicalPath != null ? absoluteUrl(canonicalPath) : null;
  upsertLink("canonical", canonical);

  const pageUrl = canonical || absoluteUrl(config.path || "/");
  const image = absoluteUrl(config.imagePath || "/landing/hero.jpg");

  upsertMeta("property", "og:type", "website");
  upsertMeta("property", "og:site_name", "Radius OS");
  upsertMeta("property", "og:title", config.title);
  upsertMeta("property", "og:description", config.description);
  upsertMeta("property", "og:url", pageUrl);
  upsertMeta("property", "og:image", image);
  upsertMeta("name", "twitter:card", "summary_large_image");
  upsertMeta("name", "twitter:title", config.title);
  upsertMeta("name", "twitter:description", config.description);
  upsertMeta("name", "twitter:image", image);

  upsertJsonLd(config.jsonLd ?? null);
}

export function applyPublicLandingSeo(): void {
  const home = absoluteUrl("/");
  const orgId = `${home}#organization`;
  const websiteId = `${home}#website`;

  applySeo({
    title: PUBLIC_TITLE,
    description: PUBLIC_DESCRIPTION,
    path: "/",
    robots: "index,follow,max-image-preview:large",
    imagePath: "/landing/hero.jpg",
    jsonLd: [
      {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": orgId,
        name: "Radius OS",
        url: home,
        description: PUBLIC_DESCRIPTION,
      },
      {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": websiteId,
        name: "Radius OS",
        url: home,
        description: PUBLIC_DESCRIPTION,
        publisher: { "@id": orgId },
        inLanguage: "en",
      },
      {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "@id": `${home}#webpage`,
        name: PUBLIC_TITLE,
        description: PUBLIC_DESCRIPTION,
        url: home,
        isPartOf: { "@id": websiteId },
        about: { "@id": orgId },
        inLanguage: "en",
      },
    ],
  });
}

export function applyPrivateAppSeo(pageLabel = "Workspace"): void {
  applySeo({
    title: `${pageLabel} · ${PRIVATE_TITLE}`,
    description: PRIVATE_DESCRIPTION,
    robots: "noindex,nofollow",
    jsonLd: undefined,
  });
  // Explicitly clear structured data on private views.
  upsertJsonLd(null);
}
