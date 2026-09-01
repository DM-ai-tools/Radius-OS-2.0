type Props = {
  title: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  maxRows?: number;
};

function label(col: string): string {
  const aliases: Record<string, string> = {
    current_url: "Current URL",
    proposed_url: "Proposed URL (if new)",
    l3_sub_subcategory: "L3 Sub-Subcategory",
    search_volume_mo: "Search Volume (mo)",
    combined_cluster_volume: "Combined Cluster Volume",
    est_products: "Est. Products",
    in_scope: "In Scope?",
    secondary_keywords: "Secondary Keywords (5-10)",
  };
  if (aliases[col]) return aliases[col];
  return col
    .replace(/_/g, " ")
    .replace(/\bmo\b/i, "(mo)")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtCell(col: string, value: unknown): string {
  if (value == null || value === "") return "—";
  if (col.includes("volume") || col === "search_volume_mo" || col === "combined_cluster_volume") {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString() : String(value);
  }
  if (col === "cpc") {
    const n = Number(value);
    return Number.isFinite(n) ? `$${n.toFixed(2)}` : String(value);
  }
  return String(value);
}

export default function WorkbookTable({ title, columns, rows, maxRows = 30 }: Props) {
  const dataRows = rows.filter((r) => r.row_type !== "section");
  const display = dataRows.slice(0, maxRows);
  if (!display.length) return null;

  return (
    <div style={{ marginBottom: 16 }}>
      <h4 style={{ margin: "14px 0 8px", fontSize: 13 }}>{title}</h4>
      <div style={{ overflowX: "auto" }}>
        <table
          className="kw-report-table"
          style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}
        >
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
              {columns.map((col) => (
                <th key={col} style={{ padding: "5px 7px", whiteSpace: "nowrap" }}>
                  {label(col)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, maxRows + 5).map((row, i) => {
              if (row.row_type === "section") {
                return (
                  <tr key={`section-${i}`} style={{ background: "rgba(0,0,0,0.03)" }}>
                    <td
                      colSpan={columns.length}
                      style={{ padding: "6px 7px", fontWeight: 700, fontSize: 11 }}
                    >
                      {String(row.section_label || row.l1_category || "")}
                    </td>
                  </tr>
                );
              }
              return (
                <tr key={`row-${i}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  {columns.map((col) => (
                    <td key={col} style={{ padding: "5px 7px", verticalAlign: "top" }}>
                      {fmtCell(col, row[col])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {dataRows.length > maxRows ? (
        <p style={{ fontSize: 11, color: "var(--muted)", margin: "6px 0 0" }}>
          Showing {maxRows} of {dataRows.length} workbook rows — export .xlsx for full sheet.
        </p>
      ) : null}
    </div>
  );
}
