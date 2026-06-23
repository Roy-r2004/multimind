import { createFileRoute } from "@tanstack/react-router";
import { ChatPage } from "@/routes/chat";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "MultiAI — Chat" },
      {
        name: "description",
        content: "Ask once, compare answers from multiple AI models, get one Verdict AI.",
      },
    ],
  }),
  component: ChatPage,
});
