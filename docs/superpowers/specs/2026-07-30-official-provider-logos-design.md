# Official Provider Logos

## Goal

Replace the hand-drawn provider approximations with recognizable official brand marks everywhere model or provider logos appear in the application.

## Scope

- Support the providers represented by the built-in catalog: OpenAI, Anthropic, Google Gemini, xAI/Grok, DeepSeek, Mistral, Meta, and Alibaba/Qwen.
- Update all existing `VendorLogo` consumers through the shared component rather than changing each screen separately.
- Preserve current logo sizes, card layouts, hover effects, titles, and watermark rendering.
- Keep a neutral fallback mark for unknown or custom providers.

## Design

Store optimized official SVG assets in the repository and map normalized provider names and aliases to those assets in `VendorLogo`. Assets render locally without runtime network requests. The component remains the single interface used by chat, model cards, model sets, loading states, judge badges, and OpenRouter model management.

Provider aliases such as `Grok`/`xAI`, `Claude`/`Anthropic`, `Gemini`/`Google`, and `Qwen`/`Alibaba` resolve to the same official mark. Each asset retains its brand geometry and appropriate brand color treatment while fitting the existing rounded logo container.

## Behavior and Accessibility

- A supplied `title` gives the logo an accessible name and tooltip.
- Decorative and watermark logos remain hidden from assistive technology.
- Missing providers render the existing neutral fallback without broken-image UI.
- SVG files are bundled with the application and do not depend on third-party CDNs.

## Verification

- Add focused tests for provider normalization, aliases, official asset selection, fallback behavior, and accessible/decorative states.
- Run lint and the production build.
- Visually verify the five council cards plus representative small-logo and watermark usages at desktop and mobile widths.

## Non-goals

- Redesigning model cards or changing provider/model names.
- Fetching logos dynamically from OpenRouter or remote icon services.
- Adding new providers beyond those already represented by the application.
