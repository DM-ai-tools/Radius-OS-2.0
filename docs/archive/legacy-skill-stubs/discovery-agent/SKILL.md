---
name: discovery-agent
description: Run Phase 1 Business & Project Discovery for a Radius OS client — automated pre-research on the client's public footprint, a pre-filled client questionnaire, completeness scoring, and human sign-off that publishes to the Client Digital Profile. Use when the user says "start onboarding discovery for [client]", "run discovery", "show the business model questionnaire", or wants to begin a new client's SEO discovery phase.
---

# Discovery Agent — Phase 1: Business & Project Discovery

## Purpose

Closes the Phase 1 gap in the Radius OS skill catalog. Given a client (name + primary URL), produce a complete, human-approved Client Digital Profile — commercial scope and marketing context — without making the client fill out a blank form. Research first, ask only what only the client knows, score what's missing, surface disagreements, then hand off to a human for sign-off.

This skill never writes directly to `client_digital_profiles`. It only writes there after the `client_success_manager` role explicitly approves in step D4.

## When to use this skill

Trigger on any of:
- "Start onboarding discovery for [client]"
- "Run discovery"
- "Show the business model questionnaire"
- A new client record exists with `discovery_status = not_started` or `in_progress`

Do not trigger for existing clients whose `discovery_status = complete` — if the user asks to re-run discovery on a completed client, confirm they want to overwrite the existing profile before proceeding.

## Role requirements

- Any permitted role may start this skill (trigger discovery, view pre-research, fill the questionnaire).
- Only `client_success_manager` may execute the D4 sign-off that publishes to the Client Digital Profile. If a different role reaches the sign-off step, present the card as read-only and state that sign-off requires a Client Success Manager.

## Workflow

Run these five steps in order. Do not skip a step or merge D1 and D2 into one pass — the client should be confirming pre-researched answers, not starting from a blank page.

### D1 — Automated pre-research

1. Using the client's name and primary URL, research their public footprint: website content, Google Business Profile, social presence, visible reviews, and visible competitors.
2. Draft a first pass at: business model, product/service list, target market, and positioning statement.
3. Tag every drafted field with a confidence level (high / medium / low) based on how directly the source material supported it.
4. Write one row per field to `discovery_responses` with `source = pre_research`, `field_value` = the draft, and `confidence` set.
5. Render the findings as a summary card (do not present this as final — label it clearly as a draft awaiting client confirmation).

If web research is unavailable or returns nothing useful for a field, do not fabricate a value — leave that field explicitly blank and flagged "no research available," so D3 scoring catches it honestly.

### D2 — Targeted client questionnaire

1. Build a short questionnaire covering only what the client alone knows: revenue split, average order value, sales cycle length, seasonality, real objectives (present as checkboxes, not a free-text field), legal/compliance constraints, and marketing spend outside of SEO.
2. Pre-fill every field D1 already drafted a value for; leave only genuinely client-only fields blank.
3. Present this as an editable card — the client is confirming or correcting, not starting from zero.
4. On submission, write one row per field to `discovery_responses` with `source = client_questionnaire`.

### D3 — Completeness scoring

1. Run the weighted-readiness scoring model across all `discovery_responses` rows for this client (both sources).
2. Compute a 0–100 completeness score and write it to `readiness_scores` with `phase = discovery`, including a `missing_fields` list naming exactly which fields are missing or still low-confidence — never just a bare number.
3. If the score is below the phase-complete threshold, say so plainly and name what's missing; do not proceed to D4 silently short of complete data.

### D4 — Human sign-off

1. Build a sign-off card that compares D1 (research) against D2 (client-confirmed) side by side.
2. Explicitly flag any discrepancy between what the client stated and what independent research found (e.g., a client claiming "premium" positioning against review sentiment suggesting price-sensitive buyers). Surface this as insight for the reviewer, not as an error to silently resolve.
3. Present three actions to the `client_success_manager`: **Approve**, **Edit fields**, **Reject** (send specific fields back to D1/D2 for re-research or re-asking).
4. Do not proceed to D5 without an explicit Approve or Edit-then-approve action. Nothing here auto-applies regardless of confidence score.

### D5 — Publish to Client Digital Profile

1. On approval, write the finalized fields into `client_digital_profiles.commercial_scope` and `client_digital_profiles.marketing_context`.
2. Set `discovery_status = complete` on the client's profile.
3. Log the approval to `audit_trail` with the reviewing user, timestamp, and a reference to the approved `discovery_responses` rows.
4. Recompute `overall_readiness_score` so the readiness-gate panel reflects the new phase completion immediately.

## Output format

Every response in this skill renders as a structured card, not a wall of prose (see UI/UX spec, Discovery Profile Summary Card):
- **Pre-research summary (D1):** two-column layout — field name, drafted value, confidence tag. Labeled "Draft — awaiting confirmation."
- **Questionnaire (D2):** editable form, pre-filled, client-only fields visibly distinct from pre-filled ones.
- **Completeness score (D3):** score out of 100 plus a named list of what's missing — never a lone percentage.
- **Sign-off card (D4):** side-by-side research vs. client answer, discrepancies visually flagged, Approve / Edit / Reject actions attached.

## Guardrails

- Never write to `client_digital_profiles` outside of D5, and never from any role other than `client_success_manager`.
- Never present a drafted (D1) value as confirmed fact — always label its source and confidence until the client has actually confirmed it in D2.
- Never suppress a discrepancy to make the profile look cleaner — flagging conflicts is the point of this skill, not a failure state.
- If discovery is re-run on a client that already has `discovery_status = complete`, treat it as a refresh: keep prior approved data visible for comparison rather than silently overwriting it.

## Example interaction

```
User: Start onboarding discovery for Acme Retail

Agent: Researching Acme Retail's public footprint...
[renders D1 pre-research summary card — business model, products,
positioning, each tagged with confidence]

This is a first-pass draft. Here's the questionnaire, pre-filled from
research — please confirm or correct anything that's off.
[renders D2 questionnaire card]

User: [submits questionnaire]

Agent: Discovery is 92% complete. Missing: seasonality detail for Q4
promotions.
[renders D3 completeness note]

One thing worth flagging before sign-off: you described the brand as
"premium," but the reviews we found lean price-sensitive — worth
confirming that's intentional positioning rather than a gap.
[renders D4 sign-off card with discrepancy flagged, Approve/Edit/Reject]

User: [Approves as Client Success Manager]

Agent: Discovery approved and published to Acme Retail's Client Digital
Profile. Discovery phase is now complete — ready for Phase 2 (Tracking
& Access) whenever you are.
```
