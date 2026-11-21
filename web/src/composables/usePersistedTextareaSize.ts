import { onMounted, onUnmounted, watch, type Ref } from 'vue';
import { flushUiPrefs, getUiPrefRefValue, hasUiPref, readUiPref, writeUiPref } from './useUiPrefs';

export interface PersistedTextareaOptions {
  defaultHeight: number;
  minHeight: number;
  maxHeight: number;
  /** Max fraction of window.innerHeight allowed so buttons below never overflow the viewport. */
  maxViewportRatio?: number;
}

/**
 * Remembers a vertically resizable <textarea>'s last user-adjusted height across
 * tab switches, AstrBot sandboxed iframe reloads (where localStorage is unavailable),
 * and standalone browser reloads, while clamping to safe bounds so sibling buttons
 * and container content never overflow.
 */
export function usePersistedTextareaSize(
  elRef: Ref<HTMLTextAreaElement | null>,
  storageKey: string,
  options: PersistedTextareaOptions,
): void {
  let observer: ResizeObserver | null = null;
  let applyingSaved = false;
  let lastAppliedHeight = 0;
  let activeEl: HTMLTextAreaElement | null = null;
  let pointerResizing = false;

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
    const raw = readUiPref<unknown>(storageKey, options.defaultHeight);
    const parsed = typeof raw === 'number' ? raw : Number(raw);
    if (Number.isFinite(parsed) && parsed > 0) {
      return clampHeight(parsed);
    }
    return clampHeight(options.defaultHeight);
  }

  function applyHeight(el: HTMLTextAreaElement, height: number): void {
    const clamped = clampHeight(height);
    lastAppliedHeight = clamped;
    applyingSaved = true;
    el.style.height = `${clamped}px`;
    // Skip initial ResizeObserver frame(s) triggered by our programmatic write
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        applyingSaved = false;
      });
    });
  }

  function captureResize(el: HTMLTextAreaElement, immediateFlush = false): void {
    if (applyingSaved || !el.isConnected) return;
    const offsetH = el.offsetHeight;
    if (offsetH <= 0) return;

    // Native browser corner-drag resize updates inline `el.style.height` in exact CSS px.
    // Reading inline height avoids 1px HiDPI rounding creep and prevents mount/transition/
    // flex layout fluctuations from clobbering the user's remembered height.
    const inlineRaw = el.style.height ? Number.parseFloat(el.style.height) : NaN;
    if (!Number.isFinite(inlineRaw) || inlineRaw <= 0) return;

    const clamped = clampHeight(inlineRaw);
    const outOfBounds = inlineRaw > computeUpperBound() + 0.5 || inlineRaw < options.minHeight - 0.5;

    if (outOfBounds) {
      applyHeight(el, clamped);
    }
    if (Math.abs(clamped - lastAppliedHeight) >= 1) {
      lastAppliedHeight = clamped;
      writeUiPref(storageKey, clamped, { immediate: immediateFlush });
    } else if (immediateFlush) {
      void flushUiPrefs();
    }
  }

  function handlePointerDown(): void {
    pointerResizing = true;
  }

  function handleWindowPointerUp(): void {
    if (!pointerResizing) return;
    pointerResizing = false;
    if (activeEl) {
      captureResize(activeEl, true);
    }
  }

  function handleWindowResize(): void {
    const el = elRef.value;
    if (!el || !el.isConnected) return;
    const upper = computeUpperBound();
    const current = el.offsetHeight;
    if (current > upper) {
      applyHeight(el, upper);
      writeUiPref(storageKey, upper);
    } else if (hasUiPref(storageKey)) {
      const target = readSavedHeight();
      if (Math.abs(target - lastAppliedHeight) >= 1) {
        applyHeight(el, target);
      }
    }
  }

  function detachEl(): void {
    if (activeEl) {
      activeEl.removeEventListener('pointerdown', handlePointerDown);
      activeEl.removeEventListener('mousedown', handlePointerDown);
      activeEl = null;
    }
    observer?.disconnect();
    observer = null;
  }

  // React to async backend hydration (e.g., GET ui/prefs arriving after mount)
  watch(
    () => getUiPrefRefValue(storageKey),
    (remoteVal) => {
      const el = elRef.value;
      if (!el || !el.isConnected || remoteVal === undefined) return;
      const parsed = typeof remoteVal === 'number' ? remoteVal : Number(remoteVal);
      if (!Number.isFinite(parsed) || parsed <= 0) return;
      const clamped = clampHeight(parsed);
      if (Math.abs(clamped - lastAppliedHeight) >= 1) {
        applyHeight(el, clamped);
      }
    },
  );

  watch(
    elRef,
    (el) => {
      detachEl();
      if (!el) return;
      activeEl = el;
      el.addEventListener('pointerdown', handlePointerDown);
      el.addEventListener('mousedown', handlePointerDown);
      applyHeight(el, readSavedHeight());
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => {
          captureResize(el, false);
        });
        observer.observe(el);
      }
    },
    { immediate: true, flush: 'post' },
  );

  onMounted(() => {
    window.addEventListener('resize', handleWindowResize);
    window.addEventListener('pointerup', handleWindowPointerUp);
    window.addEventListener('mouseup', handleWindowPointerUp);
  });

  onUnmounted(() => {
    if (activeEl && pointerResizing) {
      captureResize(activeEl, true);
    }
    pointerResizing = false;
    detachEl();
    window.removeEventListener('resize', handleWindowResize);
    window.removeEventListener('pointerup', handleWindowPointerUp);
    window.removeEventListener('mouseup', handleWindowPointerUp);
  });
}
