import { useEffect, useRef, useState } from "react";
import type { ReportDownloadFormat } from "../api";

const FORMATS: { format: ReportDownloadFormat; label: string }[] = [
  { format: "pdf", label: "PDF" },
  { format: "docx", label: "Word (.docx)" },
];

type Props = {
  onPick: (format: ReportDownloadFormat) => void;
  loading: boolean;
  label?: string;
};

/** One button that downloads every phase report as PDF or Word. */
export default function DownloadReportsMenu({ onPick, loading, label = "Download all" }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="report-download-menu" ref={rootRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="btn btn-ghost"
        disabled={loading}
        onClick={() => setOpen((v) => !v)}
        title="Download every phase report as PDF or Word"
        aria-haspopup="true"
        aria-expanded={open}
      >
        {loading ? "Preparing…" : `${label} ▾`}
      </button>
      {open ? (
        <div className="report-download-menu-list" role="menu">
          {FORMATS.map((f) => (
            <button
              key={f.format}
              type="button"
              role="menuitem"
              className="report-download-menu-item"
              onClick={() => {
                setOpen(false);
                onPick(f.format);
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
