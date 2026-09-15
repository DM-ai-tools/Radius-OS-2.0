import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { applyPrivateAppSeo, applyPublicLandingSeo } from "../lib/seo";

/**
 * Applies document metadata from the current route.
 * Only `/` is indexable; all authenticated app routes stay noindex.
 */
export default function SeoManager() {
  const { pathname } = useLocation();

  useEffect(() => {
    if (pathname === "/" || pathname === "") {
      applyPublicLandingSeo();
      return;
    }
    if (pathname.startsWith("/clients/") && pathname.includes("/chat")) {
      applyPrivateAppSeo("Client chat");
      return;
    }
    if (pathname === "/app" || pathname.startsWith("/clients")) {
      applyPrivateAppSeo("Clients");
      return;
    }
    applyPrivateAppSeo();
  }, [pathname]);

  return null;
}
