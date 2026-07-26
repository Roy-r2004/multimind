import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { BlueprintSection } from "@/components/scraping/BlueprintSection";
import type { ScrapingBlueprintCitation } from "@/lib/scraping/types";

export function GeneratedBlueprintContent({
  humanReadable,
  structured,
  citations,
}: {
  humanReadable?: string | null;
  structured?: Record<string, unknown> | null;
  citations?: ScrapingBlueprintCitation[] | null;
}) {
  return (
    <div className="space-y-4">
      {humanReadable && (
        <BlueprintSection title="Blueprint">
          <div className="prose prose-sm max-w-none dark:prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
              {humanReadable}
            </ReactMarkdown>
          </div>
        </BlueprintSection>
      )}
      {structured && (
        <BlueprintSection title="Structured Blueprint">
          <pre className="max-h-[32rem] overflow-auto rounded-lg border border-border bg-muted/30 p-4 text-xs leading-5 whitespace-pre-wrap">
            {JSON.stringify(structured, null, 2)}
          </pre>
        </BlueprintSection>
      )}
      <BlueprintSection title="Citations">
        {citations?.length ? (
          <ul className="space-y-3">
            {deduplicateCitations(citations).map((citation) => (
              <li key={citation.url} className="rounded-lg border border-border p-3">
                <a
                  href={safeExternalUrl(citation.url) ?? undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-primary underline-offset-4 hover:underline"
                >
                  {citation.title || citation.url}
                </a>
                <p className="mt-1 break-all text-xs text-muted-foreground">{citation.url}</p>
                {citation.notes && <p className="mt-2 text-sm">{citation.notes}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">No provider citations were returned.</p>
        )}
      </BlueprintSection>
    </div>
  );
}

function deduplicateCitations(citations: ScrapingBlueprintCitation[]) {
  return citations.filter(
    (citation, index) => citations.findIndex((item) => item.url === citation.url) === index,
  );
}

function safeExternalUrl(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}
