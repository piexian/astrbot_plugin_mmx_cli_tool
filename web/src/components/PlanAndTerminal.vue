<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue';
import type { TeamPlanState, TerminalLogEntry } from '../types/mcode';
import { bridgeGet, bridgePost, pushToast, useBridge } from '../composables/useBridge';

const { backendOnline, subscribeSSE, unsubscribeSSE } = useBridge();

const plan = ref<TeamPlanState | null>(null);

// Sample entries for the offline preview only; never mixed into the live SSE list.
const demoLogs = ref<TerminalLogEntry[]>([
  {
    id: 'log_1',
    ts_ms: Date.now() - 180000,
    source: 'oauth',
    level: 'ok',
    command: 'POST https://account.minimax.cn/oauth2/device/code',
    detail: 'client_id=mcode-public scope=agent.default code_challenge_method=S256 -> 200 OK',
    latency_ms: 118,
  },
  {
    id: 'log_2',
    ts_ms: Date.now() - 125000,
    source: 'matrix_mcp',
    level: 'ok',
    command: 'POST /mavis/api/v1/mcp/images_search_and_download',
    detail: 'queries=[{"query":"建筑极简主义 暖光材质"}] -> 3 images cached to shared temp dir',
    latency_ms: 642,
  },
  {
    id: 'log_3',
    ts_ms: Date.now() - 64000,
    source: 'canvas',
    level: 'info',
    command: 'CanvasService.apply(op_initial_seed)',
    detail: 'CanvasDocumentV1 changeSeq=3 · 3 file_ref nodes · 1 normalizedRect annotation',
    latency_ms: 14,
  },
]);

const liveLogs = ref<TerminalLogEntry[]>([]);
const logs = computed(() => (backendOnline.value ? liveLogs.value : demoLogs.value));

const logSourceFilter = ref<string>('all');
let sseSubId: string | null = null;

function formatTime(ms: number): string {
  const d = new Date(ms);
  return d.toTimeString().slice(0, 8);
}

async function handlePlanDecision(decision: 'approve' | 'reject'): Promise<void> {
  if (!plan.value) return;
  const res = await bridgePost<{ status: TeamPlanState['status'] }>(
    'plans/approve',
    {
      plan_id: plan.value.plan_id,
      decision,
    },
    () => ({
      status: decision === 'approve' ? 'executing' : 'review_required',
    }),
  );
  plan.value.status = res.status;
  if (decision === 'approve') {
    plan.value.tasks = plan.value.tasks.map((t) =>
      t.status === 'pending' ? { ...t, status: 'in_progress' } : t,
    );
    appendLog(demoLogs, {
      source: 'plan',
      level: 'ok',
      command: `TeamPlan.approve(${plan.value.plan_id})`,
      detail: 'Plan approved by operator -> dispatched pending tasks to active workers',
      latency_ms: 22,
    });
    pushToast('已批准当前 M-Plan 方案并分发子任务（演示）', 'ok');
  } else {
    pushToast('已标记方案需重新修订（演示）', 'info');
  }
}

function appendLog(target: Ref<TerminalLogEntry[]>, entry: Omit<TerminalLogEntry, 'id' | 'ts_ms'>): void {
  target.value = [
    {
      id: `log_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      ts_ms: Date.now(),
      ...entry,
    },
    ...target.value.slice(0, 79),
  ];
}

function clearLogs(): void {
  (backendOnline.value ? liveLogs : demoLogs).value = [];
}

function simulateLiveEvent(): void {
  const samples: Array<Omit<TerminalLogEntry, 'id' | 'ts_ms'>> = [
    {
      source: 'matrix_mcp',
      level: 'ok',
      command: 'POST /mavis/api/v1/mcp/batch_text_to_music',
      detail: 'output_file=slot_0.mp3 -> synthesized & downloaded to AstrBot shared temp dir',
      latency_ms: 1840,
    },
    {
      source: 'llm',
      level: 'info',
      command: 'POST /mavis/api/v1/llm/v1/messages (MiniMax-M3.1-Flash)',
      detail: 'thinking.effort=high input_tokens=4,128 output_tokens=962 stop_reason=end_turn',
      latency_ms: 920,
    },
    {
      source: 'canvas',
      level: 'ok',
      command: 'POST /canvas/iterate (node_poster_hero #1)',
      detail: 'Dispatched NormalizedRect(0.64, 0.16, 0.28, 0.42) to VLM & Image Synthesizer',
      latency_ms: 48,
    },
  ];
  const pick = samples[demoLogs.value.length % samples.length];
  appendLog(demoLogs, pick);
}

onMounted(async () => {
  const remotePlan = await bridgeGet<TeamPlanState>('plans/current', undefined, () => null);
  if (remotePlan && remotePlan.plan_id) {
    plan.value = remotePlan;
  }

  try {
    const id = await subscribeSSE?.('events', {
      onMessage(evt) {
        if (evt.parsed && typeof evt.parsed === 'object' && evt.parsed.command) {
          appendLog(liveLogs, {
            source: evt.parsed.source || 'matrix_mcp',
            level: evt.parsed.level || 'info',
            command: evt.parsed.command,
            detail: evt.parsed.detail || '',
            latency_ms: evt.parsed.latency_ms,
          });
        }
      },
    });
    if (id) sseSubId = id;
  } catch {
    // Fallback mode when SSE endpoint is not yet mounted
  }
});

onUnmounted(() => {
  if (sseSubId) {
    unsubscribeSSE?.(sseSubId).catch(() => {});
  }
});
</script>

<template>
  <div class="plan-root">
    <!-- Top Section: Team Plan Review & Task Swimlanes -->
    <section class="plan-section">
      <div class="plan-review-card">
        <template v-if="plan">
        <div class="plan-top-bar">
          <div>
            <div class="plan-eyebrow-row">
              <div class="plan-eyebrow mono-num">TEAM PLAN · {{ plan.plan_id }}</div>
            </div>
            <h2 class="serif-heading plan-heading">{{ plan.title }}</h2>
          </div>
          <div class="plan-actions">
            <span
              class="pill-badge"
              :class="plan.status === 'executing' || plan.status === 'completed' ? 'ok' : 'accent'"
            >
              {{
                plan.status === 'review_required'
                  ? '待审批'
                  : plan.status === 'executing'
                    ? '执行中'
                    : '已完成'
              }}
            </span>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="plan.status === 'executing' || !plan.tasks.length"
              @click="handlePlanDecision('approve')"
            >
              批准并分发子任务
            </button>
            <button type="button" class="btn" :disabled="!plan.tasks.length" @click="handlePlanDecision('reject')">
              要求修订
            </button>
          </div>
        </div>

        <p class="plan-overview">{{ plan.overview }}</p>

        <!-- Task Cards Grid -->
        <div class="task-grid">
          <div v-for="task in plan.tasks" :key="task.id" class="task-card">
            <div class="task-head">
              <span class="task-id mono-num">{{ task.id }}</span>
              <span
                class="pill-badge"
                :class="
                  task.status === 'completed'
                    ? 'ok'
                    : task.status === 'in_progress'
                      ? 'accent'
                      : ''
                "
              >
                {{
                  task.status === 'completed'
                    ? '已完成'
                    : task.status === 'in_progress'
                      ? '进行中'
                      : '待执行'
                }}
              </span>
            </div>
            <div class="task-title">{{ task.title }}</div>
            <p class="task-summary">{{ task.summary }}</p>
            <div class="task-files">
              <span v-for="f in task.target_files" :key="f" class="file-chip mono-num">{{ f }}</span>
            </div>
          </div>
        </div>
        </template>
        <div v-else class="plan-empty">
          <div class="plan-empty-title">暂无团队方案</div>
          <p class="plan-empty-desc">
            当前没有待审批的团队方案：批准分发与修订操作需存在带任务清单的方案后才可用。
          </p>
        </div>
      </div>
    </section>

    <!-- Bottom Section: Live SSE Terminal Stream (log-viewer replica) -->
    <section class="terminal-section">
      <div class="terminal-header">
        <div class="term-title-group">
          <span class="term-dot"></span>
          <span class="term-title mono-num">MCode Runtime Event Stream (SSE /events)</span>
        </div>
        <div class="term-controls">
          <select v-model="logSourceFilter" class="term-select mono-num">
            <option value="all">全部来源</option>
            <option value="matrix_mcp">Matrix MCP</option>
            <option value="oauth">OAuth 2.0</option>
            <option value="canvas">Canvas</option>
            <option value="plan">Team Plan</option>
            <option value="llm">LLM M3.1</option>
          </select>
          <button v-if="!backendOnline" type="button" class="term-btn" @click="simulateLiveEvent">
            + 模拟实时事件
          </button>
          <button type="button" class="term-btn" @click="clearLogs">清空</button>
        </div>
      </div>

      <div class="terminal-body mono-num">
        <div
          v-for="item in logs.filter((l) => logSourceFilter === 'all' || l.source === logSourceFilter)"
          :key="item.id"
          class="term-row"
        >
          <span class="term-ts">{{ formatTime(item.ts_ms) }}</span>
          <span class="term-source" :class="item.source">[{{ item.source }}]</span>
          <span class="term-cmd">{{ item.command }}</span>
          <span v-if="item.latency_ms !== undefined" class="term-latency">{{ item.latency_ms }}ms</span>
          <div class="term-detail">{{ item.detail }}</div>
        </div>
        <div v-if="!logs.length" class="term-empty">
          {{
            backendOnline
              ? '暂无运行事件：在工作台发起搜图、画布分析等操作后，将在此实时显示'
              : '离线预览：暂无日志，可点击「+ 模拟实时事件」生成示例'
          }}
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.plan-root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 18px 22px;
  gap: 16px;
  overflow-y: auto;
}

.plan-review-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 18px 20px;
  box-shadow: var(--shadow-card);
}

.plan-top-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.plan-eyebrow-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.plan-eyebrow {
  font-size: 11px;
  color: var(--accent);
  font-weight: 600;
}

.plan-heading {
  margin: 4px 0 0;
  font-size: 18px;
}

.plan-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.plan-empty {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 26px 8px;
  text-align: center;
}

.plan-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
}

.plan-empty-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.plan-overview {
  margin: 10px 0 16px;
  color: var(--text-secondary);
  font-size: 13px;
}

.task-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 12px;
}

.task-card {
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.task-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.task-id {
  font-size: 11px;
  color: var(--text-tertiary);
}

.task-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.task-summary {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.45;
}

.task-files {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: auto;
  padding-top: 4px;
}

.file-chip {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  color: var(--text-secondary);
}

.terminal-section {
  flex: 1;
  min-height: 220px;
  background: var(--bg-terminal);
  color: #e5e4dd;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-strong);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.terminal-header {
  height: 38px;
  padding: 0 14px;
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.term-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.term-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #46a775;
}

.term-title {
  font-size: 12px;
  color: #d2d0c6;
}

.term-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.term-select,
.term-btn {
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.06);
  color: #ecebe6;
  font-size: 11.5px;
  cursor: pointer;
}
.term-select option {
  background: #24231f;
  color: #ecebe6;
}


.terminal-body {
  flex: 1;
  padding: 12px 14px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}

.term-row {
  padding-bottom: 6px;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.07);
}

.term-ts {
  color: #8e8c84;
  margin-right: 8px;
}

.term-source {
  font-weight: 600;
  margin-right: 8px;
  color: #e06d3e;
}

.term-source.oauth {
  color: #46a775;
}

.term-source.canvas {
  color: #e09432;
}

.term-cmd {
  color: #ffffff;
  font-weight: 500;
}

.term-latency {
  float: right;
  color: #46a775;
  font-size: 11px;
}

.term-detail {
  color: #a8a69d;
  font-size: 11.5px;
  margin-top: 2px;
  padding-left: 14px;
}

.term-empty {
  color: #8e8c84;
  font-size: 12px;
}
</style>
