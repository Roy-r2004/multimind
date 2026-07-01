import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

type MessageContentProps = {
  children: string;
  className?: string;
  /** Tighter typography for model answer cards */
  compact?: boolean;
  /** Softer color for secondary text (e.g. verdict reasoning) */
  muted?: boolean;
};

function buildComponents(compact: boolean): Components {
  const text = compact ? "text-[0.8125rem]" : "text-sm";
  const heading = compact ? "text-sm" : "text-base";
  const codeSize = compact ? "text-[0.75rem]" : "text-[0.8125rem]";

  return {
    p: ({ children }) => (
      <p className={cn("mb-3 leading-[1.7] last:mb-0", text)}>{children}</p>
    ),
    ul: ({ children }) => (
      <ul className={cn("mb-3 list-disc space-y-1.5 pl-5 last:mb-0", text)}>{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className={cn("mb-3 list-decimal space-y-1.5 pl-5 last:mb-0", text)}>{children}</ol>
    ),
    li: ({ children }) => <li className="pl-0.5 marker:text-muted-foreground">{children}</li>,
    h1: ({ children }) => (
      <h3 className={cn("mb-2 mt-4 font-semibold first:mt-0", heading)}>{children}</h3>
    ),
    h2: ({ children }) => (
      <h4 className={cn("mb-2 mt-3 font-semibold first:mt-0", heading)}>{children}</h4>
    ),
    h3: ({ children }) => (
      <h5 className={cn("mb-1.5 mt-3 font-medium first:mt-0", text)}>{children}</h5>
    ),
    h4: ({ children }) => (
      <h6 className={cn("mb-1 mt-2 font-medium first:mt-0", text)}>{children}</h6>
    ),
    blockquote: ({ children }) => (
      <blockquote
        className={cn(
          "mb-3 border-l-2 border-primary/25 bg-muted/30 py-1 pl-3 italic last:mb-0",
          text,
        )}
      >
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-4 border-border/80" />,
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
      <pre className="mb-3 overflow-x-auto rounded-lg last:mb-0">{children}</pre>
    ),
    table: ({ children }) => (
      <div className="mb-3 overflow-x-auto rounded-lg border border-border last:mb-0">
        <table className={cn("w-full border-collapse text-left", codeSize)}>{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-muted/50">{children}</thead>,
    th: ({ children }) => (
      <th className="border-b border-border px-3 py-2 font-medium text-foreground">{children}</th>
    ),
    td: ({ children }) => (
      <td className="border-b border-border/60 px-3 py-2 align-top">{children}</td>
    ),
  };
}

const compactComponents = buildComponents(true);
const defaultComponents = buildComponents(false);

export function MessageContent({ children, className, compact = false, muted = false }: MessageContentProps) {
  const text = children?.trim();
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
        components={compact ? compactComponents : defaultComponents}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
