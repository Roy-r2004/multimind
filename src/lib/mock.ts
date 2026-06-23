export type Model = {
  id: string;
  name: string;
  vendor: string;
  color: string;
  blurb: string;
};

export const MODELS: Model[] = [
  {
    id: "gpt-4.1",
    name: "GPT-4.1",
    vendor: "OpenAI",
    color: "oklch(0.75 0.13 155)",
    blurb: "Reliable generalist",
  },
  {
    id: "claude",
    name: "Claude 3.5",
    vendor: "Anthropic",
    color: "oklch(0.78 0.12 60)",
    blurb: "Careful reasoner",
  },
  {
    id: "gemini",
    name: "Gemini 1.5",
    vendor: "Google",
    color: "oklch(0.72 0.14 240)",
    blurb: "Multimodal",
  },
  {
    id: "mistral",
    name: "Mistral Large",
    vendor: "Mistral",
    color: "oklch(0.68 0.16 25)",
    blurb: "Fast & open",
  },
  {
    id: "deepseek",
    name: "DeepSeek V3",
    vendor: "DeepSeek",
    color: "oklch(0.65 0.17 280)",
    blurb: "Coding specialist",
  },
  {
    id: "llama",
    name: "Llama 3.1",
    vendor: "Meta",
    color: "oklch(0.72 0.14 200)",
    blurb: "Open-weight",
  },
  {
    id: "perplex",
    name: "Perplexity Pro",
    vendor: "Perplexity",
    color: "oklch(0.70 0.13 195)",
    blurb: "Web-grounded",
  },
];

export type ModelSet = {
  id: string;
  name: string;
  description: string;
  models: string[];
  verdictModel: string;
  strategy: Strategy;
  bestFor: string;
  templateName?: string;
  customInstructions?: string;
};

export type Strategy = "Reconcile" | "Synthesize" | "Rank" | "Pick Best" | "Debate";

export const STRATEGIES: { name: Strategy; desc: string }[] = [
  { name: "Reconcile", desc: "Finds agreements and disagreements, then writes a balanced answer." },
  { name: "Synthesize", desc: "Combines the best parts from each answer into one." },
  { name: "Rank", desc: "Ranks every answer from strongest to weakest with reasons." },
  { name: "Pick Best", desc: "Chooses the single best answer and explains why." },
  { name: "Debate", desc: "Shows the disagreement and decides which argument is stronger." },
];

export const MODEL_SETS: ModelSet[] = [
  {
    id: "balanced",
    name: "Balanced Set",
    description: "Great default for everyday questions.",
    models: ["gpt-4.1", "claude", "gemini"],
    verdictModel: "gpt-4.1",
    strategy: "Synthesize",
    bestFor: "General questions, everyday use",
  },
  {
    id: "coding",
    name: "Coding Set",
    description: "Tuned for code review and debugging.",
    models: ["gpt-4.1", "deepseek", "claude"],
    verdictModel: "claude",
    strategy: "Pick Best",
    bestFor: "Coding, debugging, architecture",
  },
  {
    id: "business",
    name: "Business Set",
    description: "Strategic, concise, action-oriented.",
    models: ["gpt-4.1", "gemini", "mistral"],
    verdictModel: "gpt-4.1",
    strategy: "Reconcile",
    bestFor: "Strategy, startups, business decisions",
  },
  {
    id: "research",
    name: "Research Set",
    description: "Deep, cited, careful reasoning.",
    models: ["claude", "perplex", "gpt-4.1"],
    verdictModel: "claude",
    strategy: "Debate",
    bestFor: "Research, analysis, fact-checking",
  },
];

export type Template = {
  id: string;
  title: string;
  description: string;
  category: string;
  instructions: string;
};

export const TEMPLATES: Template[] = [
  {
    id: "t1",
    title: "Explain simply",
    description: "Beginner-friendly explanations.",
    category: "Learning",
    instructions: "Explain like I'm new to the topic. Use simple words and short examples.",
  },
  {
    id: "t2",
    title: "Short business answer",
    description: "Concise, decision-oriented.",
    category: "Business",
    instructions: "Give a short, business-focused answer. Lead with the recommendation.",
  },
  {
    id: "t3",
    title: "Compare & recommend",
    description: "Weigh options and pick one.",
    category: "Decision",
    instructions: "Compare options in a small table, then recommend the most practical one.",
  },
  {
    id: "t4",
    title: "Step-by-step",
    description: "Walk through with examples.",
    category: "Learning",
    instructions: "Explain step by step with examples for each step.",
  },
];

export type ChatMsg = { role: "user" | "ai"; text: string };

export type Chat = {
  id: string;
  title: string;
  updated: string;
  /** Project this chat belongs to, if any. */
  projectId?: string | null;
};

export const SAMPLE_CHATS: Chat[] = [
  { id: "c1", title: "Best framework for SaaS landing page", updated: "2h ago", projectId: null },
  { id: "c2", title: "Pricing model for indie tool", updated: "Yesterday", projectId: null },
  { id: "c3", title: "Capital of Lebanon", updated: "3 days ago", projectId: null },
  { id: "c4", title: "Refactor strategy for monorepo", updated: "Last week", projectId: null },
];

export const SAMPLE_ANSWERS = [
  {
    modelId: "gpt-4.1",
    confidence: 92,
    text: "For a fast SaaS landing page, pick **Next.js or TanStack Start** with **Tailwind**. They give you SSR for SEO, fast image handling, and a huge component ecosystem. Pair with shadcn/ui for polished primitives.",
  },
  {
    modelId: "claude",
    confidence: 88,
    text: "I'd lean toward **Astro** if the page is mostly static — it ships almost zero JS and is fantastic for SEO and Lighthouse scores. Use React islands only for the interactive bits.",
  },
  {
    modelId: "gemini",
    confidence: 81,
    text: "Consider **SvelteKit** for the smallest bundle and best developer ergonomics. The downside is a smaller component library, but tools like Skeleton UI close the gap.",
  },
];

export const VERDICT = {
  strategy: "Synthesize" as Strategy,
  text: "All three models agree the framework should prioritize **SEO, speed, and easy components**. If the page is mostly static, start with **Astro** for the leanest output; if you expect a dashboard later, start with **Next.js / TanStack Start + Tailwind + shadcn**. Either way, defer interactivity and ship images as AVIF/WebP.",
  reason:
    "Astro wins on pure static performance, but Next/TanStack wins on long-term flexibility. The synthesis chooses based on the user's actual future plans.",
};

export type Project = {
  id: string;
  name: string;
  description?: string;
  /** Baseline chat count (seed data); live assignments are added on top. */
  chats: number;
  members: number;
  updated: string;
};

export const PROJECTS: Project[] = [
  {
    id: "p1",
    name: "Acme Marketing",
    description: "Campaign copy, positioning and launch research.",
    chats: 12,
    members: 4,
    updated: "2h ago",
  },
  {
    id: "p2",
    name: "Internal Tools",
    description: "Engineering specs and tooling decisions.",
    chats: 7,
    members: 2,
    updated: "Yesterday",
  },
  {
    id: "p3",
    name: "Q4 Research",
    description: "Market and competitor analysis for Q4.",
    chats: 23,
    members: 6,
    updated: "3 days ago",
  },
];

export const ADMIN_USERS = [
  { name: "Sara Kassem", email: "sara@acme.co", role: "Admin", chats: 142 },
  { name: "Liam Park", email: "liam@acme.co", role: "Organization User", chats: 87 },
  { name: "Noor Halabi", email: "noor@acme.co", role: "Team Member", chats: 54 },
  { name: "Diego Reyes", email: "diego@example.com", role: "Normal User", chats: 12 },
];

export function modelById(id: string) {
  return MODELS.find((m) => m.id === id) ?? MODELS[0];
}
