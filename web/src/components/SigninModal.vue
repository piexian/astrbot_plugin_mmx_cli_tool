<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import type {
  AccountSigninClaimResponse,
  AccountSigninDayItem,
  AccountUsageResponse,
} from '../types/mcode';
import { bridgePost, pushToast, reportError } from '../composables/useBridge';

const props = defineProps<{
  open: boolean;
  usage: AccountUsageResponse | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'refresh'): void;
  (e: 'claimed', payload: AccountSigninClaimResponse): void;
}>();

const isClaiming = ref(false);

const signin = computed(() => props.usage?.signin ?? null);

const days = computed<AccountSigninDayItem[]>(() => {
  const raw = signin.value?.days;
  if (Array.isArray(raw) && raw.length > 0) return raw;
  // 若后端未返回逐日数组但有今日数据，兜底展示单日卡片
  if (signin.value && signin.value.today_day_no !== null) {
    return [
      {
        day_no: signin.value.today_day_no,
        points: signin.value.today_points,
        status: signin.value.today_claimed ? 3 : 1,
        is_today: true,
        claimed: Boolean(signin.value.today_claimed),
      },
    ];
  }
  return [];
});

const claimedCount = computed(() => days.value.filter((d) => d.claimed).length);

const cycleTotalPoints = computed(() =>
  days.value.reduce(
    (acc, d) =>
      acc +
      (typeof d.points === 'number' ? d.points : 0) +
      (typeof d.bonus_points === 'number' ? d.bonus_points : 0),
    0,
  ),
);

const regularPoints = computed(() => {
  const vals = days.value
    .map((d) => (typeof d.points === 'number' ? d.points : 0))
    .filter((v) => v > 0);
  return vals.length ? Math.min(...vals) : 0;
});

function isMilestoneDay(day: AccountSigninDayItem): boolean {
  const pts = typeof day.points === 'number' ? day.points : 0;
  const bonus = typeof day.bonus_points === 'number' ? day.bonus_points : 0;
  return bonus > 0 || (regularPoints.value > 0 && pts > regularPoints.value);
}

function formatNum(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('zh-CN').format(value);
}

async function handleClaimToday(): Promise<void> {
  if (isClaiming.value || signin.value?.today_claimed) return;
  isClaiming.value = true;
  try {
    const res = await bridgePost<AccountSigninClaimResponse>(
      'account/signin',
      {},
      () => ({
        ok: true,
        claimed: true,
        day_no: signin.value?.today_day_no ?? 1,
        points: signin.value?.today_points ?? 0,
        claim_id: `preview_${Date.now()}`,
      }),
    );
    const ptsText = res.points ? ` +${formatNum(res.points)} PTS` : '';
    pushToast(`签到成功${ptsText}`, 'ok');
    emit('claimed', res);
  } catch (err) {
    reportError(err, '今日签到领取失败');
  } finally {
    isClaiming.value = false;
  }
}

function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && props.open) {
    emit('close');
  }
}

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
        class="signin-modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="signin-modal-title"
        @click="emit('close')"
      >
        <div class="signin-modal-dialog" @click.stop>
          <!-- Header: Refresh + Top-right X only -->
          <header class="signin-modal-header">
            <div class="header-main">
              <div class="title-line">
                <h2 id="signin-modal-title" class="serif-heading modal-title">每日签到</h2>
                <span v-if="signin && signin.today_day_no !== null" class="pill-badge accent mono-num">
                  第 {{ signin.today_day_no }} / {{ Math.max(signin.days_total || days.length, signin.today_day_no) }} 天
                </span>
              </div>
              <p class="modal-subtitle">
                连续签到可累积赠送积分，当日重复领取按天幂等。
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
                aria-label="关闭签到弹窗"
                @click="emit('close')"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </header>

          <!-- Today Hero Action Card -->
          <section v-if="signin" class="today-hero">
            <div class="today-info">
              <div class="today-eyebrow">今日签到奖励</div>
              <div class="today-points mono-num">
                <template v-if="signin.today_points !== null && signin.today_points !== undefined">
                  +{{ formatNum(signin.today_points) }}
                  <span class="unit">PTS</span>
                </template>
                <template v-else>—</template>
              </div>
              <div class="today-meta">
                {{
                  signin.today_claimed
                    ? '今日奖励已到账，可在「全部积分明细」中查看批次有效期'
                    : '点击右侧按钮即可领取今日签到赠送积分'
                }}
              </div>
            </div>

            <div class="today-action">
              <span v-if="signin.today_claimed" class="claimed-pill">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                今日已签到
              </span>
              <button
                v-else
                type="button"
                class="btn btn-primary claim-btn"
                :disabled="isClaiming"
                @click="handleClaimToday"
              >
                {{ isClaiming ? '领取中…' : '立即签到领取' }}
              </button>
            </div>
          </section>

          <!-- Cycle Day Grid -->
          <div class="signin-body">
            <div v-if="days.length" class="days-grid">
              <div
                v-for="(day, index) in days"
                :key="day.day_no ?? index"
                class="day-card"
                :class="{
                  today: day.is_today,
                  claimed: day.claimed,
                  milestone: isMilestoneDay(day),
                  upcoming: !day.claimed && !day.is_today,
                }"
              >
                <div class="day-head">
                  <span class="day-no mono-num">第 {{ day.day_no ?? index + 1 }} 天</span>
                  <span v-if="day.is_today" class="today-tag">今日</span>
                  <span v-else-if="isMilestoneDay(day)" class="bonus-tag">加倍</span>
                </div>

                <div class="day-points mono-num">
                  <template v-if="day.points !== null && day.points !== undefined">
                    +{{ formatNum(day.points) }}
                    <span class="pts-unit">PTS</span>
                  </template>
                  <template v-else>—</template>
                </div>
                <div v-if="day.bonus_points && day.bonus_points > 0" class="day-bonus mono-num">
                  额外 +{{ formatNum(day.bonus_points) }}
                </div>

                <div class="day-status">
                  <template v-if="day.claimed">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                    <span>已领取</span>
                  </template>
                  <template v-else-if="day.is_today">
                    <span>可领取</span>
                  </template>
                  <template v-else>
                    <span>待解锁</span>
                  </template>
                </div>
              </div>
            </div>
            <div v-else class="signin-empty">暂无签到周期数据，请点击右上角刷新重试。</div>
          </div>

          <!-- Clean Summary Footer (no duplicate Close button) -->
          <footer class="signin-modal-footer">
            <span class="footer-meta mono-num">
              本周期已签 {{ claimedCount }} / {{ Math.max(days.length, signin?.days_total || 0) }} 天
              <template v-if="cycleTotalPoints > 0">
                · 周期总奖励 {{ formatNum(cycleTotalPoints) }} PTS
              </template>
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
.signin-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 265;
  background: var(--bg-backdrop);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.signin-modal-dialog {
  width: min(620px, 100%);
  max-height: min(84vh, 660px);
  display: flex;
  flex-direction: column;
  background: var(--bg-canvas);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-modal);
  overflow: hidden;
  transition: transform 200ms var(--ease-out), opacity 170ms var(--ease-out);
}

.modal-fade-enter-from .signin-modal-dialog,
.modal-fade-leave-to .signin-modal-dialog {
  opacity: 0;
  transform: scale(0.96) translateY(8px);
}

.signin-modal-header {
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

.today-hero {
  margin: 16px 22px 0;
  padding: 15px 18px;
  border-radius: var(--radius-md);
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.today-eyebrow {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--accent);
}

.today-points {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
  margin-top: 2px;
}

.today-points .unit {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-left: 3px;
}

.today-meta {
  font-size: 11.5px;
  color: var(--text-secondary);
  margin-top: 3px;
}

.claimed-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: var(--radius-pill);
  background: var(--status-ok-bg);
  border: 1px solid var(--status-ok-border);
  color: var(--status-ok);
  font-size: 12.5px;
  font-weight: 600;
}

.claim-btn {
  padding: 8px 16px;
  font-weight: 600;
}

.signin-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 22px 18px;
}

.days-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(116px, 1fr));
  gap: 10px;
}

.day-card {
  padding: 12px 11px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  display: flex;
  flex-direction: column;
  gap: 7px;
  transition: transform 150ms var(--ease-out), border-color 150ms var(--ease-out);
}

.day-card:hover {
  transform: translateY(-1px);
  border-color: var(--border-strong);
}

.day-card.claimed {
  background: var(--bg-surface-muted);
  border-color: var(--status-ok-border);
}

.day-card.today {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}

.day-card.upcoming {
  opacity: 0.84;
}

.day-card.milestone:not(.claimed) {
  border-color: var(--accent-border);
  background: var(--bg-surface);
}

.day-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.day-no {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.today-tag {
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  color: #ffffff;
  font-size: 10px;
  font-weight: 600;
}

.bonus-tag {
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
}

.day-points {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.pts-unit {
  font-size: 10px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.day-bonus {
  font-size: 10.5px;
  color: var(--accent);
  font-weight: 600;
}

.day-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.day-card.claimed .day-status {
  color: var(--status-ok);
}

.day-card.today:not(.claimed) .day-status {
  color: var(--accent);
  font-weight: 600;
}

.signin-empty {
  padding: 36px 16px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 12.5px;
}

.signin-modal-footer {
  padding: 11px 22px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.footer-meta {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

@media (max-width: 600px) {
  .signin-modal-backdrop {
    padding: 10px;
  }

  .signin-modal-header,
  .signin-body,
  .signin-modal-footer {
    padding-left: 15px;
    padding-right: 15px;
  }

  .today-hero {
    margin: 12px 15px 0;
    padding: 12px 14px;
  }

  .days-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
