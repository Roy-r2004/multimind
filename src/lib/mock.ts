/** Shared types and static config — no fake chat/model data */

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

export type Chat = {
  id: string;
  title: string;
  updated: string;
  projectId?: string | null;
};

export type Project = {
  id: string;
  name: string;
  description?: string;
  chats: number;
  members: number;
  updated: string;
};

export type Template = {
  id: string;
  title: string;
  description: string;
  category: string;
  instructions: string;
};
