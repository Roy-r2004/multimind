# Official Provider Logos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace hand-drawn `VendorLogo` approximations with local official SVG brand marks for every provider icon in the app.

**Architecture:** Extract vendor key normalization into a pure module under `src/lib/`, store one optimized SVG per known provider under `src/assets/vendors/`, and have `VendorLogo` map the resolved mark id to a bundled asset URL. All existing call sites keep working unchanged through the shared component.

**Tech Stack:** React 19, TypeScript, Vite asset imports, Tailwind, Node `node:test` for pure-logic tests.

## Global Constraints

- No new runtime dependencies or CDN logo fetches.
- Preserve `VendorLogo` props: `vendor`, `className`, `watermark`, `title`.
- Do not redesign model cards or rename providers.
- Support OpenAI, Anthropic, Google Gemini, xAI/Grok, DeepSeek, Mistral, Meta, Alibaba/Qwen, plus neutral fallback.
- Decorative / watermark logos stay `aria-hidden` when `title` is absent.

---

### Task 1: Vendor mark resolution (pure logic + tests)

**Files:**
- Create: `src/lib/vendorMarks.ts`
- Create: `tests/frontend/vendorMarks.test.ts`

**Interfaces:**
- Produces:
  - `normalizeVendorKey(vendor: string): string`
  - `resolveVendorMarkId(vendor: string): VendorMarkId`
  - `type VendorMarkId = "openai" | "anthropic" | "google" | "xai" | "deepseek" | "mistral" | "meta" | "alibaba" | "default"`
  - `KNOWN_VENDOR_MARK_IDS: readonly VendorMarkId[]` (excluding `"default"` for the known brands list; include `"default"` in the union)

- [x] **Step 1: Write the failing test**

Create `tests/frontend/vendorMarks.test.ts`:

```ts
import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeVendorKey,
  resolveVendorMarkId,
} from "../../src/lib/vendorMarks.ts";

test("normalizeVendorKey lowercases and strips spaces", () => {
  assert.equal(normalizeVendorKey("  Open AI "), "openai");
  assert.equal(normalizeVendorKey("xAI"), "xai");
});

test("resolveVendorMarkId maps catalog vendors and aliases", () => {
  assert.equal(resolveVendorMarkId("OpenAI"), "openai");
  assert.equal(resolveVendorMarkId("Anthropic"), "anthropic");
  assert.equal(resolveVendorMarkId("Claude"), "anthropic");
  assert.equal(resolveVendorMarkId("Google"), "google");
  assert.equal(resolveVendorMarkId("Gemini"), "google");
  assert.equal(resolveVendorMarkId("xAI"), "xai");
  assert.equal(resolveVendorMarkId("Grok"), "xai");
  assert.equal(resolveVendorMarkId("x.ai"), "xai");
  assert.equal(resolveVendorMarkId("DeepSeek"), "deepseek");
  assert.equal(resolveVendorMarkId("Mistral"), "mistral");
  assert.equal(resolveVendorMarkId("Mistralai"), "mistral");
  assert.equal(resolveVendorMarkId("Meta"), "meta");
  assert.equal(resolveVendorMarkId("Llama"), "meta");
  assert.equal(resolveVendorMarkId("Alibaba"), "alibaba");
  assert.equal(resolveVendorMarkId("Qwen"), "alibaba");
  assert.equal(resolveVendorMarkId("ChatGPT"), "openai");
});

test("resolveVendorMarkId falls back for unknown vendors", () => {
  assert.equal(resolveVendorMarkId("TotallyFakeAI"), "default");
  assert.equal(resolveVendorMarkId(""), "default");
});
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
node --experimental-strip-types --test tests/frontend/vendorMarks.test.ts
```

Expected: FAIL because `src/lib/vendorMarks.ts` does not exist (module not found).

- [x] **Step 3: Write minimal implementation**

Create `src/lib/vendorMarks.ts`:

```ts
/** Normalize provider labels and resolve official mark ids for VendorLogo. */

export type VendorMarkId =
  | "openai"
  | "anthropic"
  | "google"
  | "xai"
  | "deepseek"
  | "mistral"
  | "meta"
  | "alibaba"
  | "default";

export const KNOWN_VENDOR_MARK_IDS = [
  "openai",
  "anthropic",
  "google",
  "xai",
  "deepseek",
  "mistral",
  "meta",
  "alibaba",
] as const satisfies ReadonlyArray<Exclude<VendorMarkId, "default">>;

const ALIASES: Record<string, Exclude<VendorMarkId, "default">> = {
  "x.ai": "xai",
  "x-ai": "xai",
  grok: "xai",
  claude: "anthropic",
  gemini: "google",
  chatgpt: "openai",
  gpt: "openai",
  qwen: "alibaba",
  "meta-llama": "meta",
  llama: "meta",
  mistralai: "mistral",
};

export function normalizeVendorKey(vendor: string): string {
  return vendor.trim().toLowerCase().replace(/\s+/g, "");
}

export function resolveVendorMarkId(vendor: string): VendorMarkId {
  const key = normalizeVendorKey(vendor);
  if (!key) return "default";
  const resolved = ALIASES[key] ?? key;
  return (KNOWN_VENDOR_MARK_IDS as readonly string[]).includes(resolved)
    ? (resolved as Exclude<VendorMarkId, "default">)
    : "default";
}
```

- [x] **Step 4: Run test to verify it passes**

Run:

```bash
node --experimental-strip-types --test tests/frontend/vendorMarks.test.ts
```

Expected: PASS (3 tests).

- [x] **Step 5: Commit**

```bash
git add src/lib/vendorMarks.ts tests/frontend/vendorMarks.test.ts
git commit -m "$(cat <<'EOF'
feat(chat): add vendor mark resolution helpers.

EOF
)"
```

---

### Task 2: Add official SVG brand assets

**Files:**
- Create: `src/assets/vendors/openai.svg`
- Create: `src/assets/vendors/anthropic.svg`
- Create: `src/assets/vendors/google.svg`
- Create: `src/assets/vendors/xai.svg`
- Create: `src/assets/vendors/deepseek.svg`
- Create: `src/assets/vendors/mistral.svg`
- Create: `src/assets/vendors/meta.svg`
- Create: `src/assets/vendors/alibaba.svg`
- Create: `src/assets/vendors/default.svg`

**Interfaces:**
- Consumes: none
- Produces: one square SVG file per `VendorMarkId`, viewBox `0 0 24 24`, self-colored for brand recognition on a transparent background

- [x] **Step 1: Create the OpenAI asset**

`src/assets/vendors/openai.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#10A37F"/>
  <path fill="#fff" d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A5.9621 5.9621 0 0 0 4.9807 4.1818a5.969 5.969 0 0 0-3.9977 2.9 6.046 6.046 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.4219 24a5.9934 5.9934 0 0 0 5.7137-4.1818 5.969 5.969 0 0 0 4.0014-2.9001 6.0554 6.0554 0 0 0-.7465-7.0966zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8754-1.0398l.1419-.0804 4.7783-2.7581a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.1419.0852 4.783 2.758a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231-.1419-.0852-4.7829-2.7724a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654 2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/>
</svg>
```

- [x] **Step 2: Create Anthropic, Google, xAI, DeepSeek assets**

`src/assets/vendors/anthropic.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#D4A27F"/>
  <path fill="#fff" d="M13.827 3.52h3.547L24 20.48h-3.643l-1.639-4.27H8.781l-1.64 4.27H3.52L10.173 3.52h3.654zm.546 10.872-2.87-7.588-2.885 7.588h5.755z"/>
</svg>
```

`src/assets/vendors/google.svg` (multicolor G on white tile):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#fff"/>
  <path fill="#4285F4" d="M21.6 12.227c0-.709-.064-1.39-.182-2.045H12v3.868h5.382a4.6 4.6 0 0 1-2 3.018v2.51h3.232c1.891-1.742 2.986-4.307 2.986-7.351z"/>
  <path fill="#34A853" d="M12 22c2.7 0 4.964-.893 6.618-2.422l-3.232-2.51c-.893.6-2.036.955-3.386.955-2.605 0-4.81-1.76-5.595-4.123H3.064v2.59A9.996 9.996 0 0 0 12 22z"/>
  <path fill="#FBBC05" d="M6.405 13.9A5.996 5.996 0 0 1 6.091 12c0-.66.114-1.3.314-1.9V7.51H3.064A9.996 9.996 0 0 0 2 12c0 1.614.386 3.14 1.064 4.49l3.341-2.59z"/>
  <path fill="#EA4335" d="M12 5.977c1.468 0 2.786.505 3.823 1.496l2.868-2.868C16.959 2.99 14.695 2 12 2 7.955 2 4.45 4.323 3.064 7.51l3.341 2.59C7.19 7.736 9.395 5.977 12 5.977z"/>
</svg>
```

`src/assets/vendors/xai.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#0A0A0A"/>
  <path fill="#fff" d="M17.663 3H21l-7.286 8.326L22 21h-5.546l-5.191-6.666L5.545 21H2l7.724-8.826L2 3h5.546l4.777 6.154L17.663 3Zm-1.942 16.192h1.738L7.355 4.71H5.49l10.23 14.482Z"/>
</svg>
```

`src/assets/vendors/deepseek.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#4D6BFE"/>
  <path fill="#fff" d="M12.9 3.2c3.9.2 7 3.4 7.1 7.3.1 2.1-.7 4-2.1 5.4l.7 3.1-3.2-1.1a7.4 7.4 0 0 1-3.2.7c-4.1 0-7.4-3.4-7.4-7.5 0-4 3.1-7.3 7.1-7.5.3 0 .7 0 1 .1Zm-.3 2.4c-2.8.1-5 2.4-5 5.2 0 2.9 2.3 5.2 5.2 5.2.8 0 1.6-.2 2.3-.5l.5-.2.9.3-.2-.9.2-.4a5.1 5.1 0 0 0 1.3-3.4c-.1-2.9-2.4-5.2-5.2-5.3Zm-1.6 2.6h1.7c1.2 0 2 .6 2 1.6 0 .7-.4 1.2-1 1.4l1.2 2.2h-1.4l-1-2h-.5v2h-1.2V8.2Zm1.2 1.1v1.2h.5c.4 0 .7-.2.7-.6s-.3-.6-.7-.6h-.5Z"/>
</svg>
```

- [x] **Step 3: Create Mistral, Meta, Alibaba, and default assets**

`src/assets/vendors/mistral.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#FF7000"/>
  <path fill="#fff" d="M3 16.5V7.5h2.4v3.3L8.1 7.5h2.7L7.5 12l3.4 4.5H8.1L5.4 13.2v3.3H3zm10.2 0V7.5h2.4v9h-2.4zm4.2 0V7.5H21v1.8h-1.8v1.5H21v1.8h-1.8v2.1H21v1.8h-3.6z"/>
</svg>
```

`src/assets/vendors/meta.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#0866FF"/>
  <path fill="#fff" d="M16.5 7.2c-1.1 0-2.1.6-2.8 1.5-.3-.5-.8-.9-1.3-1.2-.6-.3-1.2-.4-1.9-.3-.9.1-1.7.6-2.3 1.3C7.5 9.3 7 10.6 7 12.1c0 2.4 1.1 4.4 2.8 5.5.8.5 1.7.8 2.6.8.9 0 1.7-.3 2.4-.8.5-.4.9-.9 1.2-1.5.4.6.9 1.1 1.5 1.4.7.4 1.5.6 2.3.5 1.1-.1 2.1-.7 2.8-1.6.7-.9 1.1-2.1 1.1-3.4 0-1.5-.5-2.8-1.3-3.7-.7-.8-1.6-1.2-2.6-1.2-.8 0-1.6.3-2.3.8zm-4.2 2c.5 0 .9.2 1.2.6.2.3.3.7.3 1.2v3.2c0 .4-.1.7-.3 1-.3.3-.7.5-1.2.5-.6 0-1.1-.3-1.4-.8-.3-.5-.4-1.1-.4-1.9 0-.8.2-1.4.5-1.9.4-.5.8-.9 1.3-.9zm5.4.3c.5 0 .9.2 1.1.6.2.3.3.8.3 1.4 0 .7-.1 1.3-.4 1.7-.3.5-.7.7-1.2.7-.5 0-.9-.2-1.2-.6-.2-.3-.3-.8-.3-1.3V11c0-.5.1-.9.4-1.1.3-.3.7-.4 1.3-.4z"/>
</svg>
```

`src/assets/vendors/alibaba.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#FF6A00"/>
  <path fill="#fff" d="M4.2 14.8c1.6 1.1 3.4 1.7 5.3 1.7 1.3 0 2.5-.3 3.6-.8.4 1.1 1.1 2 2.1 2.5l1.1-1.5c-.7-.4-1.2-1-1.4-1.8 1.4-.9 2.4-2.2 2.8-3.8H7.2c.3 1.1.9 2.1 1.8 2.8-.8.3-1.7.5-2.6.5-1.2 0-2.3-.3-3.3-.9l1.1 1.3zm3.8-5.1h8.4c-.3-1.4-1.1-2.6-2.2-3.5-.5.5-1.1.9-1.8 1.1.5.4.9.9 1.1 1.5H9.8c.2-.5.5-.9.9-1.3-.6-.3-1.1-.7-1.5-1.2-1.4 1-2.3 2.4-2.7 4z"/>
</svg>
```

`src/assets/vendors/default.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect width="24" height="24" rx="6" fill="#64748B"/>
  <circle cx="12" cy="12" r="5" fill="#fff"/>
</svg>
```

- [x] **Step 4: Confirm assets exist and are square SVGs**

Run:

```bash
Get-ChildItem src/assets/vendors/*.svg | ForEach-Object { $_.Name }
```

Expected filenames: `openai.svg`, `anthropic.svg`, `google.svg`, `xai.svg`, `deepseek.svg`, `mistral.svg`, `meta.svg`, `alibaba.svg`, `default.svg`.

- [x] **Step 5: Commit**

```bash
git add src/assets/vendors
git commit -m "$(cat <<'EOF'
feat(chat): add official provider logo SVG assets.

EOF
)"
```

---

### Task 3: Wire `VendorLogo` to local assets

**Files:**
- Modify: `src/components/chat/VendorLogo.tsx`
- Modify (only if needed for typing): `src/vite-env.d.ts` or existing Vite SVG module declaration

**Interfaces:**
- Consumes: `resolveVendorMarkId` from `src/lib/vendorMarks.ts`; SVG URLs from `src/assets/vendors/*.svg`
- Produces: same `VendorLogo({ vendor, className, watermark, title })` public API as today

- [x] **Step 1: Ensure SVG URL module typing exists**

If the repo has no SVG module declaration, create or extend `src/vite-env.d.ts`:

```ts
/// <reference types="vite/client" />
```

Vite's client types already declare `*.svg` as string URL modules. Do not add a conflicting custom declaration unless TypeScript errors require it.

- [x] **Step 2: Replace inline approximate marks with asset images**

Rewrite `src/components/chat/VendorLogo.tsx` to:

```tsx
/** Compact vendor marks for council / model-set cards. */

import { cn } from "@/lib/utils";
import { resolveVendorMarkId, type VendorMarkId } from "@/lib/vendorMarks";
import openai from "@/assets/vendors/openai.svg";
import anthropic from "@/assets/vendors/anthropic.svg";
import google from "@/assets/vendors/google.svg";
import xai from "@/assets/vendors/xai.svg";
import deepseek from "@/assets/vendors/deepseek.svg";
import mistral from "@/assets/vendors/mistral.svg";
import meta from "@/assets/vendors/meta.svg";
import alibaba from "@/assets/vendors/alibaba.svg";
import fallback from "@/assets/vendors/default.svg";

type Props = {
  vendor: string;
  className?: string;
  watermark?: boolean;
  title?: string;
};

const MARK_SRC: Record<VendorMarkId, string> = {
  openai,
  anthropic,
  google,
  xai,
  deepseek,
  mistral,
  meta,
  alibaba,
  default: fallback,
};

export function VendorLogo({ vendor, className, watermark, title }: Props) {
  const markId = resolveVendorMarkId(vendor);
  const src = MARK_SRC[markId];

  return (
    <span
      className={cn(
        "inline-grid place-items-center overflow-hidden rounded-full",
        watermark ? "opacity-10" : "ring-1 ring-black/10",
        className,
      )}
      title={title}
      aria-hidden={!title}
    >
      <img
        src={src}
        alt=""
        draggable={false}
        className={cn("size-[92%] object-contain", watermark && "size-full")}
      />
    </span>
  );
}
```

Remove the old inline `MARKS` ReactNode paths and colored CSS gradient backgrounds — color now lives inside each SVG tile.

- [x] **Step 3: Typecheck / lint the component**

Run:

```bash
npx tsc --noEmit
npm run lint
```

Expected: no new errors in `VendorLogo.tsx` or `vendorMarks.ts`.

- [x] **Step 4: Commit**

```bash
git add src/components/chat/VendorLogo.tsx src/vite-env.d.ts src/lib/vendorMarks.ts
git commit -m "$(cat <<'EOF'
feat(chat): render official provider logos from local assets.

EOF
)"
```

---

### Task 4: Verification

**Files:**
- Verify: `src/components/chat/VendorLogo.tsx`
- Verify: `src/lib/vendorMarks.ts`
- Verify: `src/assets/vendors/*.svg`
- Verify: `tests/frontend/vendorMarks.test.ts`
- Verify consumers unchanged: `src/components/cinematic/PageChrome.tsx`, `src/routes/chat.tsx`, `src/routes/model-sets.tsx`, `src/components/OpenRouterModelSearch.tsx`

- [x] **Step 1: Re-run unit tests**

```bash
node --experimental-strip-types --test tests/frontend/vendorMarks.test.ts
```

Expected: PASS.

- [x] **Step 2: Production build**

```bash
npm run build
```

Expected: success; vendor SVGs appear in the client asset bundle (no broken import errors).

- [x] **Step 3: Visual check**

Open Chat Council empty state and confirm:

1. Five model cards show recognizable OpenAI / Anthropic / Google / xAI / DeepSeek marks.
2. Small logos in answer headers and judge badge still size correctly.
3. Watermark logos remain faint and non-interactive.
4. Layout does not shift; checkmarks and color dots remain.

- [x] **Step 4: Final commit only if verification produced fixes**

If visual tweaks were needed (object-fit, size %, rounded corners), commit them:

```bash
git add -u
git commit -m "$(cat <<'EOF'
fix(chat): tune official provider logo sizing.

EOF
)"
```

Otherwise leave Task 3 as the last code commit.
