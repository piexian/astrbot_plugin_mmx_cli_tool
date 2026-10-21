import { ref, readonly } from 'vue';

export interface AstrBotViewContext {
  pluginName?: string;
  displayName?: string;
  pageName?: string;
  pageTitle?: string;
  locale?: string;
  i18n?: Record<string, any>;
  isDark?: boolean;
}

export interface SSEHandlers {
  onMessage?: (event: { raw: string; parsed: any; eventType: string; lastEventId?: string }) => void;
  onOpen?: () => void;
  onError?: () => void;
}

interface AstrBotPluginViewSDK {
  ready(): Promise<AstrBotViewContext>;
  getContext(): AstrBotViewContext | null;
  getLocale(): string;
  getI18n(): Record<string, any>;
  t(key: string, fallback?: string): string;
  onContext(handler: (ctx: AstrBotViewContext) => void): () => void;
  apiGet<T = any>(endpoint: string, params?: Record<string, any>): Promise<T>;
  apiPost<T = any>(endpoint: string, body?: any): Promise<T>;
  upload<T = any>(endpoint: string, file: File | Blob): Promise<T>;
  download(endpoint: string, params?: Record<string, any>, filename?: string): Promise<any>;
  subscribeSSE(endpoint: string, handlers: SSEHandlers, params?: Record<string, any>): Promise<string>;
  unsubscribeSSE(subscriptionId: string): Promise<any>;
}

declare global {
  interface Window {
    AstrBotPluginView?: AstrBotPluginViewSDK;
    AstrBotPluginPage?: AstrBotPluginViewSDK;
  }
}

export interface ToastNotice {
  id: number;
  kind: 'info' | 'ok' | 'warn';
  text: string;
}

const isDark = ref(document.documentElement.getAttribute('data-theme') === 'dark');
const locale = ref('zh-CN');
const bridgeConnected = ref(false);
const backendOnline = ref(false);
const toasts = ref<ToastNotice[]>([]);
let toastSeq = 0;
let initialized = false;

export function pushToast(text: string, kind: ToastNotice['kind'] = 'info', durationMs = 2800): void {
  toastSeq += 1;
  const id = toastSeq;
  toasts.value = [...toasts.value.slice(-3), { id, kind, text }];
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id);
  }, durationMs);
}

/**
 * Sandbox-safe clipboard copy (supports both Clipboard API and textarea fallback
 * when iframe lacks allow="clipboard-write").
 */
export async function copyToClipboard(text: string, label = '内容'): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text);
      pushToast(`已复制${label}`, 'ok');
      return true;
    }
  } catch {
    // Fallback below for sandboxed iframe
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    if (ok) {
      pushToast(`已复制${label}`, 'ok');
      return true;
    }
  } catch {
    // Ignore
  }

  pushToast(`复制失败，请手动选中复制${label}`, 'warn');
  return false;
}

export function setThemeMode(dark: boolean): void {
  isDark.value = dark;
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
}

export function toggleThemeMode(): void {
  setThemeMode(!isDark.value);
}

function getSDK(): AstrBotPluginViewSDK | undefined {
  return window.AstrBotPluginView || window.AstrBotPluginPage;
}

export function initBridge(attempt = 0): void {
  if (initialized) return;

  const sdk = getSDK();
  if (!sdk) {
    if (attempt < 25) {
      window.setTimeout(() => initBridge(attempt + 1), 60);
    }
    return;
  }
  initialized = true;

  sdk.onContext((ctx) => {
    bridgeConnected.value = true;
    if (typeof ctx?.isDark === 'boolean') {
      setThemeMode(ctx.isDark);
    }
    if (typeof ctx?.locale === 'string' && ctx.locale) {
      locale.value = ctx.locale;
    }
  });

  sdk
    .ready()
    .then((ctx) => {
      bridgeConnected.value = true;
      if (typeof ctx?.isDark === 'boolean') {
        setThemeMode(ctx.isDark);
      }
      if (typeof ctx?.locale === 'string' && ctx.locale) {
        locale.value = ctx.locale;
      }
    })
    .catch(() => {
      // Standalone mode
    });
}

export async function bridgeGet<T>(
  endpoint: string,
  params?: Record<string, any>,
  fallback?: () => T | Promise<T>,
): Promise<T> {
  const sdk = getSDK();
  if (sdk) {
    try {
      const res = await sdk.apiGet<T>(endpoint, params);
      backendOnline.value = true;
      return res;
    } catch (err) {
      if (!fallback) throw err;
    }
  }
  if (fallback) {
    return await fallback();
  }
  throw new Error(`Bridge endpoint GET ${endpoint} unavailable`);
}

export async function bridgePost<T>(
  endpoint: string,
  body?: any,
  fallback?: () => T | Promise<T>,
): Promise<T> {
  const sdk = getSDK();
  if (sdk) {
    try {
      const res = await sdk.apiPost<T>(endpoint, body);
      backendOnline.value = true;
      return res;
    } catch (err) {
      if (!fallback) throw err;
    }
  }
  if (fallback) {
    return await fallback();
  }
  throw new Error(`Bridge endpoint POST ${endpoint} unavailable`);
}

export async function bridgeUpload<T>(
  endpoint: string,
  file: File | Blob,
  fallback?: () => T | Promise<T>,
): Promise<T> {
  const sdk = getSDK();
  if (sdk) {
    try {
      const res = await sdk.upload<T>(endpoint, file);
      backendOnline.value = true;
      return res;
    } catch (err) {
      if (!fallback) throw err;
    }
  }
  if (fallback) {
    return await fallback();
  }
  throw new Error(`Bridge upload ${endpoint} unavailable`);
}

export async function bridgeDownload(
  endpoint: string,
  params: Record<string, any>,
  filename: string,
  fallbackDataUrl?: string,
): Promise<void> {
  const sdk = getSDK();
  if (sdk && backendOnline.value) {
    try {
      await sdk.download(endpoint, params, filename);
      pushToast(`已触发下载：${filename}`, 'ok');
      return;
    } catch {
      // Fallback to anchor download
    }
  }
  if (fallbackDataUrl) {
    const a = document.createElement('a');
    a.href = fallbackDataUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    pushToast(`已保存本地预览文件：${filename}`, 'ok');
    return;
  }
  pushToast('当前处于离线预览模式，挂载后端路由后可下载原文件', 'warn');
}

export function useBridge() {
  initBridge();
  return {
    isDark: readonly(isDark),
    locale: readonly(locale),
    bridgeConnected: readonly(bridgeConnected),
    backendOnline: readonly(backendOnline),
    toasts: readonly(toasts),
    pushToast,
    copyToClipboard,
    toggleThemeMode,
    setThemeMode,
    bridgeGet,
    bridgePost,
    bridgeUpload,
    bridgeDownload,
    subscribeSSE: (endpoint: string, handlers: SSEHandlers, params?: Record<string, any>) =>
      getSDK()?.subscribeSSE(endpoint, handlers, params),
    unsubscribeSSE: (subId: string) => getSDK()?.unsubscribeSSE(subId),
  };
}
