/** OpenRouter slug ↔ internal model id helpers */

export const MAX_COUNCIL_MODELS = 5;

export function slugToModelId(openrouterSlug: string): string {
  return `or:${openrouterSlug.replace(/\//g, "--")}`;
}

export function modelIdToSlug(modelId: string): string | null {
  if (modelId.startsWith("or:")) {
    return modelId.slice(3).replace(/--/g, "/");
  }
  return null;
}

export function vendorFromSlug(slug: string): string {
  const provider = slug.includes("/") ? slug.split("/")[0] : slug;
  const labels: Record<string, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    google: "Google",
    "meta-llama": "Meta",
    mistralai: "Mistral",
    deepseek: "DeepSeek",
    qwen: "Alibaba",
    "x-ai": "xAI",
  };
  return labels[provider] ?? provider.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function colorForModelId(id: string): string {
  const key = modelIdToSlug(id) ?? id;
  const hue = [...key].reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360;
  return `oklch(0.68 0.14 ${hue})`;
}

export function displayNameFromSlug(slug: string): string {
  const tail = slug.split("/").pop() ?? slug;
  return tail.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
