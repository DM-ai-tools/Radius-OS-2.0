UI/UX Design

SearchFit Onboarding & Audit Agent — Chat-Based Agentic Model (Phase 1–4)

Version: 1.0
Date: August 4, 2026

---

1. Design Principles

- Conversation is the interface. There is one chat window per client engagement. All four agents (Discovery, Tracking & Access, Website Situation, Competitor & Market) are reached by talking, not by navigating between separate tool screens.
- Structured data lives inside the chat, not behind it. Tracking-health tables, audit findings, and competitor matrices render as inline cards within the message stream — the user never leaves the conversation to see the data behind a claim.
- Every AI claim is reviewable in place. Approve/edit/reject controls sit directly on the card that produced the claim, not on a separate review page.
- Progress is always visible. A persistent side panel shows the four-phase status and the overall readiness score at all times, so the user always knows what's done, what's pending, and what's blocking Phase 5.
- Nothing is silently automatic. Long-running work (crawls, backlink pulls, competitor scans) shows a live status line in the chat; findings never appear as already-approved.

2. Core Layout

```
+----------------------------------------------------------------+
|  Client: Acme Retail Co.        [Tier B]      Readiness: 62%   |
+---------------------------------------+------------------------+
|                                       |  PHASE STATUS PANEL     |
|   CHAT STREAM                        |  ---------------------- |
|   (Discovery / Tracking / Website /  |  1. Discovery      ✅   |
|    Competitor — one continuous       |  2. Tracking       🟡   |
|    thread, agent-labeled)            |  3. Website         ⏳  |
|                                       |  4. Competitor      —   |
|                                       |                          |
|                                       |  READINESS GATE          |
|                                       |  62% complete            |
|                                       |  Missing: GTM access,    |
|                                       |  backlink pull pending   |
|                                       |                          |
+---------------------------------------+------------------------+
|  [ message input ......................................  ➤ ]  |
+----------------------------------------------------------------+
```

- Left/main: the chat stream. Full scrollback across all four phases stays in one thread so context is never lost switching between agents — the user can say "go back to the tracking issue" and the agent has the history.
- Right: persistent phase-status panel. Always visible, collapses to an icon strip on narrow viewports.
- Bottom: single input, always available. The active agent is inferred from context and shown as a small label above the input (e.g., "Talking to: Website Situation Agent") rather than a mode switcher the user has to operate.

3. Message Types

| Type | Appearance | Used for |
|---|---|---|
| Agent message | Left-aligned, agent avatar + agent name label (color-coded per agent, see Section 6) | Normal conversational turns, explanations, questions to the user |
| User message | Right-aligned, plain | User/specialist input |
| System notice | Centered, muted, small | Phase transitions, job status changes ("Site crawl started — this will take about 3 minutes") |
| Structured card | Full-width block embedded in the stream | Tracking report, audit findings, competitor matrix, readiness score breakdown — see Section 4 |
| Human checkpoint card | Full-width block, distinct border color, action buttons attached | Anything requiring Approve / Edit / Reject before it becomes part of the Client Digital Profile |

4. Structured Cards (inline, per phase)

4.1 Discovery — Profile Summary Card

- Two columns: "From research" (pre-filled, editable) vs. "Confirmed by client."
- Discrepancy rows are visually flagged (e.g., left-border amber) with a one-line explanation ("Client stated 'premium' positioning; review sentiment analysis suggests price-sensitive buyers").
- Footer: Approve / Edit fields / Reject, restricted to Client Success Manager role.

4.2 Tracking & Access — Health Report Card

- Row-per-element table: GA4 base tag, conversion events, GTM container, Search Console access, cross-domain tracking.
- Status pill per row: Pass (green) / Warning (amber) / Fail (red) / Unverified (gray).
- Expandable row detail shows plain-language fix instructions.
- Footer: Approve baseline / Flag for client remediation, restricted to Technical SEO Specialist.

4.3 Website Situation — Audit Summary Card

- Three tabs within the card: Technical (crawl findings), Authority (backlink summary), Anomalies (traffic-drop detection with a small inline trend sparkline and the detected change-point date).
- Severity badges: Info / Warning / Critical.
- Footer: Approve / Edit / Reject, restricted to Technical SEO Specialist.

4.4 Competitor & Market — Landscape Card

- Competitor list with positioning-cluster labels (e.g., "Value players," "Premium/DTC") from the embedding-based clustering.
- Keyword-gap table (client vs. competitor rankings, sourced from GSC/Ahrefs — never LLM-estimated).
- Backlink/authority comparison bar (client vs. competitor set).
- Footer: Confirm competitor set / Approve landscape summary, restricted to SEO Strategist.

4.5 Readiness Score Card (side panel + inline on request)

- Radial or bar breakdown by phase (Discovery / Tracking / Website / Competitor), each weighted into the overall score.
- Named list of exactly what's missing or low-confidence, never just a number.
- "Ready for Phase 5" button only enabled, and only visible, to the SEO QA/Reporting Lead once all four phases are approved.

5. Agent "Thinking" / Working States

- Routing/planning (fast, sub-2s): a subtle animated three-dot indicator under the agent label, no separate message.
- Long-running jobs (crawl, backlink pull, competitor scan): a system notice with a live progress line ("Crawling acme-retail.com — 340 pages found, checking for redirects...") that updates in place rather than spamming new messages.
- Fallback triggered: shown transparently as a small inline note ("Ahrefs unavailable, retrying via Moz") — never hidden from the user, matching the platform's "everything is traceable" principle.

6. Visual & Brand Language

Reuses Traffic Radius's existing palette from the source architecture documents for continuity across TR's internal tools:

| Token | Color | Use |
|---|---|---|
| Primary (teal) | #028090 | Primary actions, links, Discovery agent label |
| Secondary (green-teal) | #00A896 | Tracking & Access agent label, pass states |
| Purple | #534AB7 | Website Situation agent label |
| Coral | #993C1D | Competitor & Market agent label, critical severity |
| Amber | #854F0B | Warning states, human-review checkpoints |
| Background | #F4F1EA | App background (warm off-white, matches TR docs) |
| Ink | #0B2E33 | Primary text |
| Muted | #5B6B70 | Secondary text, metadata |

Typography: serif display face (Cambria/Georgia) for headings and card titles, matching TR's existing document style; system sans-serif (Segoe UI/Calibri/Arial) for body and chat text, for readability at conversational density.

7. Human Checkpoint Interaction Pattern

Every checkpoint card follows the same three-button pattern, regardless of phase, so the behavior is predictable:

- Approve — writes the finding to `client_digital_profiles` as-is, logs `finding_approved` to the audit trail.
- Edit — opens the specific field(s) inline for correction, then approves the edited version, logs `finding_edited`.
- Reject — returns the item to the agent with the reviewer's note, agent re-runs that portion of the analysis; logs `finding_rejected`.

No card auto-dismisses. A pending checkpoint stays visible (and is also listed in the side panel) until acted on.

8. Accessibility & Responsive Behavior

- All status pills and severity badges pair color with a text label or icon — never color alone.
- Full keyboard navigation for approve/edit/reject actions (no drag interactions required for any core flow).
- Side panel collapses to a toggleable icon rail below 1024px width; chat stream remains full-width and primary on mobile/tablet.
- Structured cards reflow to single-column on narrow viewports; tables become horizontally scrollable within the card rather than truncating data.

9. Empty & Error States

- New client, no session yet: chat opens with the Discovery agent's automated pre-research already summarized as the first message, not a blank input box — the user is never staring at nothing to say.
- External API unreachable after fallback exhausted: system notice explains plainly what couldn't be verified ("Search Console access couldn't be confirmed — retried twice") and what the user can do (re-grant access, retry manually), never a raw error string.
- No competitors found automatically (Phase 4): card explicitly invites manual entry rather than silently returning an empty list.

10. Out of Scope for This UI

- No CMS editing surface (nothing in Phases 1–4 writes to a live site).
- No content drafting/editor UI (Phase 9+ concern).
- No standalone reporting dashboard beyond the readiness panel — recurring reporting (Phase 16) is a separate later interface.
