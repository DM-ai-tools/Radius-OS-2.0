import type { ReactNode } from "react";
import ReportDownloadButton from "./ReportDownloadButton";

type Props = {
  card: Record<string, unknown>;
  clientId?: string;
  token: string | null;
  onError?: (message: string) => void;
  children: ReactNode;
};

const SKIP_DOWNLOAD_TYPES = new Set([
  "oauth_request",
  "discovery_rerun_confirm",
  "discovery_questionnaire",
  "phase_validation_report",
]);

export default function ReportCardShell({ card, clientId, token, onError, children }: Props) {
  const cardType = String(card.card_type || "");
  const canDownload = Boolean(clientId && cardType && !SKIP_DOWNLOAD_TYPES.has(cardType));

  if (!canDownload) {
    return <>{children}</>;
  }

  return (
    <div className="report-card-shell">
      <div className="report-card-toolbar">
        <ReportDownloadButton
          clientId={clientId!}
          token={token}
          cardType={cardType}
          onError={onError}
        />
      </div>
      {children}
    </div>
  );
}

/** Primary report card_type per phase in team memory. */
export const PHASE_REPORT_CARD_TYPE: Record<string, string> = {
  discovery: "discovery_profile",
  tracking: "tracking_t6_signoff",
  website: "website_audit",
  competitor: "competitor_landscape",
  search_demand: "search_demand_report",
  content_audit: "content_audit_report",
  seo_strategy: "content_strategy_report",
  site_architecture: "site_architecture_blueprint",
  technical_seo: "technical_seo_report",
  content_planning: "content_planning_report",
  content_production: "content_production_report",
  on_page_seo: "on_page_seo_report",
  publishing: "publishing_report",
};
