import React from "react";

/** One page (or planned page) in a site map. Only `path` is required. */
export type TreeRow = {
  path: string;
  title?: string | null;
  /** Free-form page type: service, hub, article, blog, location, page, … */
  kind?: string | null;
  /** HTTP status when the page was crawled. */
  status?: number | string | null;
  /** Explicit parent path (planned architecture). Falls back to the URL path. */
  parent?: string | null;
  /** Small pill shown on the node, e.g. "CDD". */
  flag?: string | null;
  /** Section name. Pages that sit directly under the home page are gathered under it. */
  group?: string | null;
};

type Props = {
  rows: TreeRow[];
  /** Hub label, usually the domain. */
  rootLabel: string;
  /** Rows beyond this are left out and counted in the toolbar. */
  maxRows?: number;
};

type TNode = {
  id: string;
  label: string;
  path: string;
  title: string;
  kind: "service" | "content" | "page" | "folder";
  status: number | null;
  flag: string | null;
  virtual: boolean;
  isGroup: boolean;
  children: TNode[];
  parent: TNode | null;
  branch: number;
  level: number;
  leaves: number;
  y: number;
};

const BRANCHES = 6;
const G = { hubD: 128, nodeW: 208, nodeH: 46, gapY: 8, colGap: 54, pad: 16 };

function normPath(raw: string): string {
  let p = String(raw || "").trim();
  if (!p) return "/";
  if (/^https?:\/\//i.test(p)) {
    try {
      p = new URL(p).pathname || "/";
    } catch {
      /* keep as is */
    }
  }
  p = p.split(/[?#]/)[0];
  if (!p.startsWith("/")) p = `/${p}`;
  p = p.replace(/\/{2,}/g, "/");
  if (p.length > 1) p = p.replace(/\/+$/, "");
  return p || "/";
}

function humanize(segment: string): string {
  const text = decodeURIComponent(segment).replace(/[-_]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : segment;
}

function kindOf(raw: string | null | undefined): TNode["kind"] {
  const k = String(raw || "").toLowerCase();
  if (/(service|hub|pillar|subservice|sub_service|location|landing|product|home|commercial)/.test(k)) {
    return "service";
  }
  if (/(blog|article|post|guide|faq|comparison|listicle|spoke|cluster|supporting|news)/.test(k)) {
    return "content";
  }
  return "page";
}

function statusNumber(raw: unknown): number | null {
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function statusTone(status: number | null): "ok" | "warn" | "bad" | null {
  if (status == null) return null;
  if (status >= 400) return "bad";
  if (status >= 300) return "warn";
  return "ok";
}

function buildTree(rows: TreeRow[], rootLabel: string): { root: TNode; count: number } {
  const nodes = new Map<string, TNode>();
  const make = (path: string, virtual: boolean): TNode => {
    const segs = path.split("/").filter(Boolean);
    const last = segs[segs.length - 1] || "";
    return {
      id: path,
      label: path === "/" ? rootLabel : humanize(last),
      path,
      title: "",
      kind: virtual ? "folder" : "page",
      status: null,
      flag: null,
      virtual,
      isGroup: false,
      children: [],
      parent: null,
      branch: 0,
      level: 0,
      leaves: 0,
      y: 0,
    };
  };
  const root = make("/", false);
  root.label = rootLabel;
  nodes.set("/", root);

  const pathParent = (path: string) => {
    const segs = path.split("/").filter(Boolean);
    return segs.length <= 1 ? "/" : `/${segs.slice(0, -1).join("/")}`;
  };

  const explicit = new Map<string, string>();
  const groupOf = new Map<string, string>();
  for (const row of rows) {
    const path = normPath(row.path);
    if (path === "/") {
      root.title = String(row.title || "");
      root.status = statusNumber(row.status);
      continue;
    }
    let node = nodes.get(path);
    if (!node) {
      node = make(path, false);
      nodes.set(path, node);
    }
    node.virtual = false;
    node.kind = kindOf(row.kind) as TNode["kind"];
    node.status = statusNumber(row.status);
    node.flag = row.flag ? String(row.flag) : null;
    if (row.group && String(row.group).trim()) groupOf.set(path, String(row.group).trim());
    const title = String(row.title || "").trim();
    if (title && !title.startsWith("/") && title !== path) {
      node.title = title;
      node.label = title.length > 44 ? `${title.slice(0, 43)}…` : title;
    }
    if (row.parent) {
      const parent = normPath(row.parent);
      if (parent !== path) explicit.set(path, parent);
    }
  }

  // Missing ancestors become dashed folder nodes so the structure stays visible.
  const parentOf = (path: string) => explicit.get(path) ?? pathParent(path);
  const ensure = (path: string): TNode => {
    let n = nodes.get(path);
    if (!n) {
      n = make(path, true);
      nodes.set(path, n);
    }
    return n;
  };
  for (const id of Array.from(nodes.keys())) {
    let cursor = id;
    for (let hops = 0; hops < 24 && cursor !== "/"; hops += 1) {
      const p = parentOf(cursor);
      ensure(p);
      cursor = p;
    }
  }
  // Break any cycle introduced by explicit parents by falling back to the URL path.
  for (const id of Array.from(explicit.keys())) {
    const seen = new Set<string>([id]);
    let cursor = explicit.get(id)!;
    let looped = false;
    for (let hops = 0; hops < 24 && cursor !== "/"; hops += 1) {
      if (seen.has(cursor)) {
        looped = true;
        break;
      }
      seen.add(cursor);
      cursor = parentOf(cursor);
    }
    if (looped) explicit.delete(id);
  }
  // A flat site has every page directly under home. Gather those pages under their
  // section so the map reads as sections, not as one long strip.
  const groups = new Map<string, TNode>();
  const groupNode = (name: string): TNode => {
    let g = groups.get(name);
    if (!g) {
      g = make(`group:${name}`, true);
      g.label = name;
      g.isGroup = true;
      g.parent = root;
      root.children.push(g);
      groups.set(name, g);
    }
    return g;
  };
  for (const node of Array.from(nodes.values())) {
    if (node.id === "/" || node.isGroup) continue;
    const parentId = parentOf(node.id);
    const group = groupOf.get(node.id);
    const parent = parentId === "/" && group ? groupNode(group) : ensure(parentId);
    node.parent = parent;
    parent.children.push(node);
  }

  const weight = (n: TNode) => (n.kind === "service" ? 0 : n.kind === "page" ? 1 : n.kind === "content" ? 2 : 3);
  const finish = (node: TNode, level: number, branch: number) => {
    node.level = level;
    node.branch = branch;
    node.children.sort((a, b) => weight(a) - weight(b) || a.label.localeCompare(b.label));
    node.leaves = node.children.length
      ? node.children.reduce((sum, c, i) => {
          finish(c, level + 1, level === 0 ? i % BRANCHES : branch);
          return sum + c.leaves;
        }, 0)
      : 1;
  };
  finish(root, 0, 0);
  const real = Array.from(nodes.values()).filter((n) => n.id !== "/" && !n.isGroup).length;
  return { root, count: real };
}

export default function SiteMindTree({ rows, rootLabel, maxRows = 400 }: Props) {
  const used = React.useMemo(() => rows.slice(0, maxRows), [rows, maxRows]);
  const { root, count } = React.useMemo(() => buildTree(used, rootLabel), [used, rootLabel]);

  // Groups open by default when small; a 30-post blog starts folded.
  const defaultOpen = React.useMemo(() => {
    const open = new Set<string>(["/"]);
    const walk = (n: TNode) => {
      if (n.children.length && n.leaves <= 10) open.add(n.id);
      n.children.forEach(walk);
    };
    root.children.forEach(walk);
    return open;
  }, [root]);
  const [expanded, setExpanded] = React.useState<Set<string>>(defaultOpen);
  React.useEffect(() => setExpanded(defaultOpen), [defaultOpen]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allIds = React.useMemo(() => {
    const ids = new Set<string>(["/"]);
    const walk = (n: TNode) => {
      if (n.children.length) ids.add(n.id);
      n.children.forEach(walk);
    };
    walk(root);
    return ids;
  }, [root]);

  // ---- layout: leaves stack top to bottom, parents centre on their children ----
  const visible: TNode[] = [];
  let leafIndex = 0;
  let maxLevel = 0;
  const place = (n: TNode) => {
    visible.push(n);
    maxLevel = Math.max(maxLevel, n.level);
    const kids = expanded.has(n.id) ? n.children : [];
    if (!kids.length) {
      n.y = leafIndex * (G.nodeH + G.gapY);
      leafIndex += 1;
      return;
    }
    kids.forEach(place);
    n.y = (kids[0].y + kids[kids.length - 1].y) / 2;
  };
  place(root);

  const leafBlock = leafIndex * (G.nodeH + G.gapY) - G.gapY;
  // A short tree is centred against the hub so the hub never pokes out of the canvas.
  const yOff = Math.max(0, (G.hubD - leafBlock) / 2);
  const innerH = Math.max(leafBlock, G.hubD);
  const mapH = innerH + G.pad * 2;
  const mapW = G.pad * 2 + G.hubD + maxLevel * (G.nodeW + G.colGap);
  const colX = (level: number) =>
    level === 0 ? G.pad : G.pad + G.hubD + G.colGap + (level - 1) * (G.nodeW + G.colGap);
  const centerY = (n: TNode) => G.pad + yOff + n.y + G.nodeH / 2;
  const hubY = centerY(root);

  const curve = (x1: number, y1: number, x2: number, y2: number) => {
    const dx = (x2 - x1) * 0.55;
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  };

  // ---- fit to width (down to 70%), otherwise scroll centred ----
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const [availW, setAvailW] = React.useState(0);
  React.useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const measure = () => setAvailW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const fit = availW ? Math.min(1, Math.max(0.7, (availW - 8) / mapW)) : 1;

  const hubStatus = statusTone(root.status);

  return (
    <div className="st-wrap">
      <div className="st-toolbar">
        <span className="st-count">
          {count} {count === 1 ? "page" : "pages"}
          {rows.length > maxRows ? `, first ${maxRows} shown` : ""}
        </span>
        <span className="st-actions">
          <button type="button" className="btn btn-ghost sp-summary-btn" onClick={() => setExpanded(new Set(allIds))}>
            Expand all
          </button>
          <button type="button" className="btn btn-ghost sp-summary-btn" onClick={() => setExpanded(new Set(["/"]))}>
            Collapse all
          </button>
        </span>
      </div>
      <div className="mm-scroll" ref={scrollRef}>
        <div className="mm-fit" style={{ width: mapW * fit, height: mapH * fit }}>
          <div className="mm-canvas" style={{ width: mapW, height: mapH, transform: `scale(${fit})` }}>
            <svg className="mm-links" width={mapW} height={mapH} aria-hidden="true">
              {visible
                .filter((n) => n.parent && visible.includes(n.parent))
                .map((n) => {
                  const p = n.parent!;
                  const x1 = p.level === 0 ? colX(0) + G.hubD - 2 : colX(p.level) + G.nodeW;
                  const x2 = colX(n.level);
                  return (
                    <g key={n.id} style={{ ["--branch" as string]: `var(--mm-${n.branch})` } as React.CSSProperties}>
                      <path
                        className={`mm-link st-link${n.virtual ? " is-ghost" : " is-on"}`}
                        d={curve(x1, p.level === 0 ? hubY : centerY(p), x2, centerY(n))}
                      />
                    </g>
                  );
                })}
            </svg>

            <div
              className="mm-hub st-hub"
              style={{ left: colX(0), top: hubY - G.hubD / 2, width: G.hubD, height: G.hubD }}
            >
              <span className="mm-hub-name">{rootLabel.replace(/\./g, ".​")}</span>
              <span className="mm-hub-meta">{count} pages</span>
              {hubStatus ? <span className={`st-dot st-dot--${hubStatus}`} aria-hidden="true" /> : null}
            </div>

            {visible
              .filter((n) => n.level > 0)
              .map((n) => {
                const open = expanded.has(n.id);
                const hasKids = n.children.length > 0;
                const tone = statusTone(n.status);
                const tip = [n.path, n.title && n.title !== n.label ? n.title : "", n.status ? `HTTP ${n.status}` : ""]
                  .filter(Boolean)
                  .join(" · ");
                // The slug is already the label unless the page has its own title.
                const subline = n.isGroup ? `${n.leaves} ${n.leaves === 1 ? "page" : "pages"}` : n.title ? n.path : "";
                const inner = (
                  <>
                    {tone ? <span className={`st-dot st-dot--${tone}`} aria-hidden="true" /> : null}
                    <span className="st-text">
                      <span className="st-label">{n.label}</span>
                      {subline ? <span className="st-path">{subline}</span> : null}
                    </span>
                    {n.flag ? <span className="st-flag">{n.flag}</span> : null}
                    {hasKids ? (
                      <span className="st-fold" aria-hidden="true">
                        {open ? "−" : `+${n.leaves}`}
                      </span>
                    ) : null}
                  </>
                );
                const cls = `st-node mm-b${n.branch} is-${n.kind}${n.virtual ? " is-virtual" : ""}${
                  n.isGroup ? " is-group" : ""
                }${hasKids ? " has-kids" : ""}${subline ? "" : " is-single"}`;
                const style = { left: colX(n.level), top: G.pad + yOff + n.y, width: G.nodeW, height: G.nodeH };
                return hasKids ? (
                  <button
                    key={n.id}
                    type="button"
                    className={cls}
                    style={style}
                    title={tip}
                    aria-expanded={open}
                    onClick={() => toggle(n.id)}
                  >
                    {inner}
                  </button>
                ) : (
                  <div key={n.id} className={cls} style={style} title={tip}>
                    {inner}
                  </div>
                );
              })}
          </div>
        </div>
      </div>
    </div>
  );
}
