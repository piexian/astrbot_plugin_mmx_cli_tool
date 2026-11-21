<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import type { PluginLogEntry, PluginLogLevel, PluginLogsResponse, RuntimeLogConfig } from '../types/mcode';
import {
  bridgeGet,
  bridgePost,
  copyToClipboard,
  pushToast,
  reportError,
  useBridge,
} from '../composables/useBridge';
import { useSlidingIndicator } from '../composables/useSlidingIndicator';

const { backendOnline, subscribeSSE, unsubscribeSSE } = useBridge();

const DEFAULT_CONFIG: RuntimeLogConfig = {
  plugin_enabled: true,
  plugin_level: 'INFO',
  http_enabled: true,
  http_include_headers: false,
  http_include_body: false,
  http_body_max_chars: 1500,
  http_only_errors: false,
  max_entries: 500,
};

function createOfflinePreviewResponse(): PluginLogsResponse {
  const now = Date.now();
  const entries: PluginLogEntry[] = [
    {
      id: 'preview_http_1',
      ts_ms: now - 14000,
      category: 'http',
      source: 'minimax_api',
      level: 'ok',
      command: 'GET /minimax-cloud/api/v1/credit/details',
      detail: 'HTTP 200',
      latency_ms: 142,
      http: {
        client: 'minimax_api',
        method: 'GET',
        url: 'https://agent.minimax.cn/minimax-cloud/api/v1/credit/details?app_id=3001&region=cn',
        host: 'agent.minimax.cn',
        path: '/minimax-cloud/api/v1/credit/details?app_id=3001&region=cn',
        status_code: 200,
        ok: true,
        stream: false,
        request_headers: {
          Accept: 'application/json',
          Authorization: 'Bearer mmoa***9f2a',
          'User-Agent': 'MiniMaxAgent',
          'x-signature': '[REDACTED]',
          yy: '[REDACTED]',
        },
        response_headers: {
          'content-type': 'application/json; charset=utf-8',
        },
        response_body: JSON.stringify(
          {
            base_resp: { status_code: 0 },
            details: [
              { credit_type: 1, granted_amount: '6000', remaining_amount: '4256' },
              { credit_type: 2, granted_amount: '2400', remaining_amount: '2400' },
            ],
          },
          null,
          2,
        ),
      },
    },
    {
      id: 'preview_plugin_1',
      ts_ms: now - 38000,
      category: 'plugin',
      source: 'web_api',
      level: 'info',
      command: '[mmx-web] 已注册 21 条工作台 Web API 路由',
      detail: '',
      module: 'mmx/web_api.py:390',
    },
    {
      id: 'preview_http_2',
      ts_ms: now - 65000,
      category: 'http',
      source: 'llm_provider',
      level: 'ok',
      command: 'POST /mavis/api/v1/llm/v1/messages',
      detail: 'HTTP 200 · stream · model=MiniMax-M3.1-Flash',
      latency_ms: 814,
      http: {
        client: 'llm_provider',
        method: 'POST',
        url: 'https://agent.minimax.cn/mavis/api/v1/llm/v1/messages',
        host: 'agent.minimax.cn',
        path: '/mavis/api/v1/llm/v1/messages',
        status_code: 200,
        ok: true,
        stream: true,
        model: 'MiniMax-M3.1-Flash',
        request_headers: {
          Authorization: 'Bearer mmoa***9f2a',
          'anthropic-version': '2023-06-01',
          'Content-Type': 'application/json',
        },
        request_body: JSON.stringify(
          {
            model: 'MiniMax-M3.1-Flash',
            stream: true,
            output_config: { effort: 'high' },
          },
          null,
          2,
        ),
        response_body: '[SSE / streaming response]',
      },
    },
    {
      id: 'preview_plugin_2',
      ts_ms: now - 110000,
      category: 'plugin',
      source: 'plugin',
      level: 'warn',
      command: '[mmx] 图像生成 H3 走桌面网关路由，已自动附带分辨率参数',
      detail: '',
      module: 'mmx/apis/image.py:184',
    },
  ];
  return {
    entries,
    config: { ...DEFAULT_CONFIG, http_include_headers: true, http_include_body: true },
    stats: {
      all: entries.length,
      plugin: 2,
      http: 2,
      event: 0,
      errors: 1,
    },
  };
}

const config = ref<RuntimeLogConfig>({ ...DEFAULT_CONFIG });
const entries = ref<PluginLogEntry[]>([]);
const isLoading = ref(false);
const isUpdatingConfig = ref(false);
const streamConnected = ref(false);
const streamPaused = ref(false);
const bufferedWhilePaused = ref<PluginLogEntry[]>([]);

const categoryFilter = ref<'all' | 'plugin' | 'http' | 'event'>('all');
const levelFilter = ref<'all' | 'debug' | 'info' | 'warn' | 'error'>('all');
const searchText = ref('');
const expandedId = ref<string | null>(null);

const levelSegmentedRef = ref<HTMLElement | null>(null);
const { indicatorStyle: levelIndicatorStyle } = useSlidingIndicator(levelSegmentedRef, () => config.value.plugin_level);

const categoryTabsRef = ref<HTMLElement | null>(null);
const { indicatorStyle: categoryIndicatorStyle } = useSlidingIndicator(categoryTabsRef, [
  categoryFilter,
  () => entries.value.length,
]);

let sseSubId: string | null = null;

const LEVEL_OPTIONS: Array<{ value: PluginLogLevel; label: string }> = [
  { value: 'DEBUG', label: 'DEBUG' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARNING', label: 'WARN' },
  { value: 'ERROR', label: 'ERROR' },
];

function entryCategory(item: PluginLogEntry): 'plugin' | 'http' | 'event' {
  if (item.category === 'plugin' || item.category === 'http' || item.category === 'event') {
    return item.category;
  }
  return item.http ? 'http' : 'event';
}

const categoryCounts = computed(() => {
  let plugin = 0;
  let http = 0;
  let event = 0;
  let errors = 0;
  for (const item of entries.value) {
    const cat = entryCategory(item);
    if (cat === 'plugin') plugin += 1;
    else if (cat === 'http') http += 1;
    else event += 1;
    if (item.level === 'error' || item.level === 'warn') errors += 1;
  }
  return { all: entries.value.length, plugin, http, event, errors };
});

const visibleEntries = computed(() => {
  const q = searchText.value.trim().toLowerCase();
  return entries.value.filter((item) => {
    const cat = entryCategory(item);
    if (categoryFilter.value !== 'all' && cat !== categoryFilter.value) return false;
    if (levelFilter.value !== 'all') {
      if (levelFilter.value === 'info') {
        if (item.level !== 'info' && item.level !== 'ok') return false;
      } else if (item.level !== levelFilter.value) {
        return false;
      }
    }
    if (!q) return true;
    const haystack = [
      item.command,
      item.detail,
      item.source,
      item.module,
      item.http?.url,
      item.http?.method,
      item.http?.status_code !== undefined && item.http?.status_code !== null
        ? String(item.http.status_code)
        : '',
      item.http?.client,
      item.http?.model,
      item.http?.error,
      item.http?.request_body,
      item.http?.response_body,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return haystack.includes(q);
  });
});

function formatTime(ms: number): string {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const frac = String(d.getMilliseconds()).padStart(3, '0');
  return `${hh}:${mm}:${ss}.${frac}`;
}

function levelBadgeTone(level: PluginLogEntry['level']): string {
  if (level === 'error') return 'danger';
  if (level === 'warn') return 'warn';
  if (level === 'ok') return 'ok';
  if (level === 'debug') return '';
  return 'info';
}

function levelLabel(level: PluginLogEntry['level']): string {
  if (level === 'error') return 'ERROR';
  if (level === 'warn') return 'WARN';
  if (level === 'ok') return 'OK';
  if (level === 'debug') return 'DEBUG';
  return 'INFO';
}

function statusCodeTone(status: number | null | undefined, ok: boolean): string {
  if (status === null || status === undefined) return 'danger';
  if (status >= 500) return 'danger';
  if (status >= 400 || !ok) return 'warn';
  return 'ok';
}

function sourceDisplay(source: string): string {
  const map: Record<string, string> = {
    minimax_api: 'MiniMax API',
    llm_provider: 'LLM Provider',
    media_transfer: 'Media OSS',
    oauth: 'OAuth',
    web_api: 'Web API',
    plugin: 'Plugin',
    tools: 'Tools',
    apis: 'APIs',
    account: 'Account',
    matrix_mcp: 'Matrix',
    canvas: 'Canvas',
    plan: 'Dashboard',
  };
  return map[source] || source;
}

function prependEntry(entry: PluginLogEntry): void {
  const cap = Math.max(50, config.value.max_entries || 500);
  if (entries.value.some((existing) => existing.id === entry.id)) return;
  entries.value = [entry, ...entries.value].slice(0, cap);
}

async function fetchLogs(): Promise<void> {
  isLoading.value = true;
  try {
    const res = await bridgeGet<PluginLogsResponse>(
      'logs',
      { limit: config.value.max_entries || 500 },
      () => createOfflinePreviewResponse(),
    );
    if (res) {
      if (res.config) config.value = { ...DEFAULT_CONFIG, ...res.config };
      entries.value = Array.isArray(res.entries) ? res.entries : [];
      bufferedWhilePaused.value = [];
    }
  } catch (err) {
    reportError(err, '拉取插件日志失败');
  } finally {
    isLoading.value = false;
  }
}

async function patchRuntimeConfig(patch: Partial<RuntimeLogConfig>, notice?: string): Promise<void> {
  const nextLocal = { ...config.value, ...patch };
  config.value = nextLocal;
  isUpdatingConfig.value = true;
  try {
    const res = await bridgePost<{ ok: boolean; config: RuntimeLogConfig }>(
      'logs/config',
      patch,
      () => ({ ok: true, config: nextLocal }),
    );
    if (res?.config) {
      config.value = { ...DEFAULT_CONFIG, ...res.config };
    }
    if (patch.max_entries && entries.value.length > config.value.max_entries) {
      entries.value = entries.value.slice(0, config.value.max_entries);
    }
    if (notice) {
      pushToast(`${notice}（仅运行时生效，未改配置）`, 'ok', 2200);
    }
  } catch (err) {
    reportError(err, '更新日志采集设置失败');
  } finally {
    isUpdatingConfig.value = false;
  }
}

function togglePause(): void {
  streamPaused.value = !streamPaused.value;
  if (!streamPaused.value && bufferedWhilePaused.value.length) {
    for (const item of [...bufferedWhilePaused.value].reverse()) {
      prependEntry(item);
    }
    bufferedWhilePaused.value = [];
  }
}

async function handleClear(): Promise<void> {
  const cat = categoryFilter.value;
  try {
    await bridgePost('logs/clear', { category: cat }, () => ({ ok: true, cleared: entries.value.length }));
    if (cat === 'all') {
      entries.value = [];
      bufferedWhilePaused.value = [];
    } else {
      entries.value = entries.value.filter((item) => entryCategory(item) !== cat);
      bufferedWhilePaused.value = bufferedWhilePaused.value.filter(
        (item) => entryCategory(item) !== cat,
      );
    }
    expandedId.value = null;
    pushToast(cat === 'all' ? '已清空内存日志缓冲' : `已清空当前分类日志`, 'ok', 2000);
  } catch (err) {
    reportError(err, '清空日志失败');
  }
}

function formatEntryText(item: PluginLogEntry): string {
  const header = `[${formatTime(item.ts_ms)}] [${entryCategory(item).toUpperCase()}] [${levelLabel(item.level)}] [${item.source}] ${item.command}`;
  const lines = [header];
  if (item.module) lines.push(`  module: ${item.module}`);
  if (item.latency_ms !== undefined) lines.push(`  latency: ${item.latency_ms}ms`);
  if (item.detail) lines.push(`  detail: ${item.detail}`);
  if (item.http) {
    lines.push(`  url: ${item.http.url}`);
    if (item.http.error) lines.push(`  error: ${item.http.error}`);
    if (item.http.request_headers) {
      lines.push(`  request_headers: ${JSON.stringify(item.http.request_headers)}`);
    }
    if (item.http.request_body) lines.push(`  request_body:\n${item.http.request_body}`);
    if (item.http.response_headers) {
      lines.push(`  response_headers: ${JSON.stringify(item.http.response_headers)}`);
    }
    if (item.http.response_body) lines.push(`  response_body:\n${item.http.response_body}`);
  }
  return lines.join('\n');
}

function copyVisibleLogs(): void {
  if (!visibleEntries.value.length) {
    pushToast('当前没有可复制的日志条目', 'warn');
    return;
  }
  const text = visibleEntries.value.slice(0, 120).map(formatEntryText).join('\n\n');
  copyToClipboard(text, `${Math.min(visibleEntries.value.length, 120)} 条日志`);
}

function toggleExpand(id: string): void {
  expandedId.value = expandedId.value === id ? null : id;
}

onMounted(async () => {
  await fetchLogs();
  try {
    const id = await subscribeSSE?.('events', {
      onOpen() {
        streamConnected.value = true;
      },
      onError() {
        streamConnected.value = false;
      },
      onMessage(evt) {
        if (!evt.parsed || typeof evt.parsed !== 'object' || !evt.parsed.command) return;
        const incoming: PluginLogEntry = {
          id: evt.parsed.id || `sse_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          ts_ms: evt.parsed.ts_ms || Date.now(),
          category: evt.parsed.category || (evt.parsed.http ? 'http' : 'event'),
          source: evt.parsed.source || 'plugin',
          level: evt.parsed.level || 'info',
          command: evt.parsed.command,
          detail: evt.parsed.detail || '',
          latency_ms: evt.parsed.latency_ms,
          module: evt.parsed.module,
          http: evt.parsed.http,
        };
        if (streamPaused.value) {
          bufferedWhilePaused.value = [incoming, ...bufferedWhilePaused.value].slice(0, 200);
        } else {
          prependEntry(incoming);
        }
      },
    });
    if (id) {
      sseSubId = id;
      streamConnected.value = true;
    }
  } catch {
    streamConnected.value = false;
  }
});

onUnmounted(() => {
  if (sseSubId) {
    unsubscribeSSE?.(sseSubId).catch(() => {});
  }
});
</script>

<template>
  <div class="logs-root">
    <!-- Top Editorial Header -->
    <header class="logs-header">
      <div class="header-left">
        <div class="title-row">
          <h2 class="serif-heading logs-title">插件运行日志</h2>
          <span
            class="pill-badge"
            :class="streamPaused ? 'warn' : streamConnected || backendOnline ? 'ok' : ''"
          >
            <span
              class="live-dot"
              :class="{ active: !streamPaused && (streamConnected || backendOnline), paused: streamPaused }"
            ></span>
            {{
              streamPaused
                ? `已暂停${bufferedWhilePaused.length ? ` (+${bufferedWhilePaused.length})` : ''}`
                : streamConnected || backendOnline
                  ? '实时流已连接'
                  : '离线预览示例'
            }}
          </span>
        </div>
        <p class="logs-subtitle">
          仅隔离显示本插件日志与本插件发起的 HTTP 请求；下方所有开关均在内存中即时生效，不会修改插件配置。
        </p>
      </div>

      <div class="header-actions">
        <button type="button" class="btn" :disabled="isLoading" @click="fetchLogs">
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            :class="{ 'is-spinning': isLoading }"
          >
            <path d="M21 2v6h-6" />
            <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
            <path d="M3 22v-6h6" />
            <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
          </svg>
          刷新
        </button>
        <button type="button" class="btn" :class="{ 'btn-soft': streamPaused }" @click="togglePause">
          {{ streamPaused ? '继续接收' : '暂停实时' }}
        </button>
        <button type="button" class="btn" @click="copyVisibleLogs">复制日志</button>
        <button type="button" class="btn" @click="handleClear">清空</button>
      </div>
    </header>

    <!-- Runtime Live Adjustment Panel (No Config File Mutation) -->
    <section class="controls-card" aria-label="运行时日志采集调节">
      <!-- Plugin Log Capture Group -->
      <div class="control-group">
        <div class="group-top">
          <label class="claude-switch">
            <input
              type="checkbox"
              :checked="config.plugin_enabled"
              :disabled="isUpdatingConfig"
              @change="
                patchRuntimeConfig(
                  { plugin_enabled: ($event.target as HTMLInputElement).checked },
                  ($event.target as HTMLInputElement).checked ? '已开启本插件日志捕获' : '已暂停本插件日志捕获',
                )
              "
            />
            <span class="claude-switch-track"></span>
            <span class="group-title">捕获插件日志</span>
          </label>

          <div ref="levelSegmentedRef" class="level-segmented sliding-track" role="group" aria-label="插件日志捕获级别">
            <span class="sliding-pill" :style="levelIndicatorStyle()" aria-hidden="true"></span>
            <button
              v-for="opt in LEVEL_OPTIONS"
              :key="opt.value"
              type="button"
              class="seg-pill mono-num"
              :class="{ active: config.plugin_level === opt.value }"
              :disabled="!config.plugin_enabled || isUpdatingConfig"
              @click="
                patchRuntimeConfig(
                  { plugin_level: opt.value },
                  `本插件日志级别已切至 ${opt.label}`,
                )
              "
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="group-hint">
          切至 <strong class="mono-num">DEBUG</strong> 可临时查看本插件调试日志，不影响 AstrBot 全局日志配置。
        </div>
      </div>

      <div class="controls-divider"></div>

      <!-- Plugin HTTP Log Capture Group -->
      <div class="control-group">
        <div class="group-top">
          <label class="claude-switch">
            <input
              type="checkbox"
              :checked="config.http_enabled"
              :disabled="isUpdatingConfig"
              @change="
                patchRuntimeConfig(
                  { http_enabled: ($event.target as HTMLInputElement).checked },
                  ($event.target as HTMLInputElement).checked ? '已开启本插件 HTTP 日志' : '已关闭本插件 HTTP 日志',
                )
              "
            />
            <span class="claude-switch-track"></span>
            <span class="group-title">捕获 HTTP 日志</span>
          </label>

          <div class="http-toggles">
            <button
              type="button"
              class="toggle-chip"
              :class="{ active: config.http_only_errors }"
              :disabled="!config.http_enabled || isUpdatingConfig"
              @click="
                patchRuntimeConfig(
                  { http_only_errors: !config.http_only_errors },
                  !config.http_only_errors ? 'HTTP 日志切换为仅记录错误' : 'HTTP 日志恢复记录全部请求',
                )
              "
            >
              仅看错误
            </button>
            <button
              type="button"
              class="toggle-chip"
              :class="{ active: config.http_include_headers }"
              :disabled="!config.http_enabled || isUpdatingConfig"
              @click="
                patchRuntimeConfig(
                  { http_include_headers: !config.http_include_headers },
                  !config.http_include_headers ? '已开启脱敏请求头记录' : '已关闭请求头记录',
                )
              "
            >
              含请求头 (脱敏)
            </button>
            <button
              type="button"
              class="toggle-chip"
              :class="{ active: config.http_include_body }"
              :disabled="!config.http_enabled || isUpdatingConfig"
              @click="
                patchRuntimeConfig(
                  { http_include_body: !config.http_include_body },
                  !config.http_include_body ? '已开启请求/响应体预览' : '已关闭请求/响应体预览',
                )
              "
            >
              含 Body 预览
            </button>
            <label class="buffer-select-wrap">
              <span>缓冲</span>
              <select
                class="buffer-select mono-num"
                :value="config.max_entries"
                :disabled="isUpdatingConfig"
                @change="
                  patchRuntimeConfig(
                    { max_entries: Number(($event.target as HTMLSelectElement).value) },
                    `日志缓冲上限已调整为 ${($event.target as HTMLSelectElement).value} 条`,
                  )
                "
              >
                <option :value="200">200</option>
                <option :value="500">500</option>
                <option :value="1000">1000</option>
                <option :value="2000">2000</option>
              </select>
            </label>
          </div>
        </div>
        <div class="group-hint">
          覆盖 MiniMax API、OAuth、媒体上传下载与 LLM Provider；Authorization、API Key 与签名头自动脱敏。
        </div>
      </div>
    </section>

    <!-- Filter & Search Bar -->
    <div class="filter-toolbar">
      <div ref="categoryTabsRef" class="category-tabs sliding-track" role="tablist" aria-label="日志分类">
        <span class="sliding-pill" :style="categoryIndicatorStyle()" aria-hidden="true"></span>
        <button
          type="button"
          class="cat-tab"
          :class="{ active: categoryFilter === 'all' }"
          @click="categoryFilter = 'all'"
        >
          全部 <span class="tab-badge mono-num">{{ categoryCounts.all }}</span>
        </button>
        <button
          type="button"
          class="cat-tab"
          :class="{ active: categoryFilter === 'plugin' }"
          @click="categoryFilter = 'plugin'"
        >
          插件日志 <span class="tab-badge mono-num">{{ categoryCounts.plugin }}</span>
        </button>
        <button
          type="button"
          class="cat-tab"
          :class="{ active: categoryFilter === 'http' }"
          @click="categoryFilter = 'http'"
        >
          HTTP 日志 <span class="tab-badge mono-num">{{ categoryCounts.http }}</span>
        </button>
        <button
          type="button"
          class="cat-tab"
          :class="{ active: categoryFilter === 'event' }"
          @click="categoryFilter = 'event'"
        >
          工作台操作 <span class="tab-badge mono-num">{{ categoryCounts.event }}</span>
        </button>
      </div>

      <div class="search-center-slot">
        <div class="search-wrap">
          <svg
            class="search-icon"
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="searchText"
            type="text"
            class="search-input"
            placeholder="搜索路径、状态码、模块或关键词…"
          />
          <button
            v-if="searchText"
            type="button"
            class="search-clear"
            aria-label="清除搜索"
            @click="searchText = ''"
          >
            ×
          </button>
        </div>
      </div>

      <div class="filter-right">
        <select v-model="levelFilter" class="level-filter-select" aria-label="按级别筛选">
          <option value="all">全部级别</option>
          <option value="debug">DEBUG</option>
          <option value="info">INFO / OK</option>
          <option value="warn">WARN</option>
          <option value="error">ERROR</option>
        </select>
      </div>
    </div>

    <!-- Structured Interactive Log Stream -->
    <section class="stream-panel" aria-label="日志流列表">
      <div v-if="visibleEntries.length" class="stream-list">
        <div
          v-for="item in visibleEntries"
          :key="item.id"
          class="log-row-card"
          :class="[item.level, { expanded: expandedId === item.id }]"
        >
          <button
            type="button"
            class="log-row-summary"
            :aria-expanded="expandedId === item.id"
            @click="toggleExpand(item.id)"
          >
            <span class="row-time mono-num">{{ formatTime(item.ts_ms) }}</span>

            <span class="pill-badge cat-pill mono-num" :class="entryCategory(item) === 'http' ? 'accent' : ''">
              {{
                entryCategory(item) === 'http'
                  ? 'HTTP'
                  : entryCategory(item) === 'plugin'
                    ? 'LOG'
                    : 'EVENT'
              }}
            </span>

            <span class="pill-badge level-pill mono-num" :class="levelBadgeTone(item.level)">
              {{ levelLabel(item.level) }}
            </span>

            <span class="row-source">{{ sourceDisplay(item.source) }}</span>

            <!-- HTTP Method & Status Pills -->
            <template v-if="item.http">
              <span class="http-method mono-num">{{ item.http.method }}</span>
              <span
                class="pill-badge mono-num"
                :class="statusCodeTone(item.http.status_code, item.http.ok)"
              >
                {{ item.http.status_code ?? 'ERR' }}
              </span>
            </template>

            <span class="row-message mono-num">
              {{ item.http ? item.http.path : item.command }}
            </span>

            <span v-if="item.latency_ms !== undefined" class="row-latency mono-num">
              {{ item.latency_ms }}ms
            </span>

            <svg
              class="row-chevron"
              :class="{ open: expandedId === item.id }"
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>

          <!-- Expanded Detail Inspector -->
          <Transition name="drawer-reveal">
            <div v-if="expandedId === item.id" class="log-row-drawer">
            <div class="drawer-toolbar">
              <div class="drawer-meta mono-num">
                <span v-if="item.module">{{ item.module }}</span>
                <span v-if="item.http?.host">{{ item.http.host }}</span>
                <span v-if="item.http?.model">model: {{ item.http.model }}</span>
                <span v-if="item.http?.stream">SSE stream</span>
              </div>
              <div class="drawer-actions">
                <button
                  v-if="item.http?.url"
                  type="button"
                  class="btn btn-mini"
                  @click.stop="copyToClipboard(item.http.url, '请求 URL')"
                >
                  复制 URL
                </button>
                <button
                  type="button"
                  class="btn btn-mini"
                  @click.stop="copyToClipboard(formatEntryText(item), '日志详情')"
                >
                  复制此条
                </button>
              </div>
            </div>

            <!-- HTTP Inspector -->
            <template v-if="item.http">
              <div class="drawer-field">
                <div class="field-k">请求地址</div>
                <div class="code-block mono-num">{{ item.http.method }} {{ item.http.url }}</div>
              </div>

              <div v-if="item.http.error" class="drawer-field">
                <div class="field-k danger-label">错误信息</div>
                <div class="code-block error-block mono-num">{{ item.http.error }}</div>
              </div>

              <div v-if="item.http.request_headers || item.http.response_headers" class="drawer-grid">
                <div v-if="item.http.request_headers" class="drawer-field">
                  <div class="field-k">请求头（已脱敏）</div>
                  <pre class="code-block mono-num">{{ JSON.stringify(item.http.request_headers, null, 2) }}</pre>
                </div>
                <div v-if="item.http.response_headers" class="drawer-field">
                  <div class="field-k">响应头（已脱敏）</div>
                  <pre class="code-block mono-num">{{ JSON.stringify(item.http.response_headers, null, 2) }}</pre>
                </div>
              </div>

              <div v-if="item.http.request_body || item.http.response_body" class="drawer-grid">
                <div v-if="item.http.request_body" class="drawer-field">
                  <div class="field-k">请求体预览</div>
                  <pre class="code-block mono-num">{{ item.http.request_body }}</pre>
                </div>
                <div v-if="item.http.response_body" class="drawer-field">
                  <div class="field-k">响应体预览</div>
                  <pre class="code-block mono-num">{{ item.http.response_body }}</pre>
                </div>
              </div>

              <div
                v-if="!item.http.request_headers && !item.http.request_body && !item.http.response_body"
                class="drawer-tip"
              >
                当前未记录请求头或 Body；可在上方开启「含请求头 (脱敏)」或「含 Body 预览」以捕获后续请求的完整报文。
              </div>
            </template>

            <!-- Plugin / Event Inspector -->
            <template v-else>
              <div class="drawer-field">
                <div class="field-k">日志内容</div>
                <pre class="code-block mono-num">{{ item.command }}</pre>
              </div>
              <div v-if="item.detail && item.detail !== item.command" class="drawer-field">
                <div class="field-k">详细信息 / 堆栈</div>
                <pre class="code-block mono-num">{{ item.detail }}</pre>
              </div>
            </template>
            </div>
          </Transition>
        </div>
      </div>

      <div v-else class="stream-empty">
        <div class="empty-title">
          {{ entries.length ? '没有匹配当前筛选条件的日志' : '暂未捕获到本插件日志' }}
        </div>
        <p class="empty-desc">
          {{
            entries.length
              ? '尝试切换分类标签、放宽级别过滤或清空搜索词。'
              : '当本插件执行聊天指令、刷新积分用量、调用生图/视频/音乐或触发 OAuth 与 LLM 请求时，日志与 HTTP 报文会实时显示在此处。'
          }}
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.logs-root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 18px 22px;
  gap: 12px;
  overflow: hidden;
  background: var(--bg-canvas);
}

.logs-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.header-left {
  min-width: 0;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.logs-title {
  margin: 0;
  font-size: 20px;
  line-height: 1.2;
  color: var(--text-primary);
}

.logs-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
}

.live-dot.active {
  background: var(--status-ok);
}

.live-dot.paused {
  background: var(--status-warn);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

/* Runtime Controls Card */
.controls-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 13px 16px;
  box-shadow: var(--shadow-card);
  display: grid;
  grid-template-columns: minmax(260px, 0.9fr) 1px minmax(360px, 1.35fr);
  gap: 16px;
  align-items: center;
}

.controls-divider {
  width: 1px;
  height: 100%;
  min-height: 44px;
  background: var(--border-hairline);
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.group-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.group-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.group-hint {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 1.4;
}

.level-segmented {
  display: inline-flex;
  padding: 3px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
}

.seg-pill {
  padding: 3px 9px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: color 160ms var(--ease-out), transform 120ms var(--ease-out);
}

.seg-pill:hover:not(:disabled) {
  color: var(--text-primary);
}

.seg-pill:active:not(:disabled) {
  transform: scale(0.95);
}

.seg-pill.active {
  color: var(--accent);
}

.seg-pill:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.http-toggles {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.toggle-chip {
  padding: 4px 9px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.14s var(--ease-out);
}

.toggle-chip:hover:not(:disabled) {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.toggle-chip.active {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent);
  font-weight: 600;
}

.toggle-chip:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.buffer-select-wrap {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  font-size: 11.5px;
  color: var(--text-secondary);
}

.buffer-select {
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-weight: 600;
  cursor: pointer;
  outline: none;
}

/* Filter & Search Bar */
.filter-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  width: 100%;
  min-width: 0;
}

.category-tabs {
  display: inline-flex;
  padding: 3px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
  gap: 2px;
  max-width: 100%;
  flex-shrink: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.category-tabs::-webkit-scrollbar {
  display: none;
}

.cat-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
  cursor: pointer;
  transition: color 160ms var(--ease-out), transform 120ms var(--ease-out);
}

.cat-tab:hover {
  color: var(--text-primary);
}

.cat-tab:active {
  transform: scale(0.97);
}

.cat-tab.active {
  color: var(--accent);
  font-weight: 600;
}

.tab-badge {
  font-size: 10.5px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--bg-surface-muted);
  color: var(--text-secondary);
}

.cat-tab.active .tab-badge {
  background: var(--accent-soft);
  color: var(--accent);
}

.search-center-slot {
  flex: 1 1 140px;
  min-width: 120px;
  max-width: 100%;
  display: flex;
  justify-content: center;
}

.search-wrap {
  position: relative;
  width: 100%;
  max-width: min(440px, 100%);
  min-width: 0;
}

.filter-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.level-filter-select {
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}

.search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-tertiary);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 6px 26px 6px 30px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  transition: border-color 0.14s var(--ease-out), box-shadow 0.14s var(--ease-out);
}

.search-input:focus {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.search-clear {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 15px;
  cursor: pointer;
  padding: 2px 5px;
}

/* Stream Container */
.stream-panel {
  flex: 1;
  min-height: 0;
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
  overflow-y: auto;
}

.stream-list {
  display: flex;
  flex-direction: column;
}

.log-row-card {
  border-bottom: 1px solid var(--border-hairline);
  transition: background-color 0.12s var(--ease-out);
}

.log-row-card:last-child {
  border-bottom: none;
}

.log-row-card.error {
  background: rgba(185, 56, 44, 0.03);
}

.log-row-card.warn {
  background: rgba(168, 100, 11, 0.025);
}

.log-row-summary {
  width: 100%;
  border: none;
  background: transparent;
  padding: 9px 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s var(--ease-out);
}

.log-row-summary:hover {
  background: var(--bg-surface-muted);
}

.row-time {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.cat-pill,
.level-pill {
  padding: 1px 6px;
  font-size: 10px;
  flex-shrink: 0;
}

.row-source {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
  flex-shrink: 0;
  min-width: 72px;
}

.http-method {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
  flex-shrink: 0;
}

.row-message {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-latency {
  font-size: 11px;
  color: var(--status-ok);
  flex-shrink: 0;
}

.row-chevron {
  color: var(--text-tertiary);
  flex-shrink: 0;
  transition: transform 0.15s var(--ease-out);
}

.row-chevron.open {
  transform: rotate(90deg);
  color: var(--accent);
}

/* Expanded Drawer */
.log-row-drawer {
  padding: 12px 16px 14px;
  background: var(--bg-canvas);
  border-top: 1px dashed var(--border-hairline);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.drawer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.drawer-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--text-secondary);
}

.drawer-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.btn-mini {
  padding: 3px 9px;
  font-size: 11.5px;
}

.drawer-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
}

.drawer-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.field-k {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.danger-label {
  color: var(--status-danger);
}

.code-block {
  margin: 0;
  padding: 9px 11px;
  border-radius: var(--radius-xs);
  border: 1px solid var(--border-hairline);
  background: var(--bg-code);
  color: var(--text-primary);
  font-size: 11.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow: auto;
}

.error-block {
  border-color: var(--status-danger-border);
  background: var(--status-danger-bg);
  color: var(--status-danger);
}

.drawer-tip {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.stream-empty {
  padding: 56px 24px;
  text-align: center;
  max-width: 480px;
  margin: 0 auto;
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
}

.empty-desc {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

@media (max-width: 960px) {
  .controls-card {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .controls-divider {
    width: 100%;
    height: 1px;
    min-height: 1px;
  }
}

@media (max-width: 680px) {
  .logs-root {
    padding: 12px;
  }

  .search-center-slot {
    flex: 1 1 100%;
    order: 2;
  }

  .search-wrap {
    max-width: 100%;
  }

  .filter-right {
    order: 3;
    width: 100%;
    justify-content: flex-end;
  }

  .row-source {
    display: none;
  }

  .log-row-summary {
    flex-wrap: wrap;
    row-gap: 5px;
    padding: 9px 12px;
  }

  .row-message {
    flex: 1 1 100%;
    order: 10;
    padding-top: 2px;
  }

  .row-latency {
    margin-left: auto;
  }
}
</style>
