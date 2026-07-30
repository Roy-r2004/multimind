# Cinematic Council Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the five empty-state Chat Council model tabs look like a premium Aurora Command Deck without changing other `ModelPill` consumers.

**Architecture:** Add an optional `variant="cinematic"` (plus stagger `index`) to `ModelPill`. Keep the default variant visually identical. Drive provider-colored glass, aurora, shimmer, and beacon styles via CSS custom properties and classes in `src/styles.css`. Wire the cinematic variant only from the Chat Council hero.

**Tech Stack:** React 19, TypeScript, Tailwind utility classes, CSS keyframes in `src/styles.css`.

## Global Constraints

- Hero-only treatment; default `ModelPill` appearance stays unchanged elsewhere.
- Preserve names, vendors, official logos, checks, colors, grid, and “Choose your models”.
- No canvas, WebGL, or new animation dependencies.
- Respect `prefers-reduced-motion`.

---

### Task 1: Cinematic `ModelPill` markup

**Files:**

- Modify: `src/components/cinematic/PageChrome.tsx`

**Interfaces:**

- Produces: `ModelPill({ name, vendor, color, subtitle?, variant?: "default" | "cinematic", index?: number })`

- [ ] **Step 1: Extend props and branch cinematic layout**

Keep the existing default return path unchanged. When `variant === "cinematic"`, render a layered glass card that:

- Sets `--pill-accent: color` on the root.
- Uses class `council-pill` plus `council-pill-delay-{min(index,4)}`.
- Includes decorative layers (`council-pill-aurora`, `council-pill-bloom`, `council-pill-sheen`) with `aria-hidden`.
- Shows a luminous check beacon (`council-pill-beacon`).
- Uses a larger logo (`size-11`) inside a glass spotlight shell.
- Shows sharper title/vendor hierarchy and a lower signal rail + status dot.

- [ ] **Step 2: Confirm default consumers still compile unchanged**

No call-site changes outside chat hero yet. TypeScript must accept optional props.

---

### Task 2: Aurora CSS motion and reduced-motion

**Files:**

- Modify: `src/styles.css`

- [ ] **Step 1: Add council-pill surface, glow, sheen, beacon, and delay classes**

Include:

- Glass surface with provider-tinted border/shadow via `color-mix` / `var(--pill-accent)`.
- Hover lift + stronger glow.
- Slow offset sheen animation.
- Entrance animation with slight blur/perspective.
- Delay variants 0–4.

- [ ] **Step 2: Gate animated transforms/sheen under `prefers-reduced-motion`**

Add `.council-pill`, `.council-pill-sheen`, and delay classes to the existing reduced-motion block so they become static.

---

### Task 3: Wire hero-only cinematic variant

**Files:**

- Modify: `src/routes/chat.tsx` (empty council hero `ModelPill` map only)

- [ ] **Step 1: Pass `variant="cinematic"` and `index`**

In the empty-state five-card grid only:

```tsx
<ModelPill
  name={model.name}
  vendor={model.vendor}
  color={model.color}
  variant="cinematic"
  index={index}
/>
```

Remove redundant outer `elevate-card` animation wrappers if the cinematic pill owns entrance motion, or keep the wrapper only if needed for layout. Prefer letting `council-pill` own stagger so motion does not double-fire.

---

### Task 4: Verify, commit, push

**Files:**

- Verify: `PageChrome.tsx`, `styles.css`, `chat.tsx`

- [ ] **Step 1: Lint changed files and run production build**
- [ ] **Step 2: Commit logo/spec/plan + cinematic UI together only if still uncommitted; otherwise commit UI + docs**
- [ ] **Step 3: Push to `origin/main`**
