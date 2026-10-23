/**
 * MiniMax Code (MCode 3.1.4) + AstrBot Plugin Page Type Contracts.
 * Aligns 1:1 with:
 * - @mavis/local-runtime-v2/src/service/canvas/contracts.ts (CanvasDocumentV1 / CanvasOperationV1)
 * - @mavis/oauth-core (Device Authorization + PKCE)
 * - @mavis/agent-tools Matrix MCP tools (images_search_and_download, image_reverse_search, batch_text_to_music, batch_text_to_audio)
 * - M-Plan 2.0 dual-cycle quota (/backend/account/token_plan/remains_percent & usage_summary)
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
  model: string;
  thinking_effort: 'default' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  capabilities: string[];
}

export interface QuotaBucket {
  label: string;
  total: number;
  used: number;
  remaining: number;
  remaining_percent: number;
  /** 1 = normal, 2 = exhausted, 3 = unlimited */
  status: 1 | 2 | 3;
  reset_at_ms: number;
}

export interface MPlanQuotaResponse {
  plan_name: string;
  region: ServiceRegion;
  interval_5h: QuotaBucket;
  weekly_video: QuotaBucket;
  credit_fallback: {
    enabled: boolean;
    active_deducting: boolean;
    balance: string;
    consumed_today: string;
  };
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
 * Team Plan & Live SSE Terminal Stream
 * ========================================================================= */

export interface TeamPlanTaskItem {
  id: string;
  title: string;
  owner: string;
  status: 'pending' | 'in_progress' | 'completed' | 'blocked';
  target_files: string[];
  summary: string;
}

export interface TeamPlanState {
  plan_id: string;
  title: string;
  model: string;
  status: 'review_required' | 'approved' | 'executing' | 'completed';
  updated_at_ms: number;
  overview: string;
  tasks: TeamPlanTaskItem[];
}

export interface TerminalLogEntry {
  id: string;
  ts_ms: number;
  source: 'matrix_mcp' | 'oauth' | 'canvas' | 'plan' | 'llm';
  level: 'info' | 'ok' | 'warn' | 'error';
  command: string;
  detail: string;
  latency_ms?: number;
}
