<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import type { AccountCreditDetailItem, AccountUsageResponse } from '../types/mcode';

const props = defineProps<{
  open: boolean;
  usage: AccountUsageResponse | null;
  loading: boolean;
  error?: string;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'refresh'): void;
}>();

type CategoryFilter = 'all' | 'active' | 'base' | 'bonus' | 'expiring';
type SortMode = 'expiry' | 'remaining' | 'original';

const activeFilter = ref<CategoryFilter>('all');
const sortMode = ref<SortMode>('expiry');

const EXPIRING_SOON_MS = 7 * 24 * 3600 * 1000;

interface NormalizedCreditBatch {
  idx: number;
  raw: AccountCreditDetailItem;
  category: 'base' | 'bonus';
  categoryLabel: string;
  customLabel: string;
  granted: number | null;
  remaining: number;
  consumed: number | null;
  ratio: number | null;
  grantedAtMs: number | null;
  expireAtMs: number | null;
  isExpired: boolean;
  isExhausted: boolean;
  isExpiringSoon: boolean;
  daysLeft: number | null;
}

function parseNum(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(num) ? num : null;
}

function formatNumber(value: string | number | null | undefined): string {
  const num = parseNum(value);
  if (num === null) return value ? String(value) : '—';
  const hasDecimal = Math.abs(num - Math.trunc(num)) > 1e-6;
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: hasDecimal ? 2 : 0,
  }).format(num);
}

function formatDateTime(ms: number | null): string {
  if (!ms || !Number.isFinite(ms) || ms <= 0) return '—';
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${day} ${hh}:${mm}`;
}

function formatDateOnly(ms: number | null): string {
  if (!ms || !Number.isFinite(ms) || ms <= 0) return '—';
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

const batches = computed<NormalizedCreditBatch[]>(() => {
  const list = props.usage?.credit_details || [];
  const now = Date.now();
  return list.map((raw, idx) => {
    const isBase =
      raw.category === 'base' || raw.credit_type === 1 || String(raw.credit_type) === '1';
    const remaining = parseNum(raw.remaining_amount) ?? 0;
    const granted = parseNum(raw.granted_amount);
    const explicitConsumed = parseNum(raw.consumed_amount);
    const consumed =
      explicitConsumed !== null
        ? explicitConsumed
        : granted !== null && granted >= remaining
          ? Math.max(0, granted - remaining)
          : null;
    const ratio =
      granted !== null && granted > 0
        ? Math.max(0, Math.min(100, (remaining / granted) * 100))
        : null;
    const expireAtMs =
      typeof raw.expire_at_ms === 'number' && raw.expire_at_ms > 0 ? raw.expire_at_ms : null;
    const grantedAtMs =
      typeof raw.granted_at_ms === 'number' && raw.granted_at_ms > 0 ? raw.granted_at_ms : null;
    const diffMs = expireAtMs !== null ? expireAtMs - now : null;
    const isExpired = diffMs !== null && diffMs <= 0;
    const isExhausted = remaining <= 0;
    const isExpiringSoon = !isExpired && !isExhausted && diffMs !== null && diffMs <= EXPIRING_SOON_MS;
    const daysLeft =
      diffMs !== null && diffMs > 0 ? Math.max(0, Math.ceil(diffMs / (24 * 3600 * 1000))) : null;

    return {
      idx,
      raw,
      category: isBase ? 'base' : 'bonus',
      categoryLabel: isBase ? '订阅积分' : '赠送积分',
      customLabel: raw.label?.trim() || '',
      granted,
      remaining,
      consumed,
      ratio,
      grantedAtMs,
      expireAtMs,
      isExpired,
      isExhausted,
      isExpiringSoon,
      daysLeft,
    };
  });
});

const counts = computed(() => {
  let active = 0;
  let base = 0;
  let bonus = 0;
  let expiring = 0;
  for (const item of batches.value) {
    if (!item.isExhausted && !item.isExpired) active += 1;
    if (item.category === 'base') base += 1;
    else bonus += 1;
    if (item.isExpiringSoon) expiring += 1;
  }
  return {
    all: batches.value.length,
    active,
    base,
    bonus,
    expiring,
  };
});

const totalRemainingDisplay = computed(() => {
  if (!props.usage) return '—';
  if (props.usage.credit_details.length > 0 && props.usage.credit_total_remaining) {
    return formatNumber(props.usage.credit_total_remaining);
  }
  return formatNumber(props.usage.credit_balance);
});

const filteredBatches = computed<NormalizedCreditBatch[]>(() => {
  const list = batches.value.filter((item) => {
    if (activeFilter.value === 'active') return !item.isExhausted && !item.isExpired;
    if (activeFilter.value === 'base') return item.category === 'base';
    if (activeFilter.value === 'bonus') return item.category === 'bonus';
    if (activeFilter.value === 'expiring') return item.isExpiringSoon;
    return true;
  });

  if (sortMode.value === 'original') return list;

  return [...list].sort((a, b) => {
    if (sortMode.value === 'remaining') {
      return b.remaining - a.remaining || a.idx - b.idx;
    }
    // 即将到期优先：有效余额优先于已用完/已过期，再按到期时间升序
    const aInactive = a.isExhausted || a.isExpired ? 1 : 0;
    const bInactive = b.isExhausted || b.isExpired ? 1 : 0;
    if (aInactive !== bInactive) return aInactive - bInactive;
    const aExp = a.expireAtMs ?? Number.MAX_SAFE_INTEGER;
    const bExp = b.expireAtMs ?? Number.MAX_SAFE_INTEGER;
    return aExp - bExp || b.remaining - a.remaining;
  });
});

function statusBadge(item: NormalizedCreditBatch): { text: string; tone: string } {
  if (item.isExpired) return { text: '已过期', tone: 'danger' };
  if (item.isExhausted) return { text: '已用完', tone: '' };
  if (item.isExpiringSoon) {
    return {
      text: item.daysLeft !== null && item.daysLeft <= 1 ? '24h 内到期' : `${item.daysLeft} 天后到期`,
      tone: 'warn',
    };
  }
  return { text: '可用', tone: 'ok' };
}

function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && props.open) {
    emit('close');
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && activeFilter.value === 'expiring' && counts.value.expiring === 0) {
      activeFilter.value = 'all';
    }
  },
);

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div
        v-if="open"
        class="credit-modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="credit-modal-title"
        @click="emit('close')"
      >
        <div class="credit-modal-dialog" @click.stop>
          <!-- Modal Header -->
          <header class="credit-modal-header">
            <div class="header-main">
              <div class="title-line">
                <h2 id="credit-modal-title" class="serif-heading modal-title">全部积分明细</h2>
                <span v-if="usage?.account?.region" class="pill-badge mono-num">
                  {{ usage.account.region === 'global' ? '国际站 · GLOBAL' : '国内站 · CN' }}
                </span>
              </div>
              <p v-if="usage?.account" class="modal-subtitle">
                {{ usage.account.name || 'MiniMax 账号' }}
                <template v-if="usage.account.email"> · {{ usage.account.email }}</template>
              </p>
            </div>
            <div class="header-actions">
              <button
                type="button"
                class="btn"
                :disabled="loading"
                @click="emit('refresh')"
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  :class="{ 'is-spinning': loading }"
                >
                  <path d="M21 2v6h-6" />
                  <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                  <path d="M3 22v-6h6" />
                  <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                </svg>
                {{ loading ? '刷新中' : '刷新' }}
              </button>
              <button
                type="button"
                class="btn-ghost close-btn"
                title="关闭弹窗 (Esc)"
                aria-label="关闭全部积分弹窗"
                @click="emit('close')"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </header>

          <!-- KPI Summary Strip -->
          <section v-if="usage" class="kpi-strip">
            <div class="kpi-card primary">
              <div class="kpi-label">可用总积分</div>
              <div class="kpi-value mono-num">
                {{ totalRemainingDisplay }}
                <span class="kpi-unit">PTS</span>
              </div>
              <div class="kpi-foot mono-num">共 {{ counts.all }} 笔 · {{ counts.active }} 笔可用</div>
            </div>

            <div class="kpi-card">
              <div class="kpi-label">订阅积分</div>
              <div class="kpi-value mono-num">
                {{ usage.credit_split ? formatNumber(usage.credit_split.base) : '—' }}
                <span class="kpi-unit">PTS</span>
              </div>
              <div class="kpi-foot">{{ counts.base }} 笔记录</div>
            </div>

            <div class="kpi-card">
              <div class="kpi-label">赠送 / 签到积分</div>
              <div class="kpi-value mono-num">
                {{ usage.credit_split ? formatNumber(usage.credit_split.bonus) : '—' }}
                <span class="kpi-unit">PTS</span>
              </div>
              <div class="kpi-foot">{{ counts.bonus }} 笔记录</div>
            </div>

            <div class="kpi-card">
              <div class="kpi-label">积分兜底</div>
              <div class="kpi-status-row">
                <span
                  class="pill-badge"
                  :class="
                    usage.fallback_enabled === true
                      ? 'ok'
                      : usage.fallback_enabled === false
                        ? ''
                        : 'info'
                  "
                >
                  {{
                    usage.fallback_enabled === true
                      ? '已开启'
                      : usage.fallback_enabled === false
                        ? '未开启'
                        : '未知'
                  }}
                </span>
              </div>
              <div class="kpi-foot">套餐用尽后自动抵扣</div>
            </div>
          </section>

          <!-- Filter & Sort Bar -->
          <div class="modal-toolbar">
            <div class="filter-pills" role="tablist" aria-label="积分筛选">
              <button
                type="button"
                class="filter-chip"
                :class="{ active: activeFilter === 'all' }"
                @click="activeFilter = 'all'"
              >
                全部 <span class="chip-count mono-num">{{ counts.all }}</span>
              </button>
              <button
                type="button"
                class="filter-chip"
                :class="{ active: activeFilter === 'active' }"
                @click="activeFilter = 'active'"
              >
                有余量 <span class="chip-count mono-num">{{ counts.active }}</span>
              </button>
              <button
                type="button"
                class="filter-chip"
                :class="{ active: activeFilter === 'base' }"
                @click="activeFilter = 'base'"
              >
                订阅积分 <span class="chip-count mono-num">{{ counts.base }}</span>
              </button>
              <button
                type="button"
                class="filter-chip"
                :class="{ active: activeFilter === 'bonus' }"
                @click="activeFilter = 'bonus'"
              >
                赠送积分 <span class="chip-count mono-num">{{ counts.bonus }}</span>
              </button>
              <button
                v-if="counts.expiring > 0"
                type="button"
                class="filter-chip warn-chip"
                :class="{ active: activeFilter === 'expiring' }"
                @click="activeFilter = 'expiring'"
              >
                即将到期 <span class="chip-count mono-num">{{ counts.expiring }}</span>
              </button>
            </div>

            <label class="sort-control">
              <span>排序</span>
              <select v-model="sortMode" class="sort-select">
                <option value="expiry">到期时间优先</option>
                <option value="remaining">剩余积分从高到低</option>
                <option value="original">接口返回顺序</option>
              </select>
            </label>
          </div>

          <!-- Body: Complete Credit Batch List -->
          <div class="modal-body">
            <div v-if="error" class="modal-error-banner">
              <span>{{ error }}</span>
              <button type="button" class="btn" @click="emit('refresh')">重试</button>
            </div>

            <div v-if="!usage && loading" class="modal-empty">正在拉取全部积分明细…</div>

            <div v-else-if="filteredBatches.length" class="batch-list">
              <article
                v-for="item in filteredBatches"
                :key="`${item.idx}-${item.expireAtMs ?? 'none'}`"
                class="batch-card"
                :class="{
                  muted: item.isExhausted || item.isExpired,
                  expiring: item.isExpiringSoon,
                }"
              >
                <div class="batch-top">
                  <div class="batch-identity">
                    <span class="pill-badge" :class="item.category === 'base' ? 'accent' : 'info'">
                      {{ item.categoryLabel }}
                    </span>
                    <span v-if="item.customLabel" class="batch-name">{{ item.customLabel }}</span>
                    <span v-else class="batch-name muted-name mono-num">批次 #{{ item.idx + 1 }}</span>
                    <span class="pill-badge" :class="statusBadge(item).tone">
                      {{ statusBadge(item).text }}
                    </span>
                  </div>

                  <div class="batch-amounts mono-num">
                    <strong class="amount-remain">{{ formatNumber(item.remaining) }}</strong>
                    <span v-if="item.granted !== null" class="amount-granted">
                      / {{ formatNumber(item.granted) }}
                    </span>
                    <span class="amount-unit">PTS</span>
                  </div>
                </div>

                <!-- Usage bar when granted_amount is available -->
                <div v-if="item.ratio !== null" class="batch-progress-row">
                  <div class="batch-track">
                    <div
                      class="batch-fill"
                      :class="{
                        base: item.category === 'base',
                        warn: item.isExpiringSoon,
                        empty: item.isExhausted || item.isExpired,
                      }"
                      :style="{ width: `${item.ratio}%` }"
                    ></div>
                  </div>
                  <span class="batch-pct mono-num">{{ Math.round(item.ratio) }}%</span>
                </div>

                <div class="batch-meta">
                  <div class="meta-left mono-num">
                    <span v-if="item.consumed !== null">已用 {{ formatNumber(item.consumed) }} PTS</span>
                    <span v-if="item.grantedAtMs">发放 {{ formatDateOnly(item.grantedAtMs) }}</span>
                  </div>
                  <div class="meta-right mono-num" :class="{ 'warn-text': item.isExpiringSoon }">
                    <template v-if="item.expireAtMs">
                      到期 {{ formatDateTime(item.expireAtMs) }}
                    </template>
                    <template v-else>未标注到期时间</template>
                  </div>
                </div>
              </article>
            </div>

            <div v-else class="modal-empty">
              {{
                counts.all === 0
                  ? '当前账号暂无积分批次明细记录'
                  : '当前筛选条件下没有匹配的积分批次'
              }}
            </div>
          </div>

          <!-- Footer (top-right X closes; no redundant Close button) -->
          <footer class="credit-modal-footer">
            <span class="footer-meta mono-num">
              显示 {{ filteredBatches.length }} / {{ counts.all }} 条记录
            </span>
            <span v-if="usage?.updated_at_ms" class="footer-meta mono-num">
              更新于 {{ new Date(usage.updated_at_ms).toTimeString().slice(0, 8) }}
            </span>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.credit-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 260;
  background: var(--bg-backdrop);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.credit-modal-dialog {
  width: min(760px, 100%);
  max-height: min(86vh, 760px);
  display: flex;
  flex-direction: column;
  background: var(--bg-canvas);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-modal);
  overflow: hidden;
}

.credit-modal-header {
  padding: 18px 22px 14px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.header-main {
  min-width: 0;
}

.title-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.modal-title {
  margin: 0;
  font-size: 19px;
  line-height: 1.25;
  color: var(--text-primary);
}

.modal-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.close-btn {
  padding: 6px;
  border-radius: var(--radius-sm);
  border: none;
  cursor: pointer;
}

.kpi-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding: 14px 22px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
}

.kpi-card {
  padding: 11px 13px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-hairline);
  background: var(--bg-canvas);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kpi-card.primary {
  background: var(--accent-soft);
  border-color: var(--accent-border);
}

.kpi-label {
  font-size: 11.5px;
  color: var(--text-secondary);
  font-weight: 500;
}

.kpi-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

.kpi-card.primary .kpi-value {
  color: var(--accent);
}

.kpi-unit {
  font-size: 10.5px;
  font-weight: 500;
  color: var(--text-tertiary);
  margin-left: 3px;
}

.kpi-status-row {
  margin: 2px 0;
}

.kpi-foot {
  font-size: 11px;
  color: var(--text-tertiary);
}

.modal-toolbar {
  padding: 11px 22px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--border-hairline);
  background: var(--bg-sidebar);
}

.filter-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.14s var(--ease-out);
}

.filter-chip:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
  transform: translateY(-0.5px);
}

.filter-chip:active {
  transform: scale(0.96);
}

.filter-chip.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.filter-chip.warn-chip:not(.active) {
  color: var(--status-warn);
  border-color: var(--status-warn-border);
  background: var(--status-warn-bg);
}

.chip-count {
  font-size: 11px;
  opacity: 0.85;
}

.sort-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

.sort-select {
  padding: 4px 8px;
  border-radius: var(--radius-xs);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}

.modal-body {
  flex: 1;
  min-height: 180px;
  overflow-y: auto;
  padding: 14px 22px 18px;
}

.modal-error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  margin-bottom: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--status-warn-border);
  background: var(--status-warn-bg);
  color: var(--status-warn);
  font-size: 12.5px;
}

.batch-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.batch-card {
  padding: 13px 15px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  box-shadow: var(--shadow-xs);
  display: flex;
  flex-direction: column;
  gap: 9px;
  transition: border-color 0.14s var(--ease-out);
}

.batch-card:hover {
  border-color: var(--border-strong);
}

.batch-card.expiring {
  border-color: var(--status-warn-border);
}

.batch-card.muted {
  opacity: 0.68;
  background: var(--bg-surface-muted);
}

.batch-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.batch-identity {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  min-width: 0;
}

.batch-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.batch-name.muted-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.batch-amounts {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
}

.amount-remain {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}

.amount-granted {
  font-size: 12.5px;
  color: var(--text-secondary);
}

.amount-unit {
  font-size: 10.5px;
  color: var(--text-tertiary);
  margin-left: 2px;
}

.batch-progress-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.batch-track {
  flex: 1;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--bg-surface-muted);
  overflow: hidden;
}

.batch-fill {
  height: 100%;
  border-radius: var(--radius-pill);
  background: var(--status-ok);
  transition: width 0.2s var(--ease-out);
}

.batch-fill.base {
  background: var(--accent);
}

.batch-fill.warn {
  background: var(--status-warn);
}

.batch-fill.empty {
  background: var(--text-tertiary);
}

.batch-pct {
  min-width: 34px;
  text-align: right;
  font-size: 11px;
  color: var(--text-secondary);
}

.batch-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.meta-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.warn-text {
  color: var(--status-warn);
  font-weight: 500;
}

.modal-empty {
  padding: 42px 16px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
}

.credit-modal-footer {
  padding: 12px 22px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.footer-meta {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

/* Modal transition */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.16s var(--ease-out);
}

.modal-fade-enter-active .credit-modal-dialog,
.modal-fade-leave-active .credit-modal-dialog {
  transition: transform 0.18s var(--ease-out), opacity 0.16s var(--ease-out);
}

.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.modal-fade-enter-from .credit-modal-dialog,
.modal-fade-leave-to .credit-modal-dialog {
  opacity: 0;
  transform: scale(0.96) translateY(6px);
}

@media (max-width: 680px) {
  .credit-modal-backdrop {
    padding: 10px;
  }

  .kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    padding: 10px 14px;
  }

  .kpi-card {
    padding: 9px 10px;
  }

  .kpi-value {
    font-size: 16px;
  }

  .credit-modal-header,
  .modal-toolbar,
  .modal-body,
  .credit-modal-footer {
    padding-left: 14px;
    padding-right: 14px;
  }
}
</style>
