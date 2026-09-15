import { FieldGrid, MetricGrid } from "./PresentableValue";

type Block =
  | { type: "h1" | "h2" | "h3"; text: string }
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "hr" }
  | { type: "img"; alt: string; src: string; caption?: string }
  | { type: "figure"; role: string; caption: string };

export type DraftImage = {
  role?: string;
  src?: string | null;
  alt?: string;
  caption?: string;
  prompt?: string;
  status?: string;
};

function parseMarkdown(md: string): Block[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  const flushPara = (buf: string[]) => {
    const text = buf.join(" ").trim();
    if (text) blocks.push({ type: "p", text });
    buf.length = 0;
  };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      i += 1;
      continue;
    }
    if (trimmed === "---") {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }
    if (trimmed.startsWith("# ")) {
      blocks.push({ type: "h1", text: trimmed.slice(2).trim() });
      i += 1;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      blocks.push({ type: "h2", text: trimmed.slice(3).trim() });
      i += 1;
      continue;
    }
    if (trimmed.startsWith("### ")) {
      blocks.push({ type: "h3", text: trimmed.slice(4).trim() });
      i += 1;
      continue;
    }
    const mdImg = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)\s*$/);
    if (mdImg) {
      let caption = "";
      if (i + 1 < lines.length && /^\*.+\*$/.test(lines[i + 1].trim())) {
        caption = lines[i + 1].trim().slice(1, -1);
        i += 1;
      }
      blocks.push({ type: "img", alt: mdImg[1], src: mdImg[2], caption });
      i += 1;
      continue;
    }
    const fig = trimmed.match(/^\[FIGURE\s+([^\]]+)\]\s*(.*)$/i);
    if (fig) {
      blocks.push({ type: "figure", role: fig[1].trim(), caption: fig[2].trim() });
      i += 1;
      continue;
    }
    if (trimmed.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i]
          .trim()
          .replace(/^\|/, "")
          .replace(/\|$/, "")
          .split("|")
          .map((c) => c.trim());
        if (!cells.every((c) => /^:?-+:?$/.test(c))) rows.push(cells);
        i += 1;
      }
      if (rows.length) {
        blocks.push({ type: "table", headers: rows[0], rows: rows.slice(1) });
      }
      continue;
    }
    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ul", items });
      continue;
    }
    const para: string[] = [trimmed];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("#") &&
      !lines[i].trim().startsWith("|") &&
      !/^[-*]\s+/.test(lines[i].trim()) &&
      lines[i].trim() !== "---" &&
      !/^!\[[^\]]*\]\([^)]+\)/.test(lines[i].trim()) &&
      !/^\[FIGURE\s+/i.test(lines[i].trim())
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    flushPara(para);
  }
  return blocks;
}

function stripMd(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^[-*]\s+\[[ xX]\]\s+/, "")
    .trim();
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[AUTHOR INPUT REQUIRED[^\]]*\]|\[VERIFY\][^.!]*)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return (
            <span key={i} className="cs-chip">
              {part.slice(1, -1)}
            </span>
          );
        }
        if (part.startsWith("[AUTHOR INPUT REQUIRED")) {
          return (
            <span key={i} className="cs-chip" style={{ background: "rgba(180, 35, 24, 0.12)", color: "#b42318" }}>
              {part}
            </span>
          );
        }
        if (part.startsWith("[VERIFY]")) {
          return (
            <span key={i} className="cs-chip" style={{ background: "rgba(180, 120, 24, 0.14)", color: "#8a5a00" }}>
              {part}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function metadataFromList(items: string[]): Record<string, string> {
  const data: Record<string, string> = {};
  for (const item of items) {
    const cleaned = stripMd(item).replace(/^\*\*|\*\*$/g, "");
    const pipeBits = cleaned.split("|").map((b) => b.trim()).filter(Boolean);
    if (pipeBits.length > 1) {
      for (const bit of pipeBits) {
        const m = bit.match(/^\*?\*?([^:*]+)\*?\*?\s*:\s*(.+)$/);
        if (m) data[m[1].trim()] = m[2].trim();
      }
      continue;
    }
    const m = cleaned.match(/^([^:]+):\s*(.+)$/);
    if (m) data[m[1].trim()] = m[2].trim();
  }
  return data;
}

type Props = {
  markdown: string;
  title?: string;
  url?: string;
  author?: string;
  standing?: string;
  status?: string;
  differentiation?: string;
  keyword?: string;
  action?: string;
  funnel?: string;
  images?: DraftImage[];
  relatedKeywords?: string[];
  metaDescription?: string;
};

function SerpPreview({
  title,
  url,
  metaDescription,
}: {
  title?: string;
  url?: string;
  metaDescription?: string;
}) {
  if (!title && !url && !metaDescription) return null;
  const descLen = (metaDescription || "").length;
  return (
    <div className="draft-serp-preview">
      <div className="serp-preview-label">Ready to publish — search preview</div>
      {url ? <div className="serp-url">{url}</div> : null}
      <div className="serp-title">{title || "(no title tag set)"}</div>
      {metaDescription ? (
        <>
          <div className="serp-meta">{metaDescription}</div>
          <div className={`serp-char-count${descLen > 160 ? " is-over" : ""}`}>
            {descLen} / 160 characters{descLen > 160 ? " — will be truncated in search results" : ""}
          </div>
        </>
      ) : (
        <div className="serp-meta is-missing">No meta description set — resolve before publishing.</div>
      )}
    </div>
  );
}

function DraftFigure({
  src,
  alt,
  caption,
  role,
  status,
}: {
  src?: string | null;
  alt?: string;
  caption?: string;
  role?: string;
  status?: string;
}) {
  return (
    <figure className="draft-figure">
      {src ? (
        <img src={src} alt={alt || role || "Draft figure"} />
      ) : (
        <div className="draft-figure-placeholder">
          {status === "failed" ? "Image generation failed" : "Image from strategy"}
          {role ? ` · ${role}` : ""}
        </div>
      )}
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}

export default function DraftDocument({
  markdown,
  title,
  url,
  author,
  standing,
  status,
  differentiation,
  keyword,
  action,
  funnel,
  images,
  relatedKeywords,
  metaDescription,
}: Props) {
  const blocks = parseMarkdown(markdown);
  const isMetaHeading = (heading: string) => {
    const h = heading.toLowerCase();
    return (
      h.includes("review queue") ||
      h.includes("draft metadata") ||
      h.includes("differentiation") ||
      h.includes("coverage check") ||
      h.includes("notes for the editor")
    );
  };

  const meta: Record<string, unknown> = {};
  if (url) meta.url = url;
  if (keyword) meta.keyword = keyword;
  if (relatedKeywords?.length) meta.related = relatedKeywords.join(", ");
  if (author) meta.author = standing ? `${author} — ${standing}` : author;
  if (action) meta.action = action;
  if (funnel) meta.funnel = funnel;
  if (status) meta.status = status;

  const sections: { heading: string; blocks: Block[] }[] = [];
  let current = { heading: "", blocks: [] as Block[] };
  for (const b of blocks) {
    if (b.type === "h1") {
      if (!title) title = b.text;
      if (current.heading || current.blocks.length) sections.push(current);
      current = { heading: "", blocks: [] };
      continue;
    }
    if (b.type === "hr") {
      if (current.heading || current.blocks.length) sections.push(current);
      current = { heading: "", blocks: [] };
      continue;
    }
    if (b.type === "h2") {
      if (current.heading || current.blocks.length) sections.push(current);
      current = { heading: b.text, blocks: [] };
      continue;
    }
    current.blocks.push(b);
  }
  if (current.heading || current.blocks.length) sections.push(current);

  const review = sections.find((s) => s.heading.toLowerCase().includes("review queue"));
  const metaSec = sections.find((s) => s.heading.toLowerCase().includes("draft metadata"));
  const diffSec = sections.find((s) => s.heading.toLowerCase().includes("differentiation"));
  const coverage = sections.find((s) => s.heading.toLowerCase().includes("coverage check"));
  const notes = sections.find((s) => s.heading.toLowerCase().includes("notes for the editor"));
  const body = sections.filter((s) => !isMetaHeading(s.heading));

  if (metaSec) {
    for (const b of metaSec.blocks) {
      if (b.type === "ul") Object.assign(meta, metadataFromList(b.items));
      if (b.type === "p") Object.assign(meta, metadataFromList([b.text]));
    }
  }
  // Meta description gets its own prominent search-preview block below, rather
  // than sitting anonymously in the generic metadata grid.
  const parsedMetaDescription =
    typeof meta["Meta description"] === "string" ? (meta["Meta description"] as string) : undefined;
  const resolvedMetaDescription = metaDescription || parsedMetaDescription;
  delete meta["Meta description"];
  const diffText =
    differentiation ||
    diffSec?.blocks
      .filter((b) => b.type === "p")
      .map((b) => (b.type === "p" ? b.text : ""))
      .join(" ");

  const reviewItems = review?.blocks.flatMap((b) => (b.type === "ul" ? b.items : b.type === "p" ? [b.text] : [])) || [];
  const noteItems = notes?.blocks.flatMap((b) => (b.type === "ul" ? b.items : b.type === "p" ? [b.text] : [])) || [];
  const coverageTable = coverage?.blocks.find((b) => b.type === "table");
  const generated = (images || []).filter((i) => i.src);
  const allImages = images || [];
  const heroImg =
    generated.find((i) => i.role === "hero") || generated[0];
  const shownSrc = new Set<string>(heroImg?.src ? [heroImg.src] : []);
  const failedPlaceholders =
    !heroImg && allImages.length
      ? allImages
      : [];

  return (
    <div className="draft-document">
      <div className="cs-pillar-head" style={{ marginBottom: 8 }}>
        <strong>{title || "Draft"}</strong>
        {status ? <span className="cs-chip">{status}</span> : null}
        <span className="cs-meta">human review required</span>
      </div>

      <SerpPreview title={title} url={url} metaDescription={resolvedMetaDescription} />

      {Object.keys(meta).length ? <FieldGrid data={meta} /> : null}

      {heroImg ? (
        <DraftFigure
          src={heroImg.src}
          alt={heroImg.alt}
          caption={heroImg.caption || heroImg.prompt}
          role={heroImg.role}
          status={heroImg.status}
        />
      ) : failedPlaceholders.length ? (
        <div className="cs-pillar-block">
          <div className="present-key">Images</div>
          {failedPlaceholders.map((img, i) => (
            <DraftFigure
              key={`failed-${i}`}
              src={img.src}
              alt={img.alt}
              caption={img.caption || img.prompt}
              role={img.role}
              status={img.status || "failed"}
            />
          ))}
        </div>
      ) : null}

      {diffText ? (
        <div className="cs-pillar-block">
          <div className="present-key">Differentiation</div>
          <p className="draft-prose">
            <Inline text={diffText} />
          </p>
        </div>
      ) : null}

      {reviewItems.length ? (
        <div className="cs-pillar-block">
          <h4 style={{ marginBottom: 8 }}>Review queue</h4>
          <ul className="missing-list">
            {reviewItems.map((item, i) => {
              const checked = /^\[[xX]\]/.test(item) || item.includes("[x]");
              const label = stripMd(item.replace(/^\[[ xX]\]\s*/, ""));
              return (
                <li key={i}>
                  <label className="draft-check">
                    <input type="checkbox" disabled checked={checked} />
                    <Inline text={label} />
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {body.map((sec, i) => (
        <div key={`${sec.heading}-${i}`} className="cs-pillar-block">
          {sec.heading ? (
            <div className="cs-pillar-head">
              <strong>{sec.heading}</strong>
            </div>
          ) : null}
          {sec.blocks.map((b, j) => {
            if (b.type === "h3") {
              return (
                <h5 key={j} className="draft-h3">
                  {b.text}
                </h5>
              );
            }
            if (b.type === "img") {
              if (heroImg?.src && b.src === heroImg.src) return null;
              shownSrc.add(b.src);
              return (
                <DraftFigure key={j} src={b.src} alt={b.alt} caption={b.caption} />
              );
            }
            if (b.type === "figure") {
              return <DraftFigure key={j} role={b.role} caption={b.caption} />;
            }
            if (b.type === "p") {
              return (
                <p key={j} className="draft-prose">
                  <Inline text={b.text} />
                </p>
              );
            }
            if (b.type === "ul") {
              return (
                <ul key={j} className="missing-list">
                  {b.items.map((item, k) => (
                    <li key={k}>
                      <Inline text={stripMd(item)} />
                    </li>
                  ))}
                </ul>
              );
            }
            if (b.type === "table") {
              return (
                <div key={j} style={{ overflowX: "auto", marginTop: 8 }}>
                  <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                    <thead>
                      <tr>
                        {b.headers.map((h) => (
                          <th key={h}>{stripMd(h)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {b.rows.map((row, r) => (
                        <tr key={r}>
                          {row.map((cell, c) => (
                            <td key={c}>{stripMd(cell)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            }
            return null;
          })}
        </div>
      ))}

      {(images || [])
        .filter((img) => Boolean(img.src) && img.src !== heroImg?.src && !shownSrc.has(String(img.src)))
        .map((img, i) => (
          <DraftFigure
            key={`extra-${i}`}
            src={img.src}
            alt={img.alt}
            caption={img.caption || img.prompt}
            role={img.role}
            status={img.status}
          />
        ))}

      {coverageTable && coverageTable.type === "table" ? (
        <div className="cs-pillar-block">
          <h4 style={{ marginBottom: 8 }}>Coverage check</h4>
          <div style={{ overflowX: "auto" }}>
            <table className="kw-report-table" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {coverageTable.headers.map((h) => (
                    <th key={h}>{stripMd(h)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {coverageTable.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c}>{stripMd(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {noteItems.length ? (
        <div className="cs-pillar-block">
          <h4 style={{ marginBottom: 8 }}>Notes for the editor</h4>
          <ul className="missing-list">
            {noteItems.map((item, i) => (
              <li key={i}>
                <Inline text={stripMd(item)} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <MetricGrid
        items={[
          { label: "Sections", value: body.length || null, tone: "neutral" },
          { label: "Review items", value: reviewItems.length || null, tone: "warn" },
        ]}
      />
    </div>
  );
}
