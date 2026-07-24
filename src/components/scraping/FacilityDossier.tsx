import { useMemo, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ScrapingFacilityDetail } from "@/lib/scraping/types";

const TABS = [
  "Overview",
  "Locations",
  "Contacts",
  "Treatment Services",
  "Sources & Evidence",
] as const;

type Tab = (typeof TABS)[number];

type Props = {
  detail: ScrapingFacilityDetail | null;
  loading: boolean;
  error: string | null;
  onBack?: () => void;
};

const EMPTY = "Not extracted from retrieved pages yet.";

export function FacilityDossier({ detail, loading, error, onBack }: Props) {
  const [tab, setTab] = useState<Tab>("Overview");
  const [copied, setCopied] = useState(false);

  const treatmentServices = useMemo(() => {
    if (!detail) return [];
    return detail.attributes.filter((a) => a.attribute_group === "treatment_service");
  }, [detail]);

  if (loading) {
    return (
      <Panel>
        <p className="text-sm text-muted-foreground">Loading facility dossier…</p>
      </Panel>
    );
  }

  if (error) {
    return (
      <Panel>
        <p className="text-sm text-destructive">{error}</p>
      </Panel>
    );
  }

  if (!detail) {
    return (
      <Panel>
        <p className="text-sm text-muted-foreground">
          Select a facility to inspect locations, contacts, services, and evidence.
        </p>
      </Panel>
    );
  }

  const website =
    detail.primary_website ||
    detail.contacts.find((contact) => contact.contact_type === "website")?.value ||
    detail.contacts.find((contact) => contact.contact_type === "booking_url")?.value ||
    null;

  async function copyContact() {
    if (!detail?.primary_contact) return;
    try {
      await navigator.clipboard.writeText(detail.primary_contact);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Panel>
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          className="mb-3 text-sm text-muted-foreground hover:text-foreground lg:hidden"
        >
          ← Back to list
        </button>
      ) : null}

      <div className="space-y-3 border-b border-border pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-2xl font-semibold tracking-tight">{detail.canonical_name}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {[detail.facility_type, detail.primary_city, detail.primary_region, detail.country_name]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{(detail.confidence_score * 100).toFixed(0)}% confidence</Badge>
            <Badge variant="outline">{detail.human_review_status}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {website ? (
            <Button asChild size="sm" variant="outline">
              <a href={website} target="_blank" rel="noreferrer">
                Open website
              </a>
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!detail.primary_contact}
            onClick={() => void copyContact()}
          >
            {copied ? "Copied" : "Copy contact"}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setTab("Sources & Evidence")}>
            Jump to sources
          </Button>
        </div>
      </div>

      <div className="mt-4 flex gap-1 overflow-x-auto pb-2">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === name
                ? "bg-foreground text-background"
                : "bg-muted/40 text-muted-foreground hover:bg-muted"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="mt-4 min-h-[16rem]">
        {tab === "Overview" ? (
          <Overview detail={detail} />
        ) : null}
        {tab === "Locations" ? (
          <ListOrEmpty
            items={detail.locations.map((location) => ({
              title: location.location_name,
              body: [location.full_address, location.city, location.region]
                .filter(Boolean)
                .join(" · "),
            }))}
          />
        ) : null}
        {tab === "Contacts" ? (
          <ListOrEmpty
            items={detail.contacts.map((contact) => ({
              title: contact.contact_type,
              body: contact.value,
            }))}
          />
        ) : null}
        {tab === "Treatment Services" ? (
          <ListOrEmpty
            items={treatmentServices.map((attr) => ({
              title: attr.display_name,
              body: attr.value_text ?? "",
            }))}
          />
        ) : null}
        {tab === "Sources & Evidence" ? (
          <div className="space-y-4">
            <section>
              <h3 className="mb-2 text-sm font-medium">Sources</h3>
              <ListOrEmpty
                items={detail.sources.map((source) => ({
                  title: source.title || source.url,
                  body: source.url,
                  href: source.url,
                }))}
              />
            </section>
            <section>
              <h3 className="mb-2 text-sm font-medium">Field evidence</h3>
              <ListOrEmpty
                items={detail.evidence.map((row) => ({
                  title: row.field_path,
                  body: [row.extracted_value, row.evidence_text].filter(Boolean).join(" — "),
                }))}
              />
            </section>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function Panel({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-[28rem] flex-col rounded-xl border border-border bg-card/40 p-4 md:p-5">
      {children}
    </div>
  );
}

function Overview({ detail }: { detail: ScrapingFacilityDetail }) {
  const website =
    detail.primary_website ||
    detail.contacts.find((contact) => contact.contact_type === "website")?.value ||
    detail.contacts.find((contact) => contact.contact_type === "booking_url")?.value ||
    null;
  const rows = [
    ["Type", detail.facility_type],
    ["Country", detail.country_name],
    ["City / region", [detail.primary_city, detail.primary_region].filter(Boolean).join(", ") || null],
    ["Primary address", detail.primary_address],
    ["Website", website],
    ["Primary contact", detail.primary_contact],
    ["Aliases", detail.aliases.map((a) => a.name).join(", ") || null],
    ["Sources linked", String(detail.source_count)],
    ["Verification", detail.verification_status],
    ["Review", detail.human_review_status],
  ] as const;

  return (
    <dl className="grid gap-3 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-lg border border-border/70 px-3 py-2">
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-words text-sm">{value || EMPTY}</dd>
        </div>
      ))}
    </dl>
  );
}

function ListOrEmpty({
  items,
}: {
  items: Array<{ title: string; body?: string; href?: string }>;
}) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">{EMPTY}</p>;
  }
  return (
    <ul className="space-y-2">
      {items.map((item, index) => (
        <li key={`${item.title}-${index}`} className="rounded-lg border border-border/70 px-3 py-2">
          {item.href ? (
            <a
              href={item.href}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-primary underline-offset-2 hover:underline"
            >
              {item.title}
            </a>
          ) : (
            <p className="font-medium">{item.title}</p>
          )}
          {item.body && item.body !== item.title ? (
            <p className="mt-1 break-words text-sm text-muted-foreground">{item.body}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
