import { useEffect, useRef, useState } from "react";
import { api, type ReportDownloadFormat } from "../api";

type Props = {
  clientId: string;
  token: string | null;
  cardType: string;
  label?: string;
  className?: string;
  onError?: (message: string) => void;
};

const FORMATS: { format: ReportDownloadFormat; label: string }[] = [
  { format: "pdf", label: "PDF" },
  { format: "docx", label: "Word (.docx)" },
];

/** Reports whose keyword list can also be downloaded as a spreadsheet. */
const CSV_CARD_TYPES = new Set(["search_demand_report"]);
const CSV_FORMAT = { format: "csv" as ReportDownloadFormat, label: "Keyword list (.csv)" };

export default function ReportDownloadButton({
  clientId,
  token,
  cardType,
  label = "Download",
  className = "btn btn-ghost report-download-btn",
  onError,
}: Props) {
  const [loading, setLoading] = useState(false);
  const formats = CSV_CARD_TYPES.has(cardType) ? [...FORMATS, CSV_FORMAT] : FORMATS;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  async function onPick(format: ReportDownloadFormat) {
    if (!clientId || !cardType || loading) return;
    setOpen(false);
    setLoading(true);
    try {
      await api.downloadReport(token, clientId, cardType, format);
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "Failed to download report");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="report-download-menu" ref={rootRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className={className}
        disabled={loading || !clientId || !cardType}
        onClick={() => setOpen((v) => !v)}
        title={CSV_CARD_TYPES.has(cardType) ? "Download this report as PDF or Word, or the keyword list as CSV" : "Download this report as PDF or Word"}
        aria-haspopup="true"
        aria-expanded={open}
      >
        {loading ? "Preparing…" : `${label} ▾`}
      </button>
      {open ? (
        <div className="report-download-menu-list" role="menu">
          {formats.map((f) => (
            <button
              key={f.format}
              type="button"
              role="menuitem"
              className="report-download-menu-item"
              onClick={() => onPick(f.format)}
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
