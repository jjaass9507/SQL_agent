---
name: styleseed
description: >-
  Apply senior UI/UX design judgment when building, reviewing, or refining any
  web/app interface, dashboard, landing page, slide deck, or UI component. Use
  whenever the user is generating front-end UI (React/Tailwind/HTML/CSS),
  vibe-coding a SaaS/fintech/dashboard app, complains that AI-generated UI
  "looks generated / amateur / off", wants a design system, brand skin, named
  motion, or a scored quality gate on a screen. Ported from bitjaru/styleseed
  (MIT) — a brand-agnostic design-method engine: 74 visual rules, coherence
  laws, motion vocabulary, UX writing, and per-domain/page playbooks.
license: MIT (bitjaru/styleseed)
---

# StyleSeed — Design Judgment Engine

StyleSeed teaches an AI to reason like a strong UI/UX designer. It fixes the
*judgment process*, not one fixed aesthetic. The single problem it solves:
AI-generated UI that is technically valid but "looks like an AI made it"
(default indigo, `#000` text, icon-chip-in-a-pale-card on every tile, all-even
grids with no focal point, rainbow status rows, sub-16px desktop body).

The rules are **brand-agnostic** — they reference semantic tokens, never fixed
colors — so the same judgment carries across any brand skin.

## When to use this

Reach for this skill whenever you are about to produce or critique UI:
building a page/component, scaffolding a dashboard, styling a landing page or
slide deck, or when the user says their UI looks generic and wants it fixed.

## The workflow (do this in order)

**1. Lock the design first (anti-drift).** Before writing UI code, settle:
one **key color** that fits the domain (NOT the generic indigo `#5E6AD2` /
`#4F46E5`), one **font**, one **radius** personality, one **motion** feel. If
the project is ongoing, record these in a `STYLESEED.md` at the repo root and
re-read it every time so the design stops changing session to session.

**2. Pick an output grammar for the surface.** A finance home, an ops console,
an editorial page, and a commerce detail page use *different* grammars — cards,
whitespace, rules, or tonal surfaces are tools, not a universal answer. See
`references/RULESETS.md` and `references/PRODUCT-PRINCIPLES.md`.

**3. Build to the rules.** Read the relevant reference files below before
generating, then apply them.

**4. Run the Quality Gate before showing anything.** Self-review and fix:
one accent (everything else greyscale), one radius, one shadow language,
normal states grey (color = severity only), a single dominant focal point per
screen, real empty/loading/error states, desktop body ≥16px. Only show UI that
passes.

## Golden rules (never break)

1. One accent color; everything else greyscale. A second hue needs semantic/brand meaning. Restraint reads as design.
2. No pure black `#000` — refined black is `#2A2A2A`, on a 5-step grey ramp.
3. Numbers 2:1 with their unit (48px value over 24px unit); equal sizes flatten magnitude.
4. One spatial rhythm on an 8px grid; gap-around-a-group > gap-inside it.
5. Never repeat the same section type consecutively — alternate tall/compact for rhythm.
6. One elevation language: LIGHT = layered shadows ≤8% opacity, lit from one direction; DARK = tonal surface ramp + hairline borders (shadows don't read).
7. Nested-radius law: `inner = outer − padding` so concentric corners agree.
8. Semantic tokens only in components (`text-brand`, `bg-card`) — never hardcode hex.
9. Status color = severity only; a normal row is grey, never a rainbow list.
10. No emoji as UI icons; one line-icon set in `currentColor`. Avoid Lucide-in-a-pale-chip on every card.
11. Tabular numbers for anything that updates (no width jitter).
12. One focal point per screen — the hero must dominate; an all-even grid of same-weight cards is the #1 "machine-composed" tell.
13. Match type scale to surface — desktop/web B2B body ≥16px; don't ship 14px body on a 1440px screen.
14. Touch targets ≥44×44px on touch surfaces; pointer-first desktop controls may be 36–40px, always with visible focus rings.

## Reference files (load what the task needs)

Core method:
- `references/PRODUCT-PRINCIPLES.md` — the constitution + authority order. Read first.
- `references/RULESETS.md` — output grammars selected by the result's job.
- `references/ARCHITECTURE.md` — engine flow and how the layers compose.

Craft & rules (read before building a screen):
- `references/DESIGN-LANGUAGE.md` — the 74 numbered visual rules (start at the ToC, then rules 14, 18, 19, 61–63).
- `references/VISUAL-CRAFT.md` — coherence laws (§C0 is the antidote to "looks off"), shadow/radius/type recipes, contrast floors.
- `references/METHODOLOGY.md` — *why* the rules exist; progressive disclosure, density, motion vocabulary.

Bias the rules to context:
- `references/APP-PLAYBOOKS.md` — per-domain bias (fintech, SaaS, e-commerce, social, health, dev-tools…).
- `references/PAGE-TYPES.md` — per-screen bias (dashboard, form, landing, detail, list, settings, onboarding).
- `references/ADAPTERS.md` — surface contracts for UI, carousels, decks, documents, reports.

Text & style:
- `references/UX-WRITING.md` — the words inside the UI (buttons that name the action, helpful errors, inviting empty states).
- `references/PRESETS.md` — optional aesthetic profiles (never a substitute for the grammar).
- `references/REFERENCE-COMPILER.md` — turning user-supplied visual references into a project-local grammar.
- `references/CLAUDE.md` — the original combined engine index (tokens, component API, forbidden patterns).

Command playbooks (the original `/ss-*` slash commands, as step-by-step instructions you can follow manually):
- `references/commands/` — e.g. `ss-setup`, `ss-page`, `ss-review`, `ss-score`, `ss-motion`, `ss-a11y`, `ss-copy`, `ss-feedback`. Read the matching one when a task lines up with it (e.g. scoring a screen → `ss-score.md`).

## Runnable material (assets/)

Beyond the judgment layer, the runnable starter is bundled under `assets/` — copy
what you need into a project instead of writing it from scratch:

- `assets/components/ui/` — 32 primitives (shadcn/ui + motion conventions).
- `assets/components/patterns/` — 16 dashboard/layout patterns (card grid, chart, list…).
- `assets/css/` — `base.css`, `fonts.css`, `index.css`.
- `assets/tokens/` — 6 JSON design-token files; `assets/tokens.ts` helper.
- `assets/motion/` — 5 motion seeds (`spring`, `silk`, `snap`, `float`, `pulse`) + keyword move library. Read `references/METHODOLOGY.md` ch.8 and `references/commands/ss-motion.md` for usage.
- `assets/icons/`, `assets/utils/` — custom SVG icon set + formatting utilities.
- `assets/scaffold/` — Vite 6 + React 18 starter shell.
- `assets/skins/` — 7 brand skins, each a `theme.css` (+ `skin.json`): `toss`, `stripe`, `linear`, `vercel`, `notion`, `raycast`, `arc`. Pick ONE, copy its `theme.css` in, and the semantic tokens do the rest. `_from-awesome-design-md/` holds extra fetched brands.

Tech stack these assume: React 18 · TypeScript · Tailwind CSS v4 · Radix UI · Vite 6 · Lucide · CVA.

## Notes

Ported from [bitjaru/styleseed](https://github.com/bitjaru/styleseed) (MIT). This
skill bundles both the design knowledge (`references/`) and the runnable material
(`assets/`). The judgment layer is the point — the assets are optional scaffolding
you copy in when you want the starter rather than hand-rolling components.
