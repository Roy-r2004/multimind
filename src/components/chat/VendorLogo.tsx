/** Compact vendor marks for council / model-set cards. */

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type Props = {
  vendor: string;
  className?: string;
  watermark?: boolean;
  title?: string;
};

export function VendorLogo({ vendor, className, watermark, title }: Props) {
  const key = vendor.trim().toLowerCase().replace(/\s+/g, "");
  const aliases: Record<string, string> = {
    "x.ai": "xai",
    grok: "xai",
    claude: "anthropic",
    gemini: "google",
    chatgpt: "openai",
  };
  const mark = MARKS[aliases[key] ?? key] ?? MARKS.default;
  return (
    <span
      className={cn(
        "inline-grid place-items-center rounded-full",
        watermark ? "opacity-20" : "ring-1 ring-black/10",
        className,
      )}
      style={{ background: watermark ? "transparent" : mark.bg }}
      title={title}
      aria-hidden={!title}
    >
      <svg viewBox="0 0 24 24" className={cn("size-[60%]", watermark && "size-full")}>
        {mark.svg}
      </svg>
    </span>
  );
}

const MARKS: Record<string, { bg: string; svg: ReactNode }> = {
  openai: {
    bg: "linear-gradient(135deg,#10a37f,#0d8f6e)",
    svg: (
      <path
        fill="white"
        d="M22.28 9.95a5.9 5.9 0 0 0-.51-4.86 6 6 0 0 0-6.45-2.88A5.95 5.95 0 0 0 10.05 0 5.97 5.97 0 0 0 4.6 3.4a5.9 5.9 0 0 0-3.95 2.87 6 6 0 0 0 .74 7.02 5.9 5.9 0 0 0 .5 4.86 6 6 0 0 0 6.46 2.88A5.94 5.94 0 0 0 13.95 24a5.97 5.97 0 0 0 5.45-3.4 5.9 5.9 0 0 0 3.95-2.87 6 6 0 0 0-.74-7.02zM13.95 22.2a3.96 3.96 0 0 1-2.54-.92l.13-.07 4.23-2.44a.7.7 0 0 0 .35-.6v-5.97l1.79 1.03c.04.02.06.05.06.1v4.93a4 4 0 0 1-4.02 3.94zM3.5 18.3a3.96 3.96 0 0 1-.47-2.67l.13.08 4.23 2.44c.21.12.48.12.7 0l5.16-2.98v2.07a.12.12 0 0 1-.05.1l-4.28 2.47a4 4 0 0 1-5.42-1.51zm-.86-8.02V8.3a.7.7 0 0 1 .35-.6l.05-.03 4.23-2.44v5.05L5.48 11.3a.12.12 0 0 1-.11 0L3.48 10.3a.7.7 0 0 1-.84-.02zm16.5.72-4.23-2.44v-5.05l.05.03 4.23 2.44c.21.12.35.35.35.6v1.98a.7.7 0 0 1-.84.02l-.05.03a.12.12 0 0 1-.1.01l-1.41-.82zm2.06-2.7-.13-.08-4.23-2.44a.7.7 0 0 0-.7 0L11.5 8.76V6.69a.12.12 0 0 1 .05-.1l4.28-2.47a4 4 0 0 1 5.89 4.16zM8.6 15.54l-1.79-1.04a.12.12 0 0 1-.06-.1V9.48l1.79-1.03a.12.12 0 0 1 .11 0l1.79 1.03v5.15a.12.12 0 0 1-.05.1L8.7 15.54a.12.12 0 0 1-.1 0zm1.03-7.03L12 6.5l2.36 1.36v2.72L12 12l-2.36-1.36V8.51z"
      />
    ),
  },
  anthropic: {
    bg: "linear-gradient(135deg,#d4a27f,#c48a5e)",
    svg: (
      <path
        fill="white"
        d="M13.8 3.2 17.6 20.8h-3.1l-.9-3.4H10.4l-.9 3.4H6.4L10.2 3.2h3.6zm-2.3 11.5h2.9L12.9 7.7l-1.4 7z"
      />
    ),
  },
  google: {
    bg: "linear-gradient(135deg,#4285f4,#34a853 45%,#fbbc05 70%,#ea4335)",
    svg: (
      <path
        fill="white"
        d="M12 11.2v2.6h5.1c-.2 1.3-1.6 3.9-5.1 3.9A5.9 5.9 0 1 1 12 6.1c1.7 0 2.8.7 3.5 1.3l2.4-2.3C16.5 3.7 14.5 2.8 12 2.8A9.2 9.2 0 1 0 21.2 12c0-.6-.1-1.1-.2-1.6H12z"
      />
    ),
  },
  xai: {
    bg: "linear-gradient(135deg,#e5e7eb,#9ca3af)",
    svg: (
      <path
        fill="#111"
        d="M4.5 4.5h4.1L12 9.2l3.4-4.7h4.1L13.9 12l6.2 7.5h-4.1L12 14.8l-3.9 4.7H4l6.1-7.5L4.5 4.5z"
      />
    ),
  },
  deepseek: {
    bg: "linear-gradient(135deg,#4f46e5,#7c3aed)",
    svg: (
      <path
        fill="white"
        d="M12 2.5c5.2 0 9.5 4.3 9.5 9.5S17.2 21.5 12 21.5 2.5 17.2 2.5 12 6.8 2.5 12 2.5zm0 3.2a6.3 6.3 0 1 0 0 12.6 6.3 6.3 0 0 0 0-12.6zm0 2.1a4.2 4.2 0 1 1 0 8.4 4.2 4.2 0 0 1 0-8.4z"
      />
    ),
  },
  default: {
    bg: "linear-gradient(135deg,#64748b,#334155)",
    svg: <circle cx="12" cy="12" r="6" fill="white" />,
  },
};
