import { ref, watch, type Ref } from 'vue';
import { getUiPrefRefValue, hasUiPref, readUiPref, writeUiPref } from './useUiPrefs';

export interface PersistedRefOptions {
  /** Whether to also sync this key to plugin backend ui_prefs.json (default: true). */
  syncBackend?: boolean;
}

/**
 * Ref persisted across in-page tab switches, opaque-origin iframe reloads, and
 * standalone browser reloads via the unified UI prefs store.
 */
export function usePersistedRef<T>(
  key: string,
  defaultValue: T,
  options?: PersistedRefOptions,
): Ref<T> {
  const syncBackend = options?.syncBackend !== false;
  const value = ref(readUiPref<T>(key, defaultValue)) as Ref<T>;
  let applyingExternal = false;

  watch(
    () => getUiPrefRefValue(key),
    (remoteVal) => {
      if (!hasUiPref(key) || remoteVal === undefined) return;
      if (Object.is(remoteVal, value.value)) return;
      applyingExternal = true;
      value.value = remoteVal as T;
      applyingExternal = false;
    },
  );

  watch(value, (next) => {
    if (applyingExternal) return;
    writeUiPref(key, next, { syncBackend });
  });

  return value;
}
