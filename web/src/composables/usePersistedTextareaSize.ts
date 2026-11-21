import { onMounted, onUnmounted, watch, type Ref } from 'vue';

export interface PersistedTextareaOptions {
  defaultHeight: number;
  minHeight: number;
  maxHeight: number;
  /** Max fraction of window.innerHeight allowed so buttons below never overflow the viewport. */
  maxViewportRatio?: number;
}

/**
 * Remembers a vertically resizable <textarea>'s last user-adjusted height in localStorage
 * and clamps it to safe bounds so sibling buttons and container content never overflow.
 */
export function usePersistedTextareaSize(
  elRef: Ref<HTMLTextAreaElement | null>,
  storageKey: string,
  options: PersistedTextareaOptions,
): void {
  let observer: ResizeObserver | null = null;
  let applyingSaved = false;

  function computeUpperBound(): number {
    const ratio = options.maxViewportRatio ?? 0.44;
    const viewportCap =
      typeof window !== 'undefined' && window.innerHeight > 0
        ? Math.floor(window.innerHeight * ratio)
        : options.maxHeight;
    return Math.max(options.minHeight, Math.min(options.maxHeight, viewportCap));
  }

  function clampHeight(raw: number): number {
    return Math.round(Math.max(options.minHeight, Math.min(computeUpperBound(), raw)));
  }

  function readSavedHeight(): number {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw !== null) {
        const parsed = Number(JSON.parse(raw));
        if (Number.isFinite(parsed) && parsed > 0) {
          return clampHeight(parsed);
        }
      }
    } catch {
      // ignore storage errors
    }
    return clampHeight(options.defaultHeight);
  }

  function writeSavedHeight(height: number): void {
    try {
      localStorage.setItem(storageKey, JSON.stringify(height));
    } catch {
      // ignore storage quota errors
    }
  }

  function applyHeight(el: HTMLTextAreaElement, height: number): void {
    applyingSaved = true;
    el.style.height = `${clampHeight(height)}px`;
    window.requestAnimationFrame(() => {
      applyingSaved = false;
    });
  }

  function handleWindowResize(): void {
    const el = elRef.value;
    if (!el) return;
    const current = el.offsetHeight;
    const upper = computeUpperBound();
    if (current > upper) {
      applyHeight(el, upper);
      writeSavedHeight(upper);
    }
  }

  watch(
    elRef,
    (el) => {
      observer?.disconnect();
      observer = null;
      if (!el) return;
      applyHeight(el, readSavedHeight());
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => {
          if (applyingSaved) return;
          const next = el.offsetHeight;
          if (next <= 0) return;
          const clamped = clampHeight(next);
          if (clamped !== next) {
            applyHeight(el, clamped);
          }
          writeSavedHeight(clamped);
        });
        observer.observe(el);
      }
    },
    { immediate: true, flush: 'post' },
  );

  onMounted(() => {
    window.addEventListener('resize', handleWindowResize);
  });

  onUnmounted(() => {
    observer?.disconnect();
    window.removeEventListener('resize', handleWindowResize);
  });
}
