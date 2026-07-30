# Cinematic Council Tabs

## Goal

Transform the five model tabs in the empty Chat Council hero into a premium cinematic “Aurora Command Deck” while preserving the surrounding bright visual language.

## Scope

- Apply the treatment only to the five model tabs rendered in the empty Chat Council hero.
- Keep the shared `ModelPill` appearance unchanged in settings, search, and other application surfaces.
- Preserve model names, vendor names, official logos, selected checks, provider colors, grid behavior, and the “Choose your models” interaction.

## Visual Design

Each tab becomes a layered glass instrument panel:

- A translucent white-to-provider-tinted surface with a crisp illuminated border.
- A restrained provider-colored aurora behind the logo and a faint radial bloom near the lower edge.
- A subtle moving light sweep across the border/surface.
- A larger logo housed in a glass spotlight with depth shadow and a soft provider-colored halo.
- Sharper typography hierarchy: model name as the primary label and vendor as compact metadata.
- A luminous selected-status beacon in the upper-right corner.
- A tiny provider-colored signal line and status dot along the lower edge.

The five cards retain equal visual weight; no provider is presented as the recommended or dominant model.

## Motion and Interaction

- Cards enter with the existing staggered reveal, enhanced with slight perspective and blur resolving to clarity.
- Hover raises a card by a few pixels, increases its border light and aurora intensity, and gently scales the logo.
- A slow ambient shimmer moves across each card at offset timings so the row feels alive without synchronized flashing.
- No pointer-tracking or continuous JavaScript animation is required.
- `prefers-reduced-motion` removes shimmer and transform animation while preserving the complete static design.

## Architecture

Add a hero-only `variant="cinematic"` option to `ModelPill`. The default variant remains byte-for-byte compatible in appearance for all existing consumers. The Chat Council hero passes the cinematic variant and a stable card index for staggered ambient timing. Component-specific CSS classes and keyframes live in `src/styles.css`.

Provider color is exposed through CSS custom properties set by the component, allowing borders, glows, status signals, and shadows to share one source without dynamic Tailwind class generation.

## Responsive and Accessibility

- Keep the existing one-, two-, and five-column grid breakpoints.
- Prevent names and metadata from colliding with the selected-status beacon.
- Maintain sufficient text contrast against tinted glass.
- Keep the existing logo title and selected icon semantics.
- Decorative light layers remain hidden from assistive technology and do not intercept pointer events.

## Verification

- Add a focused pure helper test if class/style derivation is extracted; otherwise verify through TypeScript and lint.
- Run focused lint for changed files and the production build.
- Visually inspect desktop five-column, tablet two-column, and mobile one-column layouts.
- Verify hover, stagger, ambient shimmer, and reduced-motion behavior.

## Non-goals

- Redesigning answer cards, model search cards, settings, or model-set pages.
- Changing the hero copy or surrounding page layout.
- Adding canvas, WebGL, or a runtime animation dependency.
