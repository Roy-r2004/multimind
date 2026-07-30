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
