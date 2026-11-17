// API 类型定义，基于后端契约

export interface TimeConstraint {
  kind: 'arrival' | 'departure' | 'reservation';
  on_date: string;
  minute: number;
  end_minute?: number;
  place_name?: string;
}

export interface TripFields {
  destination: string;
  reference_ids?: string[];
  start_date?: string | null;
  end_date?: string | null;
  travelers?: number | null;
  budget_cents?: number | null;
  requirements?: string[];
  priorities?: string[];
  known_conditions?: string[];
  assumptions?: string[];
  time_constraints?: TimeConstraint[];
  clarifications?: {
    job_id: string;
    request_revision: number;
    questions: string[];
    answer: string;
  }[];
}

export interface Trip extends TripFields {
  id: string;
  revision: number;
  currency: 'CNY';
  timezone: 'Asia/Shanghai';
  missing_fields: string[];
  created_at: string;
  updated_at: string;
}

export interface JobEvent {
  stage: string;
  status: string;
  recorded_at: string;
}

export interface PlanJob {
  id: string;
  trip_id: string;
  request_revision: number;
  kind: 'plan' | 'edit';
  base_revision?: number | null;
  status: 'queued' | 'running' | 'awaiting_input' | 'succeeded' | 'failed' | 'interrupted';
  stage: string;
  retry_of?: string | null;
  result_revision?: number | null;
  questions: string[];
  error?: { code: string; message: string; retryable: boolean } | null;
  created_at: string;
  updated_at: string;
  events: JobEvent[];
  progress_estimate?: number;
}

export interface ActivityCost {
  category: 'ticket' | 'meal';
  amount_cents?: number | null;
  basis: 'per_person' | 'per_group' | 'fixed';
  evidence_id?: string | null;
}

export interface Activity {
  id: string;
  place_id: string;
  kind: 'sightseeing' | 'meal' | 'leisure';
  period: 'morning' | 'noon' | 'evening';
  start_minute: number;
  end_minute: number;
  transport_mode: 'walking' | 'transit' | 'driving';
  costs: ActivityCost[];
  description?: string;
  flexible_meal?: boolean;
}

export interface DayPlan {
  on_date: string;
  activities: Activity[];
}

export interface Place {
  id: string;
  provider: 'amap';
  provider_id: string;
  name: string;
  address: string | null;
  longitude: number | null;
  latitude: number | null;
  coordinate_system: 'GCJ-02';
  city: string | null;
  citycode: string | null;
  adcode: string | null;
  queried_at: string;
  opening_hours_text: string | null;
  cost_text: string | null;
}

export interface SearchSource {
  id: string;
  provider: string;
  title: string;
  url: string;
  snippet?: string;
  queried_at: string;
  fetched_at?: string;
  published_at?: string;
}

export interface FactEvidence {
  id: string;
  place_id: string;
  on_date: string;
  kind: 'opening_hours' | 'ticket_price' | 'meal_price' | 'reservation';
  source_id: string;
  source_url: string;
  quote: string;
  windows?: { start_minute: number; end_minute: number }[];
  closed?: boolean;
  last_entry_minute?: number | null;
  amount_cents?: number | null;
  basis: 'per_person' | 'per_group' | 'fixed';
  queried_at: string;
  fetched_at?: string | null;
  status: 'supported' | 'estimated' | 'conflict' | 'unknown';
  reason: string;
  identity_quote?: string;
  applicability?: { quote: string } | null;
  reservation_note?: string;
}

export interface ValidationIssue {
  rule: string;
  severity: 'conflict' | 'unknown';
  object_ids: string[];
  evidence_ids: string[];
  message: string;
}

export interface ValidationResult {
  status: 'passed' | 'needs_review' | 'blocked';
  issues: ValidationIssue[];
  known_cost_cents: number;
  estimated_cost_cents: number;
  unknown_cost_count: number;
}

export interface TransportLeg {
  id: string;
  from_activity_id: string;
  to_activity_id: string;
  route: {
    provider: 'amap';
    origin_place_id: string;
    destination_place_id: string;
    mode: 'walking' | 'transit' | 'driving';
    distance_meters: number | null;
    duration_seconds: number | null;
    queried_at: string;
    status: 'estimated' | 'unknown';
    fare_cents: number | null;
    fare_basis: 'per_person' | 'per_group' | 'fixed';
  };
}

export interface ConstraintConflict {
  field: 'requirements' | 'known_conditions';
  index: number;
  reason: string;
}

export interface Itinerary {
  id: string;
  trip_id: string;
  revision: number;
  request_revision: number;
  job_id: string;
  timezone: 'Asia/Shanghai';
  currency: 'CNY';
  days: DayPlan[];
  assumptions: string[];
  places: Place[];
  sources: SearchSource[];
  evidence: FactEvidence[];
  transport: TransportLeg[];
  validation: ValidationResult;
  knowledge_chunk_ids: string[];
  notices: string[];
  search_attempts: any[];
  constraint_conflicts: ConstraintConflict[];
  created_at: string;
}

export interface PreferenceProfile {
  revision: number;
  preferences: Record<string, string>;
  notes: string;
  updated_at: string;
}

export interface KnowledgeSource {
  id: string;
  kind: 'text' | 'pdf' | 'url' | 'bookmark';
  title: string;
  url?: string | null;
  status: 'pending' | 'processing' | 'ready' | 'failed';
  sha256: string;
  error?: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeChunk {
  id: string;
  source_id: string;
  ordinal: number;
  text: string;
  page?: number | null;
  paragraph?: number | null;
  offset: number;
}

export interface KnowledgeDetail extends KnowledgeSource {
  chunks: KnowledgeChunk[];
}

export interface KnowledgeHit extends KnowledgeChunk {
  title: string;
  url?: string | null;
  score: number;
}

export interface SearchProviderStatus {
  provider: string;
  auth_mode: string;
  configured: boolean;
  protocol: string;
}

export interface ConfigStatus {
  ready_for_planning: boolean;
  missing: string[];
  errors: string[];
  llm: { configured: boolean };
  amap: { configured: boolean };
  search: SearchProviderStatus[];
  connectivity_checked: boolean;
  restart_required_for_changes: boolean;
}

export interface RuntimeStatus {
  jobs: Record<string, number>;
  imports: Record<string, number>;
  cleanup: Record<string, number>;
  idle: boolean;
}

export interface ServiceCheck {
  id: string;
  service: 'llm' | 'search' | 'amap';
  success: boolean;
  details: Record<string, any>;
  checked_at: string;
}
