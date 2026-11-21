import { reactive, watch } from 'vue';
import { bridgeSilentGet, bridgeSilentPost, useBridge } from './useBridge';

/**
 * Unified UI preference store that works reliably inside AstrBot's opaque-origin
 * sandboxed iframe (`sandbox="allow-scripts allow-forms allow-downloads"` where
 * `localStorage` throws SecurityError) as well as standalone browser preview:
 *
 * 1. Module-level reactive memory store — synchronous, survives all in-page tab
 *    and sub-tab switches immediately with zero flicker.
 * 2. Best-effort `localStorage` — survives standalone preview reloads.
 * 3. Plugin backend persistence (`GET ui/prefs`, `POST ui/prefs/save`) — survives
 *    switching AstrBot sidebar pages (which recreates the iframe) and browser reloads.
 */
const memoryPrefs = reactive<Record<string, unknown>>({});
const localDirtyKeys = new Set<string>();
const pendingBackendPatch: Record<string, unknown> = {};

let storageProbed = false;
let storageAvailable = false;
let hydratedFromBackend = false;
let hydratePromise: Promise<void> | null = null;
let flushTimer: number | null = null;
let lifecycleBound = false;

function hasOwn(obj: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function canUseLocalStorage(): boolean {
  if (storageProbed) return storageAvailable;
  storageProbed = true;
  try {
    const testKey = '__mcode_storage_probe__';
    window.localStorage.setItem(testKey, '1');
    window.localStorage.removeItem(testKey);
    storageAvailable = true;
  } catch {
    storageAvailable = false;
  }
  return storageAvailable;
}

function readLocalStorage<T>(key: string): { found: boolean; value?: T } {
  if (!canUseLocalStorage()) return { found: false };
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return { found: false };
    return { found: true, value: JSON.parse(raw) as T };
  } catch {
    return { found: false };
  }
}

function writeLocalStorage(key: string, value: unknown): void {
  if (!canUseLocalStorage()) return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore quota / sandbox errors
  }
}

function bindLifecycleFlush(): void {
  if (lifecycleBound || typeof window === 'undefined') return;
  lifecycleBound = true;
  window.addEventListener('pagehide', () => {
    void flushUiPrefs();
  });
  window.addEventListener('beforeunload', () => {
    void flushUiPrefs();
  });
  const { bridgeConnected } = useBridge();
  watch(bridgeConnected, (connected) => {
    if (connected && !hydratedFromBackend) {
      void hydrateUiPrefs();
    }
  });
}

export function getUiPrefRefValue(key: string): unknown {
  return memoryPrefs[key];
}

export function hasUiPref(key: string): boolean {
  if (hasOwn(memoryPrefs, key)) return true;
  const local = readLocalStorage(key);
  if (local.found) {
    memoryPrefs[key] = local.value;
    return true;
  }
  return false;
}

export function readUiPref<T>(key: string, fallback: T): T {
  bindLifecycleFlush();
  if (hasOwn(memoryPrefs, key)) {
    return memoryPrefs[key] as T;
  }
  const local = readLocalStorage<T>(key);
  if (local.found) {
    memoryPrefs[key] = local.value;
    return local.value as T;
  }
  return fallback;
}

export async function flushUiPrefs(): Promise<void> {
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  const keys = Object.keys(pendingBackendPatch);
  if (!keys.length) return;
  const payload: Record<string, unknown> = {};
  for (const key of keys) {
    payload[key] = pendingBackendPatch[key];
    delete pendingBackendPatch[key];
  }
  await bridgeSilentPost('ui/prefs/save', { values: payload });
}

function scheduleBackendFlush(): void {
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
  }
  flushTimer = window.setTimeout(() => {
    flushTimer = null;
    void flushUiPrefs();
  }, 180);
}

export function writeUiPref<T>(
  key: string,
  value: T,
  options?: { syncBackend?: boolean; immediate?: boolean },
): void {
  bindLifecycleFlush();
  memoryPrefs[key] = value;
  localDirtyKeys.add(key);
  writeLocalStorage(key, value);

  if (options?.syncBackend === false) return;
  pendingBackendPatch[key] = value;
  if (options?.immediate) {
    void flushUiPrefs();
  } else {
    scheduleBackendFlush();
  }
}

export async function hydrateUiPrefs(): Promise<void> {
  bindLifecycleFlush();
  if (hydratedFromBackend) return;
  if (hydratePromise) return hydratePromise;

  hydratePromise = (async () => {
    const res = await bridgeSilentGet<{ values?: Record<string, unknown> }>('ui/prefs');
    if (res && res.values && typeof res.values === 'object') {
      hydratedFromBackend = true;
      for (const [key, val] of Object.entries(res.values)) {
        if (!localDirtyKeys.has(key)) {
          memoryPrefs[key] = val;
          writeLocalStorage(key, val);
        }
      }
    }
  })().finally(() => {
    hydratePromise = null;
  });

  return hydratePromise;
}
