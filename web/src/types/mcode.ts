/**
 * MiniMax Code + AstrBot Plugin Page Type Contracts.
 * Aligns 1:1 with mmx/web_api.py & mmx/runtime_logs.py.
 */

export type ServiceRegion = 'cn' | 'global';

export interface PluginStatusResponse {
  api_version: number;
  instance_id: string;
  state: 'ready' | 'initializing' | 'degraded' | 'closed';
  ready: boolean;
  region: ServiceRegion;
  auth_mode: 'oauth' | 'api_key' | 'none';
  oauth_connected: boolean;
  oauth_expires_at: number | null;
  account_label?: string;
  provider_source_warning?: string;
  model: string;
  thinking_effort: 'default' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  capabilities: string[];
}

export interface QuotaBucket {
  label: string;
  total: number;
  used: number;
  remaining: number;
  has_counts?: boolean;
  remaining_percent: number;
  /** 1 = normal, 2 = exhausted, 3 = unlimited */
  status: 1 | 2 | 3;
  reset_at_ms: number;
}

export interface AccountBalanceBreakdown {
  available?: string;
  cash?: string;
  voucher?: string;
  credit?: string;
  owed?: string;
  consumed?: string;
}

export interface MPlanQuotaResponse {
  plan_name: string;
  region: ServiceRegion;
  has_plan_quota?: boolean;
  has_interval?: boolean;
  has_weekly_video?: boolean;
  interval_5h: QuotaBucket;
  weekly_video: QuotaBucket;
  credit_fallback: {
    enabled: boolean;
    active_deducting: boolean;
    balance: string;
    consumed_today: string;
  };
  balance_breakdown?: AccountBalanceBreakdown | null;
  keys?: {
    total: number;
    failed: number;
  };
  updated_at_ms: number;
}

export interface AccountCreditDetailItem {
  credit_type?: number | string | null;
  category?: 'base' | 'bonus';
  label?: string;
  granted_amount?: string;
  remaining_amount?: string;
  consumed_amount?: string;
  granted_at_ms?: number | null;
  expire_at_ms?: number | null;
  status?: number | string | null;
}

export interface AccountModelRemainItem {
  model_name?: string;
  usage_count?: number | null;
  is_video?: boolean;
  remaining?: number | null;
  total?: number | null;
  used?: number | null;
  remaining_percent?: number | null;
  unlimited?: boolean;
  unavailable?: boolean;
  window_label?: string;
  reset_at_ms?: number | null;
}

export interface AccountSigninDayItem {
  day_no: number | null;
  points: number | null;
  bonus_points?: number | null;
  status: number | null;
  is_today: boolean;
  claimed: boolean;
}

export interface AccountSigninClaimResponse {
  ok: boolean;
  claimed: boolean;
  day_no?: number | null;
  points?: number | null;
  claim_id?: string;
}

export interface AccountUsageResponse {
  account: { name: string; email: string; region: ServiceRegion } | null;
  credit_balance: string;
  credit_split: { base: string; bonus: string } | null;
  fallback_enabled: boolean | null;
  credit_details: AccountCreditDetailItem[];
  credit_details_count?: number;
  credit_total_remaining: string;
  model_remains: AccountModelRemainItem[];
  signin: {
    scene?: number | null;
    days_total: number;
    today_day_no: number | null;
    today_points: number | null;
    today_claimed: boolean | null;
    days?: AccountSigninDayItem[];
  } | null;
  plan_name?: string;
  interval_5h?: QuotaBucket;
  weekly_video?: QuotaBucket;
  updated_at_ms: number;
}

export interface OAuthDeviceStartResponse {
  session_id: string;
  region: ServiceRegion;
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string;
  expires_in: number;
  interval: number;
}

export interface OAuthPollResponse {
  session_id: string;
  status: 'pending' | 'slow_down' | 'authorized' | 'denied' | 'expired' | 'error';
  region?: ServiceRegion;
  expires_at?: number;
  message?: string;
}

/* =========================================================================
 * Desktop Infinite Canvas Contracts (@mavis/local-runtime-v2)
 * ========================================================================= */

export interface CanvasLayoutV1 {
  x: number;
  y: number;
  width: number;
  height: number;
  zIndex: number;
}

export interface CanvasNormalizedRectV1 {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CanvasImageAnnotationV1 {
  id: string;
  number: number;
  normalizedRect: CanvasNormalizedRectV1;
  comment: string;
  action: 'comment' | 'erase';
  createdAtMs: number;
}

export interface CanvasFileNodeV1 {
  id: string;
  type: 'file_ref';
  file: {
    relativePath: string;
    assetId?: string;
    fileName?: string;
    mimeType?: string;
    sizeBytes?: number;
    sha256?: string;
    previewUrl?: string;
    textContent?: string;
  };
  layout: CanvasLayoutV1;
  annotations: CanvasImageAnnotationV1[];
}

export interface CanvasDocumentV1 {
  schemaVersion: 1;
  canvasId: string;
  sessionId: string;
  changeSeq: number;
  nodes: CanvasFileNodeV1[];
  updatedAtMs: number;
}

export type CanvasMutationV1 =
  | {
      kind: 'add_file';
      nodeId: string;
      relativePath: string;
      layout: CanvasLayoutV1;
      fileMeta?: Partial<CanvasFileNodeV1['file']>;
    }
  | {
      kind: 'update_layout';
      nodeId: string;
      layout: CanvasLayoutV1;
    }
  | {
      kind: 'remove_node';
      nodeId: string;
    }
  | {
      kind: 'add_annotation';
      nodeId: string;
      annotation: CanvasImageAnnotationV1;
    }
  | {
      kind: 'update_annotation';
      nodeId: string;
      annotation: CanvasImageAnnotationV1;
    }
  | {
      kind: 'remove_annotation';
      nodeId: string;
      annotationId: string;
    };

export interface CanvasOperationV1 {
  schemaVersion: 1;
  operationId: string;
  mutations: CanvasMutationV1[];
}

export interface CanvasIterateResultItem {
  node_id: string;
  file_name: string;
  ok: boolean;
  text?: string;
  error?: string;
}

export interface CanvasIterateResponse {
  ok: boolean;
  submitted: number;
  prompt_preview: string;
  skipped: number;
  results: CanvasIterateResultItem[];
}

/* =========================================================================
 * Multimodal Matrix Tools Contracts
 * ========================================================================= */

export interface ImageSearchItem {
  id: string;
  title: string;
  image_url: string;
  local_path?: string;
  source_domain?: string;
  width?: number;
  height?: number;
}

export interface ReverseImageMatchItem {
  title: string;
  link: string;
  source: string;
  image_url?: string;
  snippet?: string;
}

export interface MusicTrackItem {
  id: string;
  kind: 'music' | 'sfx';
  prompt: string;
  lyrics?: string;
  format: 'mp3' | 'wav';
  output_url: string;
  local_path?: string;
  created_at_ms: number;
  duration_hint?: string;
}

/* =========================================================================
 * Plugin Runtime Logs & Plugin HTTP Logs
 * ========================================================================= */

export type PluginLogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';

export interface RuntimeLogConfig {
  plugin_enabled: boolean;
  plugin_level: PluginLogLevel;
  http_enabled: boolean;
  http_include_headers: boolean;
  http_include_body: boolean;
  http_body_max_chars: number;
  http_only_errors: boolean;
  max_entries: number;
}

export interface HttpLogDetail {
  client: string;
  method: string;
  url: string;
  host: string;
  path: string;
  status_code: number | null;
  ok: boolean;
  stream: boolean;
  model?: string;
  error?: string;
  request_headers?: Record<string, string>;
  response_headers?: Record<string, string>;
  request_body?: string;
  response_body?: string;
}

export interface PluginLogEntry {
  id: string;
  ts_ms: number;
  category?: 'plugin' | 'http' | 'event';
  source: string;
  level: 'debug' | 'info' | 'ok' | 'warn' | 'error';
  command: string;
  detail: string;
  latency_ms?: number;
  module?: string;
  http?: HttpLogDetail;
}

export interface PluginLogsResponse {
  entries: PluginLogEntry[];
  config: RuntimeLogConfig;
  stats: {
    all: number;
    plugin: number;
    http: number;
    event: number;
    errors: number;
  };
}
