import { ref, watch, type Ref } from 'vue';

/** Ref persisted to localStorage so its value survives component remounts and page reloads. */
export function usePersistedRef<T>(key: string, defaultValue: T): Ref<T> {
  let initial = defaultValue;
  try {
    const raw = localStorage.getItem(key);
    if (raw !== null) initial = JSON.parse(raw) as T;
  } catch {
    // storage unavailable or corrupted — keep the default
  }
  const value = ref(initial) as Ref<T>;
  watch(value, (v) => {
    try {
      localStorage.setItem(key, JSON.stringify(v));
    } catch {
      // ignore write failures (quota / private mode)
    }
  });
  return value;
}
