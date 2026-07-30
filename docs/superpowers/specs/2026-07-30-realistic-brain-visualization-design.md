# Realistic Brain Visualization Design

**Date:** 2026-07-30  
**Route:** `/brain`  
**Primary component:** `src/components/cinematic/BrainVisualization.tsx`

## Goal

Replace the current wireframe SVG brain on the Intelligence Map card with a realistic translucent anatomical brain that matches the reference screenshot, while keeping the existing card layout, center labels, and orbiting memory chips.

## Non-goals

- No WebGL / Three.js scene
- No new zoom/pan chrome (screenshot browser chrome is out of scope)
- No changes to brain API payload, cognitive profile sidebar, or left stat cards
- No dark-mode redesign

## Approach (approved)

**Layered realistic image + animated SVG neural overlay.**

1. Background: soft blue orbs (keep existing `brain-orb` treatment)
2. Midground: high-quality translucent anatomical brain asset (light blue / white, ethereal)
3. Foreground: SVG neural nodes + faint connection lines following brain contours
4. Overlay: center “Neural map / {name} / N memories indexed” copy
5. Orbit: existing memory chips (`+ velocity`, `− vague advice`, `90-day lens`)

## Visual requirements

- First viewport of the Intelligence Map card must read as a **realistic glowing brain**, not a dashed outline
- Palette stays light cinematic blue (existing primary `oklch(0.58 0.14 240)` family)
- Soft bloom / glow on nodes; subtle breathing scale (~2–3% over ~6–8s)
- Scan line may remain if it does not fight the anatomical read; prefer dimmer or optional
- Respect `prefers-reduced-motion`: stop orbit/breathing/pulse, show static brain + labels

## Asset strategy

- Prefer a single optimized WebP/PNG in `src/assets/` (or `public/`) of a translucent top-down / slight-3/4 brain
- Asset must be license-clear for product use; if no suitable stock asset is available in-repo, generate one via the image tool and store it under `src/assets/brain/`
- Image is decorative (`aria-hidden`); accessible meaning stays in the text overlay

## Component changes

### `BrainVisualization.tsx`

- Keep props: `name`, `lessonCount`, `className`
- Swap wireframe silhouette paths for:
  - `<img>` (or CSS background) of the anatomical brain
  - SVG overlay with denser node graph than today (roughly 12–20 nodes, more synapses)
- Preserve center label block and orbiting chips
- Soften or remove dashed outline paths

### `styles.css`

- Add `.brain-anatomy` (sizing, mix-blend / opacity, drop-shadow)
- Add `.brain-breathe` animation (disabled under reduced motion)
- Tune existing node/synapse glow so they read on top of a brighter photographic base

### `brain.tsx`

- No structural layout change unless the larger visual needs a slightly taller `GlassCard` min-height

## Motion budget

At least 2–3 intentional motions:

1. Soft breathe on the anatomy layer
2. Node pulse / synapse flicker
3. Orbiting chips (existing)

## Success criteria

- On `/brain`, the Intelligence Map center looks recognizably like the reference anatomical brain
- Name + memory count remain readable
- Chips still orbit without colliding with center text
- No new heavy dependencies
- Desktop and mobile both show a clear brain (chips may hide or simplify on very small widths if needed)

## Testing

- Manual: load `/brain` with seeded Chafic user; verify image load, labels, chips, reduced-motion
- Smoke: existing frontend build / typecheck still pass
