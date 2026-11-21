import { nextTick, onMounted, onUnmounted, ref, watch, type CSSProperties, type Ref, type WatchSource } from 'vue';

/**
 * Tracks the active tab button inside a segmented bar and returns GPU-friendly
 * transform/width styles for a smoothly gliding Claude-style pill indicator.
 */
export function useSlidingIndicator(
  containerRef: Ref<HTMLElement | null>,
  watchSources: WatchSource<unknown> | WatchSource<unknown>[],
  activeSelector = '.active',
) {
  const left = ref(0);
  const width = ref(0);
  const ready = ref(false);
  const animated = ref(false);
  let observer: ResizeObserver | null = null;
  let rafId = 0;

  function measure(allowAnimation = true): void {
    const container = containerRef.value;
    if (!container) return;
    const activeEl = container.querySelector<HTMLElement>(activeSelector);
    if (!activeEl) {
      ready.value = false;
      return;
    }
    const nextLeft = activeEl.offsetLeft;
    const nextWidth = activeEl.offsetWidth;
    if (nextWidth <= 0) return;
    if (!ready.value) {
      // Snap on initial paint without an awkward slide-in from x=0
      animated.value = false;
      left.value = nextLeft;
      width.value = nextWidth;
      ready.value = true;
      rafId = window.requestAnimationFrame(() => {
        animated.value = true;
      });
    } else {
      animated.value = allowAnimation;
      left.value = nextLeft;
      width.value = nextWidth;
    }
  }

  function scheduleMeasure(allowAnimation = true): void {
    nextTick(() => {
      measure(allowAnimation);
    });
  }

  const sources = Array.isArray(watchSources) ? watchSources : [watchSources];
  watch(sources, () => scheduleMeasure(true), { flush: 'post' });

  watch(
    containerRef,
    (el) => {
      observer?.disconnect();
      observer = null;
      if (!el) {
        ready.value = false;
        animated.value = false;
        return;
      }
      scheduleMeasure(false);
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => measure(false));
        observer.observe(el);
      }
    },
    { immediate: true, flush: 'post' },
  );

  onMounted(() => {
    scheduleMeasure(false);
    if (typeof document !== 'undefined' && 'fonts' in document) {
      (document as Document & { fonts?: { ready?: Promise<unknown> } }).fonts?.ready
        ?.then(() => measure(false))
        .catch(() => {});
    }
  });

  onUnmounted(() => {
    observer?.disconnect();
    if (rafId) window.cancelAnimationFrame(rafId);
  });

  const indicatorStyle = (): CSSProperties => ({
    transform: `translate3d(${left.value}px, 0, 0)`,
    width: `${width.value}px`,
    opacity: ready.value ? 1 : 0,
    transition: animated.value
      ? 'transform 220ms cubic-bezier(0.23, 1, 0.32, 1), width 220ms cubic-bezier(0.23, 1, 0.32, 1), opacity 140ms ease'
      : 'none',
  });

  return {
    indicatorStyle,
    remeasure: () => scheduleMeasure(false),
  };
}
