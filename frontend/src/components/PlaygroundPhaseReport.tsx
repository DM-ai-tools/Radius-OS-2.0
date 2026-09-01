/** Structured phase reports for Team shared memory / playground. */

import type { ReactNode } from "react";
import PlaygroundBlocks from "./PlaygroundBlocks";

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function rows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && typeof r === "object") as Array<Record<string, unknown>>;
}

function fmtVol(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString();
}

function fmtNum(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pg-report-section">
      <h4 className="pg-report-h">{title}</h4>
      {children}
    </section>
  );
}

function MetaLine({ data }: { data: Record<string, unknown> }) {
  const bits: string[] = [];
  if (data.location_name) bits.push(`Geo: ${String(data.location_name)}`);
  else if (data.geographic_focus) bits.push(`Geo focus: ${String(data.geographic_focus)}`);
  else if (data.country) bits.push(`Country: ${String(data.country)}`);
  if (data.location_code != null) bits.push(`Location code: ${String(data.location_code)}`);
  if (data.keyword_count != null) bits.push(`Keywords: ${String(data.keyword_count)}`);
  if (Array.isArray(data.providers_used) && data.providers_used.length) {
    bits.push(`Providers: ${(data.providers_used as string[]).join(", ")}`);
  }
  const names = Array.isArray(data.competitor_names)
    ? (data.competitor_names as string[])
    : [];
  const domains = Array.isArray(data.competitor_domains)
    ? (data.competitor_domains as string[])
    : [];
  if (names.length) bits.push(`Competitors: ${names.slice(0, 6).join(", ")}`);
  else if (domains.length) bits.push(`Competitors: ${domains.slice(0, 5).join(", ")}`);
  if (data.industry) bits.push(`Industry: ${String(data.industry)}`);
  if (data.generated) bits.push(`Generated: ${String(data.generated)}`);
  if (!bits.length && data.note) return <p className="pg-report-meta">{String(data.note)}</p>;
  if (!bits.length) return null;
  return (
    <p className="pg-report-meta">
      {bits.join(" · ")}
      {data.note ? ` · ${String(data.note)}` : ""}
    </p>
  );
}

function CompetitorSitesSection({ data }: { data: Record<string, unknown> }) {
  const sites = rows(data.competitor_sites);
  const ia = rows(data.competitor_ia);
  const list = sites.length ? sites : ia;
  if (!list.length) return null;
  return (
    <Section title="Competitor sites & blogs">
      <div className="pg-report-table-wrap">
        <table className="pg-report-table">
          <thead>
            <tr>
              <th>Competitor</th>
              <th>Domain</th>
              <th>Pages</th>
              <th>Blogs</th>
              <th>Sample titles / hubs</th>
            </tr>
          </thead>
          <tbody>
            {list.slice(0, 6).map((s, i) => {
              const samples = [
                ...rows(s.blogs),
                ...rows(s.blog_examples),
                ...rows(s.pages),
              ]
                .slice(0, 4)
                .map((b) => String(b.title || b.path || ""))
                .filter(Boolean);
              const hubs = Array.isArray(s.hub_paths)
                ? (s.hub_paths as string[]).slice(0, 4).join(", ")
                : "";
              return (
                <tr key={`${String(s.domain || s.name)}-${i}`}>
                  <td>
                    <strong>{String(s.name || "—")}</strong>
                  </td>
                  <td>{String(s.domain || "—")}</td>
                  <td>{fmtNum(s.page_count)}</td>
                  <td>{fmtNum(s.blog_count)}</td>
                  <td className="pg-report-small">
                    {samples.join(" · ") || hubs || (s.error ? String(s.error) : "—")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function KeywordReportTable({ list }: { list: Array<Record<string, unknown>> }) {
  if (!list.length) return <p className="playground-empty">No keyword rows yet.</p>;
  return (
    <div className="pg-report-table-wrap">
      <table className="pg-report-table">
        <thead>
          <tr>
            <th>Keyword</th>
            <th>Volume</th>
            <th>KD</th>
            <th>CPC</th>
            <th>Score</th>
            <th>Intent</th>
            <th>Competitors</th>
            <th>Ranking titles</th>
          </tr>
        </thead>
        <tbody>
          {list.slice(0, 15).map((r, i) => {
            const titles = rows(r.serp_titles);
            const comps = Array.isArray(r.competitor_domains)
              ? (r.competitor_domains as string[])
              : [];
            const positions = rows(r.competitor_positions);
            return (
              <tr key={`${String(r.keyword)}-${i}`}>
                <td>
                  <strong>{String(r.keyword || "—")}</strong>
                  {r.gap_flag ? <div className="pg-report-flag">Gap</div> : null}
                </td>
                <td>{fmtVol(r.volume)}</td>
                <td>{fmtNum(r.difficulty)}</td>
                <td>{r.cpc != null ? `$${Number(r.cpc).toFixed(2)}` : "—"}</td>
                <td>{r.opportunity_score != null ? `${r.opportunity_score}%` : "—"}</td>
                <td>{String(r.intent || "—")}</td>
                <td className="pg-report-small">
                  {positions.length
                    ? positions
                        .slice(0, 3)
                        .map((p) => `${String(p.domain)} (#${String(p.position)})`)
                        .join(", ")
                    : comps.slice(0, 3).join(", ") || "—"}
                </td>
                <td className="pg-report-small">
                  {titles.length
                    ? titles
                        .slice(0, 3)
                        .map((t) => String(t.title))
                        .join(" · ")
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SearchDemandReport({ data }: { data: Record<string, unknown> }) {
  const table = rows(data.keyword_table);
  const best = table.length ? table : rows(data.best_opportunities);
  const evergreen = rows(data.strong_evergreen);
  const plan = isObj(data.topic_plan) ? data.topic_plan : null;
  const ideas = rows(plan?.topic_ideas);
  const clusterReport = isObj(data.cluster_report) ? data.cluster_report : null;
  const clusters = rows(clusterReport?.clusters);

  return (
    <div className="pg-report">
      <MetaLine data={data} />
      <CompetitorSitesSection data={data} />
      <Section title="Best opportunities">
        <KeywordReportTable list={best} />
      </Section>
      {ideas.length ? (
        <Section title="Topic plan">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Title</th>
                  <th>Keyword</th>
                  <th>Volume</th>
                  <th>KD</th>
                  <th>Funnel</th>
                  <th>Competitor titles</th>
                </tr>
              </thead>
              <tbody>
                {ideas.slice(0, 12).map((t, i) => (
                  <tr key={`${String(t.title)}-${i}`}>
                    <td>{i + 1}</td>
                    <td>
                      <strong>{String(t.title || "—")}</strong>
                    </td>
                    <td>{String(t.keyword || "—")}</td>
                    <td>{fmtVol(t.volume)}</td>
                    <td>{fmtNum(t.difficulty)}</td>
                    <td>{String(t.funnel || "—")}</td>
                    <td className="pg-report-small">
                      {rows(t.serp_titles)
                        .slice(0, 2)
                        .map((x) => String(x.title))
                        .join(" · ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {evergreen.length ? (
        <Section title="Strong evergreen">
          <KeywordReportTable list={evergreen.slice(0, 8)} />
        </Section>
      ) : null}
      {clusters.length ? (
        <Section title="Clusters">
          {clusters.slice(0, 5).map((c, i) => (
            <div key={`${String(c.name)}-${i}`} className="pg-report-cluster">
              <strong>
                {String(c.name || c.primary_keyword || `Cluster ${i + 1}`)}
              </strong>
              <div className="pg-report-meta">
                Intent: {String(c.intent || "—")} · {String(c.recommended_content || c.content_type || "—")}
                {c.est_traffic != null || c.total_volume != null
                  ? ` · vol ${fmtVol(c.est_traffic ?? c.total_volume)}`
                  : ""}
              </div>
              <ul className="missing-list">
                {rows(c.keywords)
                  .slice(0, 5)
                  .map((k) => (
                    <li key={String(k.keyword)}>
                      {String(k.keyword)}
                      {k.volume != null ? ` · vol ${fmtVol(k.volume)}` : ""}
                      {k.difficulty != null ? ` · KD ${fmtNum(k.difficulty)}` : ""}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function CompetitorsReport({ data }: { data: Record<string, unknown> }) {
  const comps = rows(data.competitors || data.competitor_set || data.tier_overview);
  const tiers = isObj(data.tiers) ? data.tiers : null;
  const service =
    (isObj(data.service_level) && data.service_level) ||
    (isObj(data.service_level_comparison) && data.service_level_comparison) ||
    null;
  const baseline = isObj(data.baseline)
    ? data.baseline
    : isObj(data.client_baseline)
      ? data.client_baseline
      : null;

  return (
    <div className="pg-report">
      <MetaLine data={data} />
      {data.executive_summary ? (
        <Section title="Executive summary">
          <p className="pg-text">{String(data.executive_summary)}</p>
        </Section>
      ) : null}
      {baseline ? (
        <Section title="Client baseline">
          <PlaygroundBlocks value={baseline} />
        </Section>
      ) : null}
      {comps.length ? (
        <Section title="Competitor set">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>URL / Domain</th>
                  <th>Tier</th>
                  <th>Score</th>
                  <th>Cluster</th>
                </tr>
              </thead>
              <tbody>
                {comps.slice(0, 20).map((c, i) => (
                  <tr key={`${String(c.name || c.url)}-${i}`}>
                    <td>
                      <strong>{String(c.name || c.competitor || "—")}</strong>
                    </td>
                    <td className="pg-report-small">
                      {String(c.url || c.domain || c.website || "—")}
                    </td>
                    <td>{String(c.tier || c.tier_label || "—")}</td>
                    <td>{fmtNum(c.score ?? c.maturity_score ?? c.total)}</td>
                    <td>{String(c.positioning_cluster || c.cluster || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {service ? (
        <Section title="Service-level comparison">
          <PlaygroundBlocks value={service} />
        </Section>
      ) : null}
      {tiers ? (
        <Section title="Tiers">
          <PlaygroundBlocks value={tiers} />
        </Section>
      ) : null}
      {data.monitoring ? (
        <Section title="Monitoring">
          <PlaygroundBlocks value={data.monitoring} />
        </Section>
      ) : null}
    </div>
  );
}

function StrategyReport({ data }: { data: Record<string, unknown> }) {
  const calendarMonths = rows(data.content_calendar);
  const calendarFlat = rows(data.calendar_priorities);
  const coreTopics = rows(data.core_topics);
  const pillars = coreTopics.length ? coreTopics : rows(data.pillars);
  const gaps = rows(data.content_gaps || data.competitor_content_gaps);
  const priorityQueue = rows(data.priority_queue);
  const queue = priorityQueue.length ? priorityQueue : rows(data.priority_pages);
  const linking = rows(data.internal_linking);
  const weekRows: Array<Record<string, unknown>> = calendarMonths.flatMap((m) =>
    rows(m.weeks).map((w) => ({
      ...w,
      month: m.month,
      month_label: m.label,
    })),
  );
  return (
    <div className="pg-report">
      <MetaLine data={data} />
      {data._memory_slim ? (
        <p className="pg-report-flag">
          Shared memory shows essentials — open the Content Strategy card in chat for the full report.
        </p>
      ) : null}
      <CompetitorSitesSection data={data} />
      {data.executive_summary ? (
        <Section title="Executive summary">
          <p className="pg-text">{String(data.executive_summary)}</p>
        </Section>
      ) : null}
      {data.target_audience ? (
        <Section title="Target audience">
          <p className="pg-text">{String(data.target_audience)}</p>
        </Section>
      ) : null}
      {pillars.length ? (
        <Section title="Pillars / core topics">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Pillar</th>
                  <th>Primary keyword</th>
                  <th>Intent</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {pillars.slice(0, 12).map((p, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(p.pillar || p.name || "—")}</strong>
                    </td>
                    <td>{String(p.primary_keyword || "—")}</td>
                    <td>{String(p.intent || "—")}</td>
                    <td>{fmtNum(p.opportunity_score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {queue.length ? (
        <Section title="Priority queue">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Title</th>
                  <th>Keyword</th>
                  <th>Vol</th>
                  <th>Priority</th>
                  <th>Intent</th>
                </tr>
              </thead>
              <tbody>
                {queue.slice(0, 15).map((r, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td>
                      <strong>{String(r.title || r.keyword || "—")}</strong>
                    </td>
                    <td>{String(r.keyword || "—")}</td>
                    <td>{fmtVol(r.volume)}</td>
                    <td>{String(r.priority || r.priority_label || r.bucket || "—")}</td>
                    <td>{String(r.intent || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {weekRows.length ? (
        <Section title="Content calendar (12 weeks)">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Week</th>
                  <th>Month</th>
                  <th>Title</th>
                  <th>Keyword</th>
                  <th>Type</th>
                  <th>Priority</th>
                </tr>
              </thead>
              <tbody>
                {weekRows.slice(0, 12).map((r, i) => (
                  <tr key={i}>
                    <td>{String(r.week || "—")}</td>
                    <td>
                      {String(r.month || "—")}
                      {r.month_label ? ` · ${String(r.month_label)}` : ""}
                    </td>
                    <td>
                      <strong>{String(r.title || r.keyword || "—")}</strong>
                    </td>
                    <td>{String(r.keyword || "—")}</td>
                    <td>{String(r.content_type || "—")}</td>
                    <td>{String(r.priority || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : calendarFlat.length ? (
        <Section title="Calendar priorities">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Wave</th>
                  <th>Focus</th>
                  <th>Pages</th>
                </tr>
              </thead>
              <tbody>
                {calendarFlat.slice(0, 8).map((r, i) => (
                  <tr key={i}>
                    <td>{String(r.wave || r.month || "—")}</td>
                    <td>{String(r.focus || r.label || "—")}</td>
                    <td className="pg-report-small">
                      {Array.isArray(r.pages)
                        ? (r.pages as string[]).slice(0, 6).join(", ")
                        : String(r.keyword || "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {gaps.length ? (
        <Section title="Content gaps">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>Vol</th>
                  <th>Action</th>
                  <th>Competitors</th>
                </tr>
              </thead>
              <tbody>
                {gaps.slice(0, 10).map((g, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(g.keyword || "—")}</strong>
                    </td>
                    <td>{fmtVol(g.volume)}</td>
                    <td>{String(g.action || "—")}</td>
                    <td className="pg-report-small">
                      {Array.isArray(g.competitors)
                        ? (g.competitors as string[]).slice(0, 3).join(", ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {linking.length ? (
        <Section title="Internal linking">
          <PlaygroundBlocks value={linking.slice(0, 12)} />
        </Section>
      ) : null}
    </div>
  );
}

function ArchitectureReport({ data }: { data: Record<string, unknown> }) {
  const current =
    data.current_state && typeof data.current_state === "object"
      ? (data.current_state as Record<string, unknown>)
      : {};
  const issues = rows(current.issues);
  const tree = rows(data.target_url_tree);
  const ownership = rows(data.cluster_ownership);
  const nav =
    data.navigation && typeof data.navigation === "object"
      ? (data.navigation as Record<string, unknown>)
      : {};
  const primaryNav = rows(nav.primary_nav);
  const remediation = rows(nav.depth_remediation);
  const types = rows(data.page_type_model);
  const redirects = rows(data.redirect_map);
  const handoffs = rows(data.handoffs);
  const rollout =
    data.rollout_plan && typeof data.rollout_plan === "object"
      ? (data.rollout_plan as Record<string, unknown>)
      : null;
  const rolloutSeq = Array.isArray(rollout?.sequence) ? (rollout!.sequence as string[]) : [];

  return (
    <div className="pg-report">
      <MetaLine data={data} />
      {data._memory_slim ? (
        <p className="pg-report-flag">
          Shared memory shows essentials — open the Site Architecture card in chat for the full
          blueprint.
        </p>
      ) : null}
      <CompetitorSitesSection data={data} />
      {data.executive_summary ? (
        <Section title="Executive summary">
          <p className="pg-text">{String(data.executive_summary)}</p>
        </Section>
      ) : null}
      {data.competitor_ia_notes ? (
        <Section title="Competitor IA notes">
          <p className="pg-text">{String(data.competitor_ia_notes)}</p>
        </Section>
      ) : null}
      {data.gate ? (
        <p className="pg-report-meta">
          <strong>Gate:</strong> {String(data.gate)}
        </p>
      ) : null}

      {Object.keys(current).length ? (
        <Section title="Current state (click depth)">
          <p className="pg-report-meta">
            URLs: {fmtNum(current.urls_crawled)} · Max depth: {fmtNum(current.max_click_depth)} ·
            Depth 4+: {fmtNum(current.depth_4_plus)} · Orphans: {fmtNum(current.orphans)} · Phantoms:{" "}
            {fmtNum(current.phantom_dirs)}
          </p>
          {issues.length ? (
            <div className="pg-report-table-wrap">
              <table className="pg-report-table">
                <thead>
                  <tr>
                    <th>Issue</th>
                    <th>Count</th>
                    <th>Severity</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.slice(0, 8).map((i, idx) => (
                    <tr key={idx}>
                      <td>
                        <strong>{String(i.issue || "—")}</strong>
                      </td>
                      <td>{fmtNum(i.count)}</td>
                      <td>{String(i.severity || "—")}</td>
                      <td className="pg-report-small">{String(i.note || "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Section>
      ) : null}

      {types.length ? (
        <Section title="Page-type model">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Pattern</th>
                  <th>Parent</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {types.map((t, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(t.type || "—")}</strong>
                    </td>
                    <td className="pg-report-small">{String(t.url_pattern || "—")}</td>
                    <td>{String(t.parent || "—")}</td>
                    <td>{fmtNum(t.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {tree.length ? (
        <Section title="Target URL tree">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Path / URL</th>
                  <th>Depth</th>
                  <th>Keyword</th>
                </tr>
              </thead>
              <tbody>
                {tree.slice(0, 24).map((n, i) => (
                  <tr key={i}>
                    <td>{String(n.type || "—")}</td>
                    <td className="pg-report-small">
                      <strong>{String(n.path || n.url || n.absolute_url || "—")}</strong>
                    </td>
                    <td>{fmtNum(n.depth)}</td>
                    <td>{String(n.primary_keyword || n.keyword || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {primaryNav.length || remediation.length ? (
        <Section title="Navigation & depth remediation">
          {primaryNav.length ? (
            <div className="pg-report-table-wrap" style={{ marginBottom: 12 }}>
              <table className="pg-report-table">
                <thead>
                  <tr>
                    <th>Label</th>
                    <th>URL</th>
                    <th>Depth</th>
                  </tr>
                </thead>
                <tbody>
                  {primaryNav.map((n, i) => (
                    <tr key={i}>
                      <td>
                        <strong>{String(n.label || "—")}</strong>
                      </td>
                      <td className="pg-report-small">{String(n.url || "—")}</td>
                      <td>{fmtNum(n.depth)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {remediation.length ? (
            <div className="pg-report-table-wrap">
              <table className="pg-report-table">
                <thead>
                  <tr>
                    <th>URL</th>
                    <th>Depth</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {remediation.slice(0, 12).map((r, i) => (
                    <tr key={i}>
                      <td className="pg-report-small">{String(r.url || "—")}</td>
                      <td>{fmtNum(r.current_depth)}</td>
                      <td className="pg-report-small">{String(r.action || "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Section>
      ) : data.navigation ? (
        <Section title="Navigation">
          <PlaygroundBlocks value={data.navigation} />
        </Section>
      ) : null}

      {ownership.length ? (
        <Section title="Cluster ownership">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Cluster</th>
                  <th>Owner URL</th>
                  <th>Disposition</th>
                </tr>
              </thead>
              <tbody>
                {ownership.slice(0, 16).map((o, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(o.cluster || o.name || "—")}</strong>
                    </td>
                    <td className="pg-report-small">
                      {String(o.canonical_owner_url || o.owner_url || o.url || "—")}
                    </td>
                    <td>{String(o.disposition || o.intent || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {redirects.length ? (
        <Section title="Redirect map">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Old</th>
                  <th>New</th>
                  <th>Code</th>
                </tr>
              </thead>
              <tbody>
                {redirects.slice(0, 12).map((r, i) => (
                  <tr key={i}>
                    <td className="pg-report-small">{String(r.old_url || "—")}</td>
                    <td className="pg-report-small">{String(r.new_url || "—")}</td>
                    <td>{fmtNum(r.code || 301)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {rollout ? (
        <Section title="Rollout plan">
          {rolloutSeq.length ? (
            <ol className="missing-list" style={{ paddingLeft: 18 }}>
              {rolloutSeq.map((s, i) => (
                <li key={i} style={{ listStyle: "decimal", marginBottom: 4 }}>
                  {s}
                </li>
              ))}
            </ol>
          ) : (
            <PlaygroundBlocks value={rollout} />
          )}
        </Section>
      ) : null}

      {handoffs.length ? (
        <Section title="Handoffs">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Receiving</th>
                </tr>
              </thead>
              <tbody>
                {handoffs.map((h, i) => (
                  <tr key={i}>
                    <td>{String(h.item || "—")}</td>
                    <td>{String(h.receiving || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </div>
  );
}

function DiscoveryReport({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="pg-report">
      <Section title="Commercial & positioning">
        <PlaygroundBlocks value={data} />
      </Section>
    </div>
  );
}

function TrackingReport({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="pg-report">
      {data.system_access ? (
        <Section title="System access">
          <PlaygroundBlocks value={data.system_access} />
        </Section>
      ) : null}
      {data.historical_baseline ? (
        <Section title="Historical baseline">
          <PlaygroundBlocks value={data.historical_baseline} />
        </Section>
      ) : null}
      {data.known_changes ? (
        <Section title="Known changes">
          <PlaygroundBlocks value={data.known_changes} />
        </Section>
      ) : null}
      {!data.system_access && !data.historical_baseline && !data.known_changes ? (
        <PlaygroundBlocks value={data} />
      ) : null}
    </div>
  );
}

function WebsiteReport({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: [
            data.pages_found != null ? `Pages: ${String(data.pages_found)}` : "",
            data.indexable != null ? `Indexable: ${String(data.indexable)}` : "",
            data.broken_links != null ? `Broken: ${String(data.broken_links)}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        }}
      />
      <PlaygroundBlocks value={data} />
    </div>
  );
}

function TechnicalSeoReport({ data }: { data: Record<string, unknown> }) {
  const themes = rows(data.findings_by_theme);
  const backlog = rows(data.priority_backlog).length
    ? rows(data.priority_backlog)
    : rows(data.priority_fixes);
  const broken =
    data.broken_links && typeof data.broken_links === "object"
      ? (data.broken_links as Record<string, unknown>)
      : null;
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: [
            data.score != null ? `Score: ${String(data.score)}/100` : "",
            data.broken_link_count != null ? `Broken: ${String(data.broken_link_count)}` : "",
            data.severity ? `Severity: ${String(data.severity)}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        }}
      />
      {data.executive_summary ? (
        <Section title="Executive summary">
          <p className="pg-text">{String(data.executive_summary)}</p>
        </Section>
      ) : null}
      {themes.length ? (
        <Section title="Findings by theme">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Theme</th>
                  <th>Score</th>
                  <th>Findings</th>
                </tr>
              </thead>
              <tbody>
                {themes.slice(0, 12).map((t, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(t.theme || "—")}</strong>
                    </td>
                    <td>{fmtNum(t.score)}</td>
                    <td className="pg-report-small">
                      {Array.isArray(t.findings)
                        ? (t.findings as string[]).slice(0, 3).join(" · ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {broken ? (
        <Section title="Broken links">
          <p className="pg-report-meta">
            Total {String(broken.broken_count ?? 0)} · Internal{" "}
            {Array.isArray(broken.internal) ? broken.internal.length : 0} · External{" "}
            {Array.isArray(broken.external) ? broken.external.length : 0}
          </p>
        </Section>
      ) : null}
      {backlog.length ? (
        <Section title="Priority backlog">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Priority</th>
                  <th>Issue</th>
                  <th>Fix</th>
                </tr>
              </thead>
              <tbody>
                {backlog.slice(0, 12).map((b, i) => (
                  <tr key={i}>
                    <td>{String(b.priority || "—")}</td>
                    <td>
                      <strong>{String(b.issue || "—")}</strong>
                    </td>
                    <td className="pg-report-small">{String(b.fix || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </div>
  );
}

function ContentAuditReport({ data }: { data: Record<string, unknown> }) {
  const inventory = rows(data.inventory);
  const cannibalization = rows(data.cannibalization);
  const counts =
    data.summary_counts && typeof data.summary_counts === "object"
      ? (data.summary_counts as Record<string, number>)
      : null;
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: counts
            ? `Keep ${counts.keep ?? 0} · Refresh ${counts.refresh ?? 0} · Retire ${counts.retire ?? 0}`
            : inventory.length
              ? `Pages: ${inventory.length}`
              : "",
        }}
      />
      {inventory.length ? (
        <Section title="Inventory">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Path</th>
                  <th>Keyword</th>
                  <th>Disposition</th>
                </tr>
              </thead>
              <tbody>
                {inventory.slice(0, 15).map((r, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(r.title || "—")}</strong>
                    </td>
                    <td className="pg-report-small">{String(r.path || r.url || "—")}</td>
                    <td>{String(r.keyword || "—")}</td>
                    <td>{String(r.disposition || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {cannibalization.length ? (
        <Section title="Cannibalization">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>URLs</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {cannibalization.slice(0, 10).map((c, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(c.keyword || "—")}</strong>
                    </td>
                    <td className="pg-report-small">
                      {Array.isArray(c.urls) ? (c.urls as string[]).slice(0, 4).join(", ") : "—"}
                    </td>
                    <td>{String(c.action || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </div>
  );
}

function ContentPlanningReport({ data }: { data: Record<string, unknown> }) {
  const roadmap = rows(data.roadmap);
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: [
            data.planned_count != null ? `Planned: ${String(data.planned_count)}` : "",
            data.create_count != null ? `Create: ${String(data.create_count)}` : "",
            data.refresh_count != null ? `Refresh: ${String(data.refresh_count)}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        }}
      />
      {roadmap.length ? (
        <Section title="Roadmap">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Parent</th>
                  <th>Depth</th>
                  <th>Keyword</th>
                  <th>Funnel</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {roadmap.slice(0, 20).map((r, i) => (
                  <tr key={i}>
                    <td className="pg-report-small">{String(r.url || r.path || "—")}</td>
                    <td className="pg-report-small">{String(r.parent || "—")}</td>
                    <td>{fmtNum(r.depth)}</td>
                    <td>
                      <strong>{String(r.keyword || "—")}</strong>
                    </td>
                    <td>{String(r.funnel || "—")}</td>
                    <td>{String(r.action || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : (
        <PlaygroundBlocks value={data} />
      )}
    </div>
  );
}

function ContentProductionReport({ data }: { data: Record<string, unknown> }) {
  const briefs = rows(data.briefs);
  const drafts = rows(data.drafts);
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: [
            data.brief_count != null ? `Briefs: ${String(data.brief_count)}` : "",
            data.draft_count != null ? `Drafts: ${String(data.draft_count)}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        }}
      />
      {briefs.length ? (
        <Section title="Briefs">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>URL</th>
                  <th>Funnel</th>
                  <th>Words</th>
                  <th>Outline</th>
                </tr>
              </thead>
              <tbody>
                {briefs.slice(0, 12).map((b, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(b.keyword || "—")}</strong>
                    </td>
                    <td className="pg-report-small">{String(b.url || "—")}</td>
                    <td>{String(b.funnel || "—")}</td>
                    <td>{fmtNum(b.word_count)}</td>
                    <td className="pg-report-small">
                      {Array.isArray(b.outline)
                        ? (b.outline as string[]).slice(0, 4).join(" · ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {drafts.length ? (
        <Section title="Drafts">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Keyword</th>
                  <th>Funnel</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {drafts.slice(0, 12).map((d, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{String(d.title || "—")}</strong>
                    </td>
                    <td>{String(d.keyword || "—")}</td>
                    <td>{String(d.funnel || "—")}</td>
                    <td>{String(d.status || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </div>
  );
}

function OnPageSeoReport({ data }: { data: Record<string, unknown> }) {
  const pages = rows(data.pages).length ? rows(data.pages) : rows(data.queue);
  const links = rows(data.internal_links);
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: [
            pages.length ? `Pages: ${pages.length}` : "",
            links.length ? `Links: ${links.length}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        }}
      />
      {pages.length ? (
        <Section title="On-page queue">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Keyword</th>
                  <th>Title after</th>
                  <th>H1</th>
                </tr>
              </thead>
              <tbody>
                {pages.slice(0, 15).map((p, i) => {
                  const title =
                    p.title && typeof p.title === "object"
                      ? (p.title as Record<string, unknown>)
                      : {};
                  const headings =
                    p.headings && typeof p.headings === "object"
                      ? (p.headings as Record<string, unknown>)
                      : {};
                  return (
                    <tr key={i}>
                      <td className="pg-report-small">{String(p.url || p.path || "—")}</td>
                      <td>
                        <strong>{String(p.keyword || "—")}</strong>
                      </td>
                      <td className="pg-report-small">{String(title.after || "—")}</td>
                      <td>{String(p.h1 || headings.h1 || "—")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>
      ) : (
        <PlaygroundBlocks value={data} />
      )}
      {links.length ? (
        <Section title="Internal links">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>From</th>
                  <th>To</th>
                  <th>Anchor</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {links.slice(0, 15).map((l, i) => (
                  <tr key={i}>
                    <td className="pg-report-small">{String(l.from || "—")}</td>
                    <td className="pg-report-small">{String(l.to || "—")}</td>
                    <td>{String(l.anchor || "—")}</td>
                    <td className="pg-report-small">{String(l.target_status || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : (
        <Section title="Internal links">
          <p className="pg-report-muted">
            No link suggestions — lock Site Architecture / Content Planning, then re-run
            On-Page SEO.
          </p>
        </Section>
      )}
    </div>
  );
}

function PublishingReport({ data }: { data: Record<string, unknown> }) {
  const queue = rows(data.publish_queue);
  const gsc = rows(data.gsc_recrawl);
  const checklist = Array.isArray(data.publish_checklist)
    ? (data.publish_checklist as string[])
    : [];
  const indexnow = data.indexnow_preview || data.indexnow_payload;
  return (
    <div className="pg-report">
      <MetaLine
        data={{
          note: [
            queue.length ? `Queue: ${queue.length}` : "",
            data.cms_mode ? `CMS: ${String(data.cms_mode)}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        }}
      />
      {queue.length ? (
        <Section title="Publish queue">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Title</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {queue.slice(0, 15).map((q, i) => (
                  <tr key={i}>
                    <td className="pg-report-small">{String(q.url || "—")}</td>
                    <td>
                      <strong>{String(q.title || "—")}</strong>
                    </td>
                    <td>{String(q.status || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {indexnow && typeof indexnow === "object" ? (
        <Section title="IndexNow preview">
          <PlaygroundBlocks value={indexnow} />
        </Section>
      ) : null}
      {gsc.length ? (
        <Section title="GSC recrawl">
          <div className="pg-report-table-wrap">
            <table className="pg-report-table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Action</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {gsc.slice(0, 12).map((g, i) => (
                  <tr key={i}>
                    <td className="pg-report-small">{String(g.url || "—")}</td>
                    <td>{String(g.action || "—")}</td>
                    <td>{String(g.status || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
      {checklist.length ? (
        <Section title="Checklist">
          <ul className="missing-list">
            {checklist.slice(0, 10).map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

export default function PlaygroundPhaseReport({
  reportKind,
  data,
}: {
  reportKind?: string;
  data: unknown;
}) {
  if (data == null) return null;
  if (!isObj(data)) {
    return <PlaygroundBlocks value={data} />;
  }

  switch (reportKind) {
    case "search_demand":
      return <SearchDemandReport data={data} />;
    case "competitors":
      return <CompetitorsReport data={data} />;
    case "seo_strategy":
      return <StrategyReport data={data} />;
    case "site_architecture":
      return <ArchitectureReport data={data} />;
    case "technical_seo":
      return <TechnicalSeoReport data={data} />;
    case "content_audit":
      return <ContentAuditReport data={data} />;
    case "content_planning":
      return <ContentPlanningReport data={data} />;
    case "content_production":
      return <ContentProductionReport data={data} />;
    case "on_page_seo":
      return <OnPageSeoReport data={data} />;
    case "publishing":
      return <PublishingReport data={data} />;
    case "discovery":
      return <DiscoveryReport data={data} />;
    case "tracking":
      return <TrackingReport data={data} />;
    case "website":
      return <WebsiteReport data={data} />;
    default:
      return <PlaygroundBlocks value={data} />;
  }
}
