import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

type MessageContentProps = {
  children: string;
  className?: string;
  /** Typography and spacing appropriate to the surface rendering the Markdown. */
  variant?: "default" | "verdict";
  /** Tighter typography for model answer cards */
  compact?: boolean;
  /** Softer color for secondary text (e.g. verdict reasoning) */
  muted?: boolean;
};

function buildComponents(compact: boolean, variant: "default" | "verdict" = "default"): Components {
  const isVerdict = variant === "verdict";
  const text = compact ? "text-[0.8125rem]" : "text-sm";
  const heading = compact ? "text-sm" : "text-base";
  const codeSize = compact ? "text-[0.75rem]" : "text-[0.8125rem]";

  return {
    p: ({ children }) => (
      <p
        className={cn(
          isVerdict ? "mb-4 text-[0.9375rem] leading-[1.75] sm:text-base" : "mb-3 leading-[1.7]",
          "last:mb-0",
          !isVerdict && text,
        )}
      >
        {children}
      </p>
    ),
    ul: ({ children }) => (
      <ul
        className={cn(
          isVerdict
            ? "mb-4 list-disc space-y-2 pl-6 text-[0.9375rem] leading-[1.7] sm:text-base [&_ol]:mt-2 [&_ul]:mt-2"
            : "mb-3 list-disc space-y-1.5 pl-5",
          "last:mb-0",
          !isVerdict && text,
        )}
      >
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol
        className={cn(
          isVerdict
            ? "mb-4 list-decimal space-y-2 pl-6 text-[0.9375rem] leading-[1.7] sm:text-base [&_ol]:mt-2 [&_ul]:mt-2"
            : "mb-3 list-decimal space-y-1.5 pl-5",
          "last:mb-0",
          !isVerdict && text,
        )}
      >
        {children}
      </ol>
    ),
    li: ({ children }) => <li className="pl-0.5 marker:text-muted-foreground">{children}</li>,
    h1: ({ children }) => (
      <h3
        className={cn(
          isVerdict
            ? "mb-3 mt-7 text-xl font-semibold leading-tight tracking-tight sm:text-2xl"
            : "mb-2 mt-4 font-semibold",
          "first:mt-0",
          !isVerdict && heading,
        )}
      >
        {children}
      </h3>
    ),
    h2: ({ children }) => (
      <h4
        className={cn(
          isVerdict
            ? "mb-2.5 mt-6 text-lg font-semibold leading-snug tracking-tight sm:text-xl"
            : "mb-2 mt-3 font-semibold",
          "first:mt-0",
          !isVerdict && heading,
        )}
      >
        {children}
      </h4>
    ),
    h3: ({ children }) => (
      <h5
        className={cn(
          isVerdict
            ? "mb-2 mt-5 text-base font-semibold leading-snug sm:text-lg"
            : "mb-1.5 mt-3 font-medium",
          "first:mt-0",
          !isVerdict && text,
        )}
      >
        {children}
      </h5>
    ),
    h4: ({ children }) => (
      <h6
        className={cn(
          isVerdict ? "mb-1.5 mt-4 text-base font-semibold" : "mb-1 mt-2 font-medium",
          "first:mt-0",
          !isVerdict && text,
        )}
      >
        {children}
      </h6>
    ),
    blockquote: ({ children }) => (
      <blockquote
        className={cn(
          isVerdict
            ? "mb-5 border-l-2 border-primary/25 bg-muted/30 py-2 pl-4 text-[0.9375rem] leading-relaxed italic sm:text-base"
            : "mb-3 border-l-2 border-primary/25 bg-muted/30 py-1 pl-3 italic",
          "last:mb-0",
          !isVerdict && text,
        )}
      >
        {children}
      </blockquote>
    ),
    hr: () => <hr className={cn(isVerdict ? "my-6" : "my-4", "border-border/80")} />,
    strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
      >
        {children}
      </a>
    ),
    code: ({ className, children, ...props }) => {
      const isBlock = Boolean(className?.includes("language-"));
      if (isBlock) {
        return (
          <code
            className={cn(
              "block overflow-x-auto rounded-lg bg-muted/90 px-3 py-2.5 font-mono leading-relaxed text-foreground/90",
              codeSize,
              className,
            )}
            {...props}
          >
            {children}
          </code>
        );
      }
      return (
        <code
          className={cn(
            "rounded-md bg-muted/90 px-1.5 py-0.5 font-mono text-foreground/90",
            codeSize,
          )}
          {...props}
        >
          {children}
        </code>
      );
    },
    pre: ({ children }) => (
      <pre className={cn(isVerdict ? "mb-5" : "mb-3", "overflow-x-auto rounded-lg last:mb-0")}>
        {children}
      </pre>
    ),
    table: ({ children }) => (
      <div
        className={cn(
          isVerdict ? "mb-5 mt-1" : "mb-3",
          "overflow-x-auto rounded-lg border border-border last:mb-0",
        )}
      >
        <table
          className={cn("w-full min-w-[36rem] table-auto border-collapse text-left", codeSize)}
        >
          {children}
        </table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-muted/50">{children}</thead>,
    th: ({ children }) => (
      <th className="break-words border-b border-border px-3 py-2 font-medium whitespace-normal text-foreground [overflow-wrap:break-word] [&_a]:break-all [&_code]:break-all">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="break-words border-b border-border/60 px-3 py-2 align-top whitespace-normal [overflow-wrap:break-word] [&_a]:break-all [&_code]:break-all">
        {children}
      </td>
    ),
  };
}

const compactComponents = buildComponents(true);
const defaultComponents = buildComponents(false);
const verdictComponents = buildComponents(false, "verdict");

function normalizeMessageText(text: string): string {
  return text
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/&nbsp;/gi, " ")
    .replace(/\r\n/g, "\n");
}

function MessageContentInner({
  children,
  className,
  variant = "default",
  compact = false,
  muted = false,
}: MessageContentProps) {
  const text = normalizeMessageText(children ?? "").trim();
  if (!text) return null;

  return (
    <div
      className={cn(
        "message-content min-w-0 max-w-none break-words [overflow-wrap:anywhere]",
        muted ? "text-muted-foreground" : "text-foreground/92",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={
          compact
            ? compactComponents
            : variant === "verdict"
              ? verdictComponents
              : defaultComponents
        }
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export const MessageContent = memo(MessageContentInner);
