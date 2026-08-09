export type MapsCensusRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "cancelled";

export type MapsCensusRunSummary = {
  id: string;
  country_code: string;
  country_name: string;
  status: MapsCensusRunStatus;
  error_message: string | null;
  cells_total: number;
  cells_completed: number;
  places_found: number;
  places_classified_relevant: number;
  places_with_website: number;
  places_enriched: number;
  enrichment_refresh_completed_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  hero_image_url: string | null;
  total_cost_usd: number;
};

export type MapsCensusRunDetail = MapsCensusRunSummary;

export type MapsRunLiveStats = {
  run_id: string;
  status: string;
  places_found_live: number;
  places_classified_live: number;
  places_relevant_live: number;
  places_dropped_live: number;
  places_enriched_live: number;
  cells_completed_live: number;
  cells_total: number;
  discovery_progress_pct: number;
  classification_progress_pct: number;
  enrichment_progress_pct: number;
  elapsed_seconds: number | null;
  started_at: string | null;
};

export type MapsCensusCellItem = {
  id: string;
  region_name: string;
  city_name: string | null;
  query_text: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  places_found: number;
  error_message: string | null;
  completed_at: string | null;
};

export type MapsCensusRunCreateInput = {
  country_code: string;
};

export type MapsPlaceItem = {
  id: string;
  google_place_id: string;
  canonical_name: string;
  place_types: string[];
  formatted_address: string | null;
  city_name: string | null;
  region_name: string | null;
  latitude: number | null;
  longitude: number | null;
  international_phone_number: string | null;
  contact_email: string | null;
  raw_website: string | null;
  official_website: string | null;
  website_source: "places" | "search" | "llm" | null;
  is_relevant: boolean | null;
  relevance_reason: string | null;
  confidence_score: number | null;
  discovered_via_query: string | null;
  has_photo: boolean;
  verification_tier: "verified" | "flagged" | "excluded";
  export_eligible: boolean;
  enrichment_status: "pending" | "running" | "completed" | "failed" | "skipped";
  addictions_treated: string[];
  languages_spoken: string[];
  treatment_price: string | null;
  bed_count: number | null;
  verification_verdict: "confirmed" | "contradicted" | "unknown" | null;
  verification_reason: string | null;
  verification_source_url: string | null;
  keep_drop_decision: "keep" | "drop" | null;
  manually_excluded: boolean;
};

export type MapsPaginatedMeta = {
  total: number;
  limit: number;
  offset: number;
};

export type MapsPlaceListResponse = {
  items: MapsPlaceItem[];
  meta: MapsPaginatedMeta;
};

export type MapsCampaignActionResponse = {
  run_id: string;
  status: string;
  campaign_paused: boolean;
  message: string | null;
};
