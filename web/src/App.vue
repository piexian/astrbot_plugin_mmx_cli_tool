<script setup lang="ts">
import { ref, onMounted } from 'vue';
import SidebarQuota from './components/SidebarQuota.vue';
import InfiniteCanvas from './components/InfiniteCanvas.vue';
import MediaPlayground from './components/MediaPlayground.vue';
import PlanAndTerminal from './components/PlanAndTerminal.vue';
import type { PluginStatusResponse } from './types/mcode';
import { bridgeGet, bridgePost, pushToast, useBridge } from './composables/useBridge';
import { useCanvas } from './composables/useCanvas';

const { isDark, bridgeConnected, backendOnline, toasts, toggleThemeMode } = useBridge();
const { loadCanvasDocument, totalAnnotationsCount } = useCanvas();

const activeTab = ref<'canvas' | 'playground' | 'plan'>('canvas');
const sidebarOpen = ref(true);

const status = ref<PluginStatusResponse>({
  api_version: 1,
  instance_id: 'mcode_studio_v070',
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
  const remoteStatus = await bridgeGet<PluginStatusResponse>('status', undefined, () => status.value);
  if (remoteStatus && remoteStatus.model) {
    status.value = remoteStatus;
    if (remoteStatus.provider_source_warning) {
      pushToast(remoteStatus.provider_source_warning, 'warn', 6000);
    }
  }
  await loadCanvasDocument();
});
</script>

<template>
  <div class="studio-shell">
    <!-- Left Control & Quota Sidebar -->
    <SidebarQuota
      v-if="sidebarOpen"
      :status="status"
      @status-updated="handleStatusUpdated"
      @switch-tab="(tab) => (activeTab = tab)"
    />

    <!-- Right Main Workspace -->
    <div class="workspace-column">
      <!-- Top Navigation Header -->
      <header class="workspace-topbar">
        <div class="topbar-left">
          <button
            type="button"
            class="btn-ghost sidebar-toggle-btn"
            :title="sidebarOpen ? '收起左侧面板' : '展开左侧面板'"
            @click="sidebarOpen = !sidebarOpen"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <line x1="9" y1="3" x2="9" y2="21" />
            </svg>
          </button>

          <nav class="primary-nav" aria-label="主工作区导航">
            <button
              type="button"
              class="nav-tab"
              :class="{ active: activeTab === 'canvas' }"
              @click="activeTab = 'canvas'"
            >
              无限画布
              <span v-if="totalAnnotationsCount" class="nav-count mono-num">{{ totalAnnotationsCount }}</span>
            </button>
            <button
              type="button"
              class="nav-tab"
              :class="{ active: activeTab === 'playground' }"
              @click="activeTab = 'playground'"
            >
              多模态试验台
            </button>
            <button
              type="button"
              class="nav-tab"
              :class="{ active: activeTab === 'plan' }"
              @click="activeTab = 'plan'"
            >
              Team Plan 与实时终端
            </button>
          </nav>
        </div>

        <div class="topbar-right">
          <!-- Connection Status Indicator -->
          <span class="pill-badge" :class="backendOnline ? 'ok' : bridgeConnected ? 'accent' : ''">
            <span class="conn-dot" :class="{ live: backendOnline }"></span>
            {{
              backendOnline
                ? '已连接插件后端'
                : bridgeConnected
                  ? 'Bridge 就绪 (预览态)'
                  : '独立预览模式'
            }}
          </span>

          <!-- Thinking Effort Selector -->
          <label class="effort-control mono-num">
            <span>Thinking:</span>
            <select :value="status.thinking_effort" class="effort-select" @change="handleEffortChange">
              <option value="default">default</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
              <option value="max">max</option>
            </select>
          </label>

          <!-- Theme Switcher -->
          <button
            type="button"
            class="btn"
            :title="isDark ? '切换至羊皮纸浅色主题' : '切换至黑曜石深色主题'"
            @click="toggleThemeMode"
          >
            {{ isDark ? '浅色模式' : '深色模式' }}
          </button>
        </div>
      </header>

      <!-- Active View Stage -->
      <main class="workspace-stage">
        <InfiniteCanvas v-if="activeTab === 'canvas'" />
        <MediaPlayground v-else-if="activeTab === 'playground'" @switch-tab="(t) => (activeTab = t)" />
        <PlanAndTerminal v-else />
      </main>
    </div>

    <!-- Claude-style Toast Stack -->
    <div class="toast-stack" aria-live="polite">
      <div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.kind">
        <span class="toast-dot"></span>
        <span>{{ t.text }}</span>
      </div>
    </div>
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

.workspace-column {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.workspace-topbar {
  height: 52px;
  padding: 0 16px;
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
}

@media (max-width: 768px) {
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
    font-size: 12.5px;
    white-space: nowrap;
  }

  .topbar-right {
    flex: 1 1 100%;
    justify-content: space-between;
    gap: 6px;
  }

  .effort-control {
    gap: 4px;
  }

  .effort-control span {
    display: none;
  }

  .pill-badge {
    font-size: 11px;
  }
}

.primary-nav {
  display: flex;
  align-items: center;
  gap: 4px;
}

.nav-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.14s ease, color 0.14s ease, border-color 0.14s ease;
}

.nav-tab:hover {
  background: var(--bg-surface-muted);
  color: var(--text-primary);
}

.nav-tab.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: var(--accent-border);
  font-weight: 600;
}

.nav-count {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  color: #ffffff;
  font-size: 11px;
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
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  font-size: 11.5px;
  color: var(--text-secondary);
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
  z-index: 300;
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
}

.toast-item.ok .toast-dot {
  background: var(--status-ok);
}

.toast-item.warn .toast-dot {
  background: var(--status-warn);
}
</style>
