# Realistic Brain Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `/brain` page's simple wireframe with a realistic translucent anatomical brain and animated neural overlay matching the approved reference.

**Architecture:** Keep the existing `BrainVisualization` public API and page layout. Add one optimized decorative image asset beneath a denser SVG neural graph, then tune component-specific CSS for depth, glow, motion, responsiveness, and reduced-motion behavior.

**Tech Stack:** React 19, TypeScript, SVG, CSS/Tailwind v4, Vite.

## Global Constraints

- No WebGL, Three.js, or new runtime dependencies.
- Preserve `name`, `lessonCount`, and `className` props.
- Preserve center labels and orbiting memory chips.
- Respect `prefers-reduced-motion`.
- The anatomical image is decorative and must not replace meaningful text.

---

### Task 1: Create the anatomical brain asset

**Files:**
- Create: `src/assets/brain/realistic-neural-brain.png`

- [ ] Generate a light-blue translucent anatomical brain on a transparent or near-white background, viewed from a slight three-quarter angle.
- [ ] Confirm the asset contains no text, UI chrome, watermark, or baked-in neural labels.
- [ ] Optimize dimensions for the card (roughly square, no unnecessary oversized source).

### Task 2: Layer the anatomy and neural graph

**Files:**
- Modify: `src/components/cinematic/BrainVisualization.tsx`

- [ ] Import the new image asset.
- [ ] Replace the dashed silhouette/fill paths with a decorative anatomy image layer.
- [ ] Expand the neural graph to approximately 12–20 contour-following nodes and connections.
- [ ] Keep the existing name, memory count, and chips unchanged.
- [ ] Keep all visual-only image/SVG content hidden from assistive technology.

### Task 3: Tune depth, motion, and responsive behavior

**Files:**
- Modify: `src/styles.css`
- Potentially modify: `src/routes/brain.tsx`

- [ ] Add anatomy sizing, opacity, glow, and blend treatment.
- [ ] Add a subtle 6–8 second breathing animation with only 2–3% scale variation.
- [ ] Tune synapses and nodes for visibility over the brighter brain.
- [ ] Verify chips do not collide with the center label at desktop and mobile widths.
- [ ] Add reduced-motion overrides that stop anatomy, node, synapse, scan, and orbit animations.
- [ ] Adjust the Intelligence Map card height only if the realistic brain is clipped.

### Task 4: Verify the result

**Files:**
- Verify: `src/components/cinematic/BrainVisualization.tsx`
- Verify: `src/styles.css`
- Verify: `src/routes/brain.tsx`

- [ ] Run `npm run lint` and fix newly introduced warnings/errors.
- [ ] Run `npm run build` and confirm the asset bundles correctly.
- [ ] Open `/brain` with seeded Chafic data and visually verify desktop/mobile layout.
- [ ] Emulate reduced motion and confirm the visual remains clear while animations stop.
- [ ] Compare against the reference: anatomical realism, soft blue translucency, readable labels, glowing graph.
