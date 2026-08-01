import type { MapsCensusRunStatus } from "@/lib/maps/types";

/** Derived campaign stage from backend processing_state + run status. */
export type MapsCampaignStage =
  | "queued"
  | "country_profile"
  | "discovery"
  | "website_refresh"
  | "enrichment"
  | "post_processing"
  | "completed"
  | "paused"
  | "failed"
  | "cancelled";

export type MapsCensusRunAdminDetail = {
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
  current_stage: MapsCampaignStage | string;
  campaign_paused: boolean;
  country_profile_status: string | null;
  country_profile_error: string | null;
  funnel_metrics: Record<string, number | string | null> | null;
  saturation_summary: Record<string, unknown> | null;
  processing_state: Record<string, unknown> | null;
  quota_metrics: Record<string, unknown> | null;
  regions_total: number;
  cells_pending: number;
  cells_failed: number;
  cells_capped: number;
  places_eligible: number;
  places_review: number;
  places_excluded: number;
  website_refresh_attempts: number;
  enrichment_refresh_attempts: number;
  country_profile: Record<string, unknown> | null;
};

export type MapsCensusRegionItem = {
  id: string;
  region_name: string;
  cells_planned: number;
  cells_completed: number;
  unique_places_found: number;
  new_unique_places_last_window: number;
  plausible_providers_found: number;
  new_plausible_providers_last_window: number;
  duplicate_rate: number | null;
  query_languages_used: string[] | null;
  provider_terms_used: string[] | null;
  saturation_status: string;
  eligible_candidates_found: number;
  review_candidates_found: number;
  confirmed_public_found: number;
  individuals_found: number;
  unrelated_found: number;
};

export type MapsCensusCellItem = {
  id: string;
  region_name: string;
  city_name: string | null;
  query_text: string;
  query_family: string | null;
  query_language: string | null;
  status: string;
  places_found: number;
  error_message: string | null;
  completed_at: string | null;
  started_at: string | null;
  pages_fetched: number;
  raw_results_found: number;
  unique_results_found: number;
  duplicates_found: number;
  next_page_available: boolean;
  result_cap_reached: boolean;
  pagination_error: string | null;
  parent_cell_id: string | null;
  expansion_reason: string | null;
  expansion_depth: number;
  attempt_count: number;
  last_error: string | null;
  next_retry_at: string | null;
  new_unique_places: number;
  new_plausible_places: number;
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
  raw_website: string | null;
  official_website: string | null;
  website_source: string | null;
  lifecycle_status: string;
  client_eligibility: string;
  operator_type: string | null;
  ownership_status: string | null;
  funding_type: string | null;
  facility_type: string | null;
  care_setting: string | null;
  organization_scope: string | null;
  operator_name: string | null;
  contact_status: string | null;
  addiction_focus_confirmed: boolean | null;
  medical_detox: boolean | null;
  residential_accommodation: boolean | null;
  classification_confidence: number | null;
  classification_evidence: Record<string, unknown> | null;
  discovery_sources: string[];
  is_relevant: boolean | null;
  relevance_reason: string | null;
  confidence_score: number | null;
  discovered_via_query: string | null;
  has_photo: boolean;
  verification_tier: string;
  export_eligible: boolean;
  enrichment_status: string;
  addictions_treated: string[];
  languages_spoken: string[];
  treatment_price: string | null;
  verification_verdict: string | null;
  verification_reason: string | null;
  verification_source_url: string | null;
};

export type MapsPlaceReviewActionItem = {
  id: string;
  place_id: string;
  run_id: string;
  reviewer_user_id: string | null;
  action: string;
  field_name: string | null;
  previous_value: string | null;
  new_value: string | null;
  reason: string;
  created_at: string;
};

export type MapsPlaceDetail = MapsPlaceItem & {
  enrichment_pages_crawled: string[];
  enrichment_error_message: string | null;
  operating_status: string | null;
  review_actions: MapsPlaceReviewActionItem[];
};

export type PaginatedMeta = {
  total: number;
  limit: number;
  offset: number;
};

export type MapsPlaceListResponse = {
  items: MapsPlaceItem[];
  meta: PaginatedMeta;
};

export type MapsCellListResponse = {
  items: MapsCensusCellItem[];
  meta: PaginatedMeta;
};

export type MapsRegionListResponse = {
  items: MapsCensusRegionItem[];
  meta: PaginatedMeta;
};

export type MapsPlaceReviewRequest = {
  action: string;
  field_name?: string | null;
  new_value?: string | null;
  reason: string;
};

export type MapsCampaignActionResponse = {
  run_id: string;
  status: string;
  campaign_paused: boolean;
  message: string | null;
};

export type MapsExportSummaryResponse = {
  run_id: string;
  sheets: Record<string, number>;
  total_places: number;
};

export type MapsAdminCellFilters = {
  status?: string;
  region?: string;
  query_family?: string;
  query_language?: string;
  capped_only?: boolean;
  failed_only?: boolean;
  expanded_only?: boolean;
  limit?: number;
  offset?: number;
};

export type MapsAdminPlaceFilters = {
  search?: string;
  client_eligibility?: string;
  lifecycle_status?: string;
  limit?: number;
  offset?: number;
};

export type ProviderWorkspaceTab =
  | "eligible"
  | "review"
  | "public"
  | "individuals"
  | "unrelated"
  | "all";

export const PROVIDER_TAB_FILTERS: Record<
  ProviderWorkspaceTab,
  Pick<MapsAdminPlaceFilters, "client_eligibility" | "lifecycle_status">
> = {
  eligible: { client_eligibility: "eligible" },
  review: { client_eligibility: "review" },
  public: { lifecycle_status: "confirmed_public" },
  individuals: { lifecycle_status: "confirmed_individual_practitioner" },
  unrelated: { lifecycle_status: "unrelated" },
  all: {},
};
