# Radius OS Classy Minimal UI Spec

This spec defines a premium, minimal interface for a phase-based autonomous workflow product where approval gates are the core UX action.

## 1) Product Interaction Model

- Primary model: `pipeline control room`, not chat.
- Core user loop: `Run phase -> Review evidence -> Approve -> Continue`.
- Chat is secondary: optional context and Q&A drawer.
- Default screen objective: show what is blocked, what is ready, and the single next action.

## 2) Information Architecture

- `Overview` (default): phase board with progression and blockers.
- `Phase Detail`: focused review surface per phase.
- `Approvals`: queue of pending signoffs.
- `Connections`: OAuth, CMS, provider setup (optional utilities).
- `Reports`: export and presentation views.

## 3) Screen Blueprint

## Overview: Pipeline Control Room

- Left rail: clients and account switch.
- Main: 12-phase board (group by Discovery / Strategy / Execution / Publishing).
- Right panel: selected phase summary with `Run` or `Approve`.
- Top bar: readiness, role, environment status.

## Phase Detail

- Header: phase title, status, owner, last run.
- Content sections:
  - evidence snapshot
  - validation notes
  - confidence/risk indicators
  - revision history
- Footer CTA row:
  - primary: `Approve & Continue`
  - secondary: `Request Revision`, `Re-run`

## Approvals Queue

- Dense list of pending signoffs across clients.
- Each row: phase, summary, blocker count, assigned role, due status.
- One-click open into review panel.

## 4) Visual Language (Classy Glass)

- Dark neutral base with cool accent.
- Subtle glass only; avoid bright neon and heavy glow.
- Soft depth through blur + border, not through saturated shadows.

### Core Tokens

- Background: `#0A0F1F`, `#11182B`, `#1A2238`
- Text: `#E9EEFC`
- Muted text: `#AEB8D4`
- Accent: `#9AB4FF`
- Border: `rgba(233, 238, 252, 0.16)`
- Glass surface: `rgba(255, 255, 255, 0.09)`
- Radius: `12 / 16 / 20`
- Blur: `18px`

### Typography

- Keep existing stack (`Fraunces` display, `Manrope` body).
- Increase whitespace around headings; reduce all-caps usage to labels only.
- 4 text sizes for app shell:
  - `meta`
  - `body`
  - `section`
  - `display`

## 5) Components

- `PhaseCard`
  - state badges: waiting, running, needs approval, approved, blocked
  - single clear action
- `ApprovalPanel`
  - evidence summary + pass/fail details + final signoff
- `StatusBadge`
  - semantic color, low saturation
- `GlassTopbar`
  - sticky, translucent, compact
- `ActionBar`
  - one primary CTA max per screen

## 6) Motion

- Use calm motion only:
  - hover lift: `translateY(-1px)`
  - panel open: `180-220ms ease`
  - no bouncy transitions
- Respect reduced motion preference.

## 7) Accessibility Rules

- Maintain contrast >= 4.5 for core text.
- Avoid text over noisy background without overlay.
- Keep focus ring clear and visible on glass surfaces.
- All status colors must include text labels, not color only.

## 8) Implementation Plan (Low Risk)

1. Introduce a scoped theme class (`theme-classy`) with token overrides.
2. Convert container surfaces first:
   - topbar
   - phase panel
   - cards
3. Update action hierarchy:
   - enforce one primary CTA per section
4. Add approvals queue view.
5. Shift chat entry to optional drawer.

## 9) Included in Codebase

- Theme styles: `frontend/src/theme-classy.css`
- Theme import: `frontend/src/main.tsx`

To enable class-based rollout, apply `theme-classy` at root shell (for example on `body` or the top app container), then migrate screen by screen.
