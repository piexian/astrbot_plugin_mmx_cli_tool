<script setup lang="ts">
import { ref, onMounted } from 'vue';
import SidebarQuota from './components/SidebarQuota.vue';
import InfiniteCanvas from './components/InfiniteCanvas.vue';
import MediaPlayground from './components/MediaPlayground.vue';
import PluginLogsConsole from './components/PluginLogsConsole.vue';
import type { PluginStatusResponse } from './types/mcode';
import { bridgeGet, bridgePost, pushToast, useBridge } from './composables/useBridge';
import { useCanvas } from './composables/useCanvas';
import { useSlidingIndicator } from './composables/useSlidingIndicator';

const { bridgeConnected, backendOnline, toasts } = useBridge();
const { loadCanvasDocument, totalAnnotationsCount } = useCanvas();

const activeTab = ref<'canvas' | 'playground' | 'logs'>('canvas');
const sidebarOpen = ref(true);
const statusReady = ref(false);

const primaryNavRef = ref<HTMLElement | null>(null);
const { indicatorStyle: navIndicatorStyle } = useSlidingIndicator(primaryNavRef, [
  activeTab,
  totalAnnotationsCount,
]);

const status = ref<PluginStatusResponse>({
  api_version: 1,
  instance_id: 'mcode_studio_preview',
  state: 'ready',
  ready: true,
  region: 'cn',
  auth_mode: 'oauth',
  oauth_connected: true,
  oauth_expires_at: Math.floor(Date.now() / 1000) + 6 * 24 * 3600,
  account_label: 'mcode-public (CN)',
  model: 'MiniMax-M3.1-Flash',
  thinking_effort: 'high',
  capabilities: [
    'image.generate',
    'image.search',
    'image.reverse_search',
    'video.generate',
    'audio.music_generate',
    'audio.sound_effect',
    'quota.query',
  ],
});

function handleStatusUpdated(patch: Partial<PluginStatusResponse>): void {
  status.value = {
    ...status.value,
    ...patch,
  };
}

async function handleEffortChange(e: Event): Promise<void> {
  const nextEffort = (e.target as HTMLSelectElement).value as PluginStatusResponse['thinking_effort'];
  status.value.thinking_effort = nextEffort;
  await bridgePost('config/effort', { thinking_effort: nextEffort }, () => ({ ok: true }));
  pushToast(`已切换 M3.1 思考强度档位：${nextEffort}`, 'ok');
}

onMounted(async () => {
  try {
    const remoteStatus = await bridgeGet<PluginStatusResponse>('status', undefined, () => status.value);
    if (remoteStatus && remoteStatus.model) {
      status.value = remoteStatus;
      if (remoteStatus.provider_source_warning) {
        pushToast(remoteStatus.provider_source_warning, 'warn', 6000);
      }
    }
  } finally {
    statusReady.value = true;
  }
  await loadCanvasDocument();
});
</script>

<template>
  <div class="studio-shell">
    <!-- Mobile backdrop when sidebar drawer is open -->
    <Transition name="modal-fade">
      <div
        v-if="sidebarOpen"
        class="mobile-sidebar-backdrop"
        @click="sidebarOpen = false"
      ></div>
    </Transition>

    <!-- Left Control, Credits & Quota Sidebar -->
    <SidebarQuota
      v-if="sidebarOpen"
      :status="status"
      :status-ready="statusReady"
      @status-updated="handleStatusUpdated"
      @switch-tab="(tab) => (activeTab = tab)"
      @close-sidebar="sidebarOpen = false"
    />

    <!-- Right Main Workspace -->
    <div class="workspace-column">
      <!-- Claude-style Editorial Top Navigation Bar (theme follows host/system) -->
      <header class="workspace-topbar">
        <div class="topbar-left">
          <button
            type="button"
            class="btn-ghost sidebar-toggle-btn"
            :class="{ collapsed: !sidebarOpen }"
            :title="sidebarOpen ? '收起左侧面板' : '展开左侧面板'"
            :aria-label="sidebarOpen ? '收起左侧面板' : '展开左侧面板'"
            @click="sidebarOpen = !sidebarOpen"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <rect x="3" y="3" width="18" height="18" rx="3" />
              <line x1="9" y1="3" x2="9" y2="21" />
            </svg>
          </button>

          <nav ref="primaryNavRef" class="primary-nav sliding-track" aria-label="主工作区导航">
            <span class="sliding-pill" :style="navIndicatorStyle()" aria-hidden="true"></span>
            <button
              type="button"
              class="nav-tab"
              :class="{ active: activeTab === 'canvas' }"
              @click="activeTab = 'canvas'"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <rect x="3" y="3" width="7" height="7" rx="1.5" />
                <rect x="14" y="3" width="7" height="7" rx="1.5" />
                <rect x="3" y="14" width="7" height="7" rx="1.5" />
                <rect x="14" y="14" width="7" height="7" rx="1.5" />
              </svg>
              无限画布
              <span v-if="totalAnnotationsCount" class="nav-count mono-num">{{ totalAnnotationsCount }}</span>
            </button>
            <button
              type="button"
              class="nav-tab"
              :class="{ active: activeTab === 'playground' }"
              @click="activeTab = 'playground'"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M12 3l2.4 5.4L20 10l-4.5 3.8L17 20l-5-3-5 3 1.5-6.2L4 10l5.6-1.6L12 3z" />
              </svg>
              多模态试验台
            </button>
            <button
              type="button"
              class="nav-tab"
              :class="{ active: activeTab === 'logs' }"
              @click="activeTab = 'logs'"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <polyline points="4 17 10 11 4 5" />
                <line x1="12" y1="19" x2="20" y2="19" />
              </svg>
              插件日志
            </button>
          </nav>
        </div>

        <div class="topbar-right">
          <!-- Connection Status Indicator -->
          <span class="pill-badge" :class="backendOnline ? 'ok' : bridgeConnected ? 'accent' : ''">
            <span class="conn-dot" :class="{ live: backendOnline }"></span>
            {{
              backendOnline
                ? '插件已连接'
                : bridgeConnected
                  ? 'Bridge 预览'
                  : '独立预览'
            }}
          </span>

          <!-- Thinking Effort Selector -->
          <label class="effort-control mono-num">
            <span>Thinking</span>
            <select :value="status.thinking_effort" class="effort-select" @change="handleEffortChange">
              <option value="default">default</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
              <option value="max">max</option>
            </select>
          </label>
        </div>
      </header>

      <!-- Active View Stage with Smooth Cross-Fade Transition -->
      <main class="workspace-stage">
        <Transition name="stage-fade" mode="out-in">
          <InfiniteCanvas v-if="activeTab === 'canvas'" key="canvas" />
          <MediaPlayground
            v-else-if="activeTab === 'playground'"
            key="playground"
            @switch-tab="(t) => (activeTab = t)"
          />
          <PluginLogsConsole v-else key="logs" />
        </Transition>
      </main>
    </div>

    <!-- Claude-style Animated Toast Stack -->
    <TransitionGroup name="toast-pop" tag="div" class="toast-stack" aria-live="polite">
      <div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.kind">
        <span class="toast-dot"></span>
        <span>{{ t.text }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.studio-shell {
  width: 100%;
  height: 100%;
  display: flex;
  overflow: hidden;
  background: var(--bg-canvas);
}

.mobile-sidebar-backdrop {
  display: none;
}

.workspace-column {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.workspace-topbar {
  height: 54px;
  padding: 0 18px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.topbar-left,
.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sidebar-toggle-btn {
  padding: 6px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  cursor: pointer;
  transition: background-color 140ms var(--ease-out), color 140ms var(--ease-out), transform 120ms var(--ease-out);
}

.sidebar-toggle-btn:active {
  transform: scale(0.93);
}

.sidebar-toggle-btn.collapsed {
  color: var(--accent);
}

.primary-nav {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
}

.nav-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: color 160ms var(--ease-out), transform 120ms var(--ease-out);
  white-space: nowrap;
}

.nav-tab svg {
  transition: transform 180ms var(--ease-out);
}

.nav-tab:hover {
  color: var(--text-primary);
}

.nav-tab:active {
  transform: scale(0.97);
}

.nav-tab.active {
  color: var(--accent);
  font-weight: 600;
}

.nav-tab.active svg {
  transform: scale(1.06);
}

.nav-count {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  color: #ffffff;
  font-size: 10.5px;
}

.conn-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
}

.conn-dot.live {
  background: var(--status-ok);
}

.effort-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  font-size: 11.5px;
  color: var(--text-secondary);
  transition: border-color 140ms var(--ease-out), background-color 140ms var(--ease-out);
}

.effort-control:hover {
  border-color: var(--border-strong);
}

.effort-select {
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-weight: 600;
  outline: none;
  cursor: pointer;
}

.workspace-stage {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
}

.toast-stack {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 320;
  pointer-events: none;
}

.toast-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-float);
  font-size: 12.5px;
  color: var(--text-primary);
}

.toast-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}

.toast-item.ok .toast-dot {
  background: var(--status-ok);
}

.toast-item.warn .toast-dot {
  background: var(--status-warn);
}

@media (max-width: 768px) {
  .studio-shell {
    position: relative;
  }

  .mobile-sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 210;
    background: var(--bg-backdrop);
    backdrop-filter: blur(2px);
  }

  .workspace-topbar {
    height: auto;
    min-height: 48px;
    flex-wrap: wrap;
    padding: 8px 10px;
    row-gap: 8px;
  }

  .topbar-left {
    flex: 1 1 100%;
    overflow-x: auto;
  }

  .primary-nav {
    overflow-x: auto;
    scrollbar-width: none;
  }

  .primary-nav::-webkit-scrollbar {
    display: none;
  }

  .nav-tab {
    padding: 5px 9px;
    font-size: 12px;
  }

  .topbar-right {
    flex: 1 1 100%;
    justify-content: space-between;
    gap: 6px;
  }

  .effort-control span {
    display: none;
  }
}
</style>
