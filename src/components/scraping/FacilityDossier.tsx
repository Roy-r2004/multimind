import { useMemo, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ScrapingFacilityDetail } from "@/lib/scraping/types";
import { cn } from "@/lib/utils";

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

  const phonesByLocationId = useMemo(() => {
    if (!detail) return new Map<string, string[]>();
    const map = new Map<string, string[]>();
    for (const contact of detail.contacts) {
      if (!["phone", "hotline", "whatsapp"].includes(contact.contact_type) || !contact.location_id) {
        continue;
      }
      const current = map.get(contact.location_id) ?? [];
      current.push(contact.value);
      map.set(contact.location_id, current);
    }
    return map;
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
        <p className="text-sm text-rose-600">{error}</p>
      </Panel>
    );
  }

  if (!detail) {
    return (
      <Panel>
        <p className="text-[11px] uppercase tracking-[0.28em] text-primary/90">Dossier</p>
        <p className="mt-2 font-display text-xl text-foreground">Pick a facility</p>
        <p className="mt-2 text-sm text-muted-foreground">
          Inspect locations, contacts, services, and evidence as they crystallize.
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
          className="mb-3 text-sm text-muted-foreground hover:text-primary lg:hidden"
        >
          ← Back to list
        </button>
      ) : null}

      <div className="space-y-3 border-b border-border pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-2xl tracking-tight text-foreground">
              {detail.canonical_name}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {[detail.facility_type, detail.primary_city, detail.primary_region, detail.country_name]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge
              variant="secondary"
              className="border border-primary/40 bg-primary/15 font-semibold text-primary"
            >
              {(detail.confidence_score * 100).toFixed(0)}% confidence
            </Badge>
            <Badge
              variant="outline"
              className="border-border bg-muted font-medium capitalize text-foreground"
            >
              {(detail.human_review_status || "unknown").replaceAll("_", " ")}
            </Badge>
            <Badge
              variant="outline"
              className="border-border bg-muted font-medium capitalize text-foreground"
            >
              {(detail.publication_class || "unknown").replaceAll("_", " ")}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {website ? (
            <Button
              asChild
              size="sm"
              variant="outline"
              className="border-border bg-muted/40 text-foreground hover:bg-accent"
            >
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
            className="border-border bg-muted/40 text-foreground hover:bg-accent"
            onClick={() => void copyContact()}
          >
            {copied ? "Copied" : "Copy contact"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-muted-foreground hover:bg-accent hover:text-primary"
            onClick={() => setTab("Sources & Evidence")}
          >
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
            className={cn(
              "shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium transition",
              tab === name
                ? "bg-primary text-primary-foreground"
                : "bg-muted/40 text-muted-foreground hover:bg-accent",
            )}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="mt-4 min-h-[16rem]">
        {tab === "Overview" ? <Overview detail={detail} /> : null}
        {tab === "Locations" ? (
          <LocationCards detail={detail} phonesByLocationId={phonesByLocationId} />
        ) : null}
        {tab === "Contacts" ? (
          <ContactCards detail={detail} />
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
              <h3 className="mb-2 text-sm font-medium text-foreground">Sources</h3>
              <ListOrEmpty
                items={detail.sources.map((source) => ({
                  title: source.title || source.url,
                  body: source.url,
                  href: source.url,
                }))}
              />
            </section>
            <section>
              <h3 className="mb-2 text-sm font-medium text-foreground">Field evidence</h3>
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
    <div className="flex h-full min-h-[28rem] flex-col rounded-[1.5rem] border border-border bg-card/75 p-4 shadow-[0_20px_50px_rgba(0,0,0,0.25)] backdrop-blur-md md:p-5">
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
    [
      "City / region",
      [detail.primary_city, detail.primary_region].filter(Boolean).join(", ") || null,
    ],
    ["Primary address", detail.primary_address],
    ["Website", website],
    ["Primary contact", detail.primary_contact],
    ["Publication class", detail.publication_class],
    ["Country gate", detail.country_containment_status],
    ["Gate reason", detail.country_containment_reason],
    ["Completeness", `${detail.completeness_percent.toFixed(0)}%`],
    ["Aliases", detail.aliases.map((a) => a.name).join(", ") || null],
    ["Sources linked", String(detail.source_count)],
    ["Verification", detail.verification_status],
    ["Review", detail.human_review_status],
  ] as const;

  return (
    <dl className="grid gap-3 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-lg border border-border bg-white/[0.03] px-3 py-2">
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-words text-sm text-foreground/90">{value || EMPTY}</dd>
        </div>
      ))}
    </dl>
  );
}

function LocationCards({
  detail,
  phonesByLocationId,
}: {
  detail: ScrapingFacilityDetail;
  phonesByLocationId: Map<string, string[]>;
}) {
  if (detail.locations.length === 0) {
    return <p className="text-sm text-white/45">{EMPTY}</p>;
  }
  return (
    <div className="space-y-3">
      {detail.locations.map((location) => {
        const phones = location.primary_phone
          ? [location.primary_phone]
          : (phonesByLocationId.get(location.id) ?? []);
        return (
          <div
            key={location.id}
            className="rounded-lg border border-border bg-white/[0.03] px-3 py-3"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-medium text-foreground">{location.location_name}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {[location.location_type, location.city, location.region, location.country_name]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2 text-[11px] uppercase tracking-wide text-white/45">
                <span>{location.verification_status}</span>
                <span>{location.location_completeness_status}</span>
              </div>
            </div>
            <div className="mt-3 space-y-2 text-sm text-foreground/80">
              <p>{location.full_address || EMPTY}</p>
              <p>Phone: {phones.join(" · ") || EMPTY}</p>
              <p>
                Gate:{" "}
                {[location.country_containment_status, location.country_containment_reason]
                  .filter(Boolean)
                  .join(" · ") || EMPTY}
              </p>
              {location.location_gap_reason ? <p>Gap: {location.location_gap_reason}</p> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ContactCards({ detail }: { detail: ScrapingFacilityDetail }) {
  if (detail.contacts.length === 0) {
    return <p className="text-sm text-white/45">{EMPTY}</p>;
  }
  const locationNameById = new Map(detail.locations.map((location) => [location.id, location.location_name]));
  return (
    <ul className="space-y-2">
      {detail.contacts.map((contact) => (
        <li
          key={contact.id}
          className="rounded-lg border border-border bg-white/[0.03] px-3 py-2"
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="font-medium text-foreground">
                {contact.contact_type}
                {contact.label ? ` · ${contact.label}` : ""}
              </p>
              <p className="mt-1 break-words text-sm text-muted-foreground">{contact.value}</p>
            </div>
            <div className="text-right text-xs text-white/45">
              <p>{contact.verification_status}</p>
              <p>{contact.contact_discovery_status}</p>
            </div>
          </div>
          {contact.location_id ? (
            <p className="mt-2 text-xs text-white/45">
              {locationNameById.get(contact.location_id) ?? "Linked location"}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function ListOrEmpty({
  items,
}: {
  items: Array<{ title: string; body?: string; href?: string }>;
}) {
  if (items.length === 0) {
    return <p className="text-sm text-white/45">{EMPTY}</p>;
  }
  return (
    <ul className="space-y-2">
      {items.map((item, index) => (
        <li
          key={`${item.title}-${index}`}
          className="rounded-lg border border-border bg-white/[0.03] px-3 py-2"
        >
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
            <p className="font-medium text-foreground">{item.title}</p>
          )}
          {item.body && item.body !== item.title ? (
            <p className="mt-1 break-words text-sm text-muted-foreground">{item.body}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
