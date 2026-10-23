<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import QRCode from 'qrcode';
import logoSvg from '../assets/logo.svg';
import type {
  MPlanQuotaResponse,
  OAuthDeviceStartResponse,
  OAuthPollResponse,
  PluginStatusResponse,
  ServiceRegion,
  AccountUsageResponse,
} from '../types/mcode';
import {
  backendOnline,
  bridgeGet,
  bridgePost,
  copyToClipboard,
  pushToast,
  reportError,
} from '../composables/useBridge';
import { useCanvas } from '../composables/useCanvas';

const props = defineProps<{
  status: PluginStatusResponse;
}>();

const emit = defineEmits<{
  (e: 'status-updated', next: Partial<PluginStatusResponse>): void;
  (e: 'switch-tab', tab: 'canvas' | 'playground' | 'plan'): void;
}>();

const { documentState, selectedNodeId, bringNodeToFront } = useCanvas();

const quota = ref<MPlanQuotaResponse>({
  plan_name: 'M-Plan Pro · 双周期订阅',
  region: 'cn',
  interval_5h: {
    label: '5 小时交互额度',
    total: 500,
    used: 118,
    remaining: 382,
    remaining_percent: 76.4,
    status: 1,
    reset_at_ms: Date.now() + 3 * 3600 * 1000 + 22 * 60 * 1000,
  },
  weekly_video: {
    label: '每周视频生成额度',
    total: 30,
    used: 6,
    remaining: 24,
    remaining_percent: 80.0,
    status: 1,
    reset_at_ms: Date.now() + 4 * 24 * 3600 * 1000,
  },
  credit_fallback: {
    enabled: true,
    active_deducting: false,
    balance: '1,420.00',
    consumed_today: '0.00',
  },
  updated_at_ms: Date.now(),
});

const isRefreshingQuota = ref(false);
const accountUsage = ref<AccountUsageResponse | null>(null);
const usageError = ref('');

// Two-stage OAuth state machine (Stage 1: select region -> Stage 2: QR + user_code + auto poll)
const oauthStage = ref<'idle' | 'select_region' | 'authorizing'>('idle');
const selectedRegion = ref<ServiceRegion>(props.status.region || 'cn');
const deviceAuth = ref<OAuthDeviceStartResponse | null>(null);
const qrSvgHtml = ref<string>('');
const remainingSeconds = ref<number>(0);
const isStartingOAuth = ref(false);
const isSyncingOAuth = ref(false);

let pollTimer: number | null = null;
let countdownTimer: number | null = null;

const ringCircumference = 2 * Math.PI * 24;
const ringDashOffset = computed(() => {
  const pct = Math.max(0, Math.min(100, quota.value.interval_5h.remaining_percent));
  return ringCircumference * (1 - pct / 100);
});

function formatResetCountdown(targetMs: number): string {
  const diffSec = Math.max(0, Math.floor((targetMs - Date.now()) / 1000));
  const hours = Math.floor(diffSec / 3600);
  const mins = Math.floor((diffSec % 3600) / 60);
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return `${days}天 ${hours % 24}小时后重置`;
  }
  return `${hours}小时 ${mins}分后重置`;
}

async function refreshQuota(): Promise<void> {
  isRefreshingQuota.value = true;
  try {
    const tasks: Promise<void>[] = [];
    if (props.status.oauth_connected) {
      tasks.push(refreshAccountUsage());
    } else {
      tasks.push(refreshTokenPlanQuota());
    }
    await Promise.all(tasks);
  } finally {
    isRefreshingQuota.value = false;
  }
}

async function refreshTokenPlanQuota(): Promise<void> {
  try {
    const res = await bridgeGet<MPlanQuotaResponse>('quota', undefined, () => ({
      ...quota.value,
      updated_at_ms: Date.now(),
    }));
    if (res && res.interval_5h) {
      quota.value = res;
    }
  } catch {
    // API Key 模式下额度查询失败时保留上次数据
  }
}

async function refreshAccountUsage(): Promise<void> {
  usageError.value = '';
  try {
    const res = await bridgeGet<AccountUsageResponse>('account/usage');
    if (res) {
      accountUsage.value = res;
    }
  } catch (err) {
    usageError.value = err instanceof Error ? err.message : '积分与套餐数据查询失败';
  }
}

function openRegionSelector(): void {
  selectedRegion.value = props.status.region || 'cn';
  oauthStage.value = 'select_region';
}

function cancelOAuthFlow(): void {
  stopOAuthTimers();
  oauthStage.value = 'idle';
  deviceAuth.value = null;
  qrSvgHtml.value = '';
}

function stopOAuthTimers(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (countdownTimer !== null) {
    window.clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

async function startOAuthStage2(): Promise<void> {
  isStartingOAuth.value = true;
  try {
    const host = selectedRegion.value === 'global' ? 'https://account.minimax.io' : 'https://account.minimax.cn';
    const mockCode = `${Math.random().toString(36).slice(2, 6).toUpperCase()}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
    const info = await bridgePost<OAuthDeviceStartResponse>(
      'oauth/start',
      { region: selectedRegion.value },
      () => ({
        session_id: `oauth_${Date.now()}`,
        region: selectedRegion.value,
        user_code: mockCode,
        verification_uri: `${host}/oauth2/device`,
        verification_uri_complete: `${host}/oauth2/device?user_code=${mockCode}`,
        expires_in: 600,
        interval: 4,
      }),
    );

    deviceAuth.value = info;
    remainingSeconds.value = info.expires_in || 600;
    qrSvgHtml.value = await QRCode.toString(info.verification_uri_complete || info.verification_uri, {
      type: 'svg',
      margin: 1,
      width: 148,
      color: {
        dark: '#262624',
        light: '#ffffff',
      },
    });
    oauthStage.value = 'authorizing';
    startOAuthTimers();
  } catch (err) {
    reportError(err, '启动 OAuth 设备授权失败');
  } finally {
    isStartingOAuth.value = false;
  }
}

function startOAuthTimers(): void {
  stopOAuthTimers();
  countdownTimer = window.setInterval(() => {
    if (remainingSeconds.value > 0) {
      remainingSeconds.value -= 1;
    } else {
      stopOAuthTimers();
      pushToast('设备授权码已过期，请重新发起', 'warn');
      oauthStage.value = 'idle';
    }
  }, 1000);

  const intervalMs = Math.max(3, deviceAuth.value?.interval || 4) * 1000;
  pollTimer = window.setInterval(async () => {
    if (!deviceAuth.value) return;
    try {
      const pollRes = await bridgeGet<OAuthPollResponse>(
        'oauth/poll',
        { session_id: deviceAuth.value.session_id },
        () => ({
          session_id: deviceAuth.value!.session_id,
          status: 'pending',
        }),
      );
      if (pollRes.status === 'authorized') {
        completeOAuthBinding(pollRes.region || selectedRegion.value);
      } else if (pollRes.status === 'denied' || pollRes.status === 'expired') {
        stopOAuthTimers();
        pushToast(`授权已终止 (${pollRes.status})`, 'warn');
        oauthStage.value = 'idle';
      }
    } catch {
      // Keep polling quietly
    }
  }, intervalMs);
}

/**
 * 「我已完成授权，立即同步」：在线模式立刻查询一次授权结果，仅当后端返回
 * authorized 才完成绑定；离线预览模式保持演示闭环。
 */
async function checkOAuthNow(): Promise<void> {
  if (!deviceAuth.value) return;
  const fallbackRegion = deviceAuth.value.region || selectedRegion.value;
  if (!backendOnline.value) {
    completeOAuthBinding(fallbackRegion);
    return;
  }
  isSyncingOAuth.value = true;
  try {
    const pollRes = await bridgeGet<OAuthPollResponse>('oauth/poll', {
      session_id: deviceAuth.value.session_id,
    });
    if (pollRes.status === 'authorized') {
      completeOAuthBinding(pollRes.region || fallbackRegion);
      return;
    }
    if (pollRes.status === 'denied' || pollRes.status === 'expired') {
      stopOAuthTimers();
      pushToast(pollRes.message || `授权已终止 (${pollRes.status})`, 'warn');
      deviceAuth.value = null;
      oauthStage.value = 'idle';
      return;
    }
    pushToast(pollRes.message || '尚未收到授权结果，请先在浏览器或手机上确认', 'info');
  } catch (err) {
    // 授权流程保持可继续轮询
    reportError(err, '查询授权结果失败');
  } finally {
    isSyncingOAuth.value = false;
  }
}

function completeOAuthBinding(region: ServiceRegion): void {
  stopOAuthTimers();
  oauthStage.value = 'idle';
  deviceAuth.value = null;
  emit('status-updated', {
    region,
    auth_mode: 'oauth',
    oauth_connected: true,
    oauth_expires_at: Math.floor(Date.now() / 1000) + 7 * 24 * 3600,
    account_label: `mcode-public (${region.toUpperCase()})`,
  });
  pushToast(`已完成 MiniMax (${region.toUpperCase()}) OAuth 账号绑定`, 'ok');
  refreshQuota();
  // 以服务端真实状态覆盖过期时间与账户标签（不再写死 7 天）
  bridgeGet<PluginStatusResponse>('status', undefined, () => null).then((fresh) => {
    if (fresh) emit('status-updated', fresh);
  });
}

async function handleLogout(): Promise<void> {
  await bridgePost('oauth/logout', {}, () => ({ ok: true }));
  // 以服务端真实状态为准，避免无 API Key 时误显示“兜底”
  const fresh = await bridgeGet<PluginStatusResponse>('status', undefined, () => null);
  if (fresh) {
    emit('status-updated', fresh);
  } else {
    emit('status-updated', {
      auth_mode: 'none',
      oauth_connected: false,
      oauth_expires_at: null,
      account_label: '未配置凭据',
    });
  }
  pushToast(
    '已解绑 OAuth 凭据；若默认聊天模型为 mmx_minimax_code，请在 WebUI 切换其他供应商',
    'info',
  );
}

function focusCanvasNode(nodeId: string): void {
  bringNodeToFront(nodeId);
  emit('switch-tab', 'canvas');
}

onMounted(() => {
  refreshQuota();
});

onUnmounted(() => {
  stopOAuthTimers();
});
</script>

<template>
  <aside class="sidebar">
    <!-- Brand & Model Identity -->
    <div class="sidebar-header">
      <div class="brand-row">
        <div class="brand-mark" aria-hidden="true">
          <img :src="logoSvg" alt="" class="brand-logo" />
        </div>
        <div>
          <div class="brand-title serif-heading">Matrix Studio</div>
        </div>
      </div>
    </div>

    <div class="sidebar-scroll">
      <!-- Section 1: Two-Stage OAuth Account Binding -->
      <section class="panel-card">
        <div class="card-title-row">
          <span class="section-label">账号认证</span>
          <span class="pill-badge" :class="status.oauth_connected ? 'ok' : 'warn'">
            {{ status.oauth_connected ? '已授权 mmoat' : 'API Key 模式' }}
          </span>
        </div>

        <!-- Default Idle View -->
        <div v-if="oauthStage === 'idle'" class="auth-summary">
          <div class="auth-meta">
            <div class="meta-line">
              <span class="meta-key">当前区域</span>
              <span class="meta-val mono-num">
                {{ status.region === 'global' ? '国际站 (minimax.io)' : '国内站 (minimax.cn)' }}
              </span>
            </div>
            <div class="meta-line">
              <span class="meta-key">凭据来源</span>
              <span class="meta-val">{{ status.account_label || 'mcode-public' }}</span>
            </div>
          </div>
          <div class="auth-actions">
            <button type="button" class="btn btn-primary" style="flex: 1" @click="openRegionSelector">
              {{ status.oauth_connected ? '切换区域 / 重新授权' : 'OAuth 扫码绑定' }}
            </button>
            <button
              v-if="status.oauth_connected"
              type="button"
              class="btn"
              title="解绑 OAuth Token"
              @click="handleLogout"
            >
              解绑
            </button>
          </div>
        </div>

        <!-- Stage 1: Region Selection -->
        <div v-else-if="oauthStage === 'select_region'" class="oauth-stage-box">
          <div class="stage-step-header">
            <span class="step-num mono-num">阶段 1 / 2</span>
            <span class="step-title">选择 MiniMax 账号服务区域</span>
          </div>
          <div class="region-options">
            <label class="region-option" :class="{ active: selectedRegion === 'cn' }">
              <input v-model="selectedRegion" type="radio" value="cn" />
              <div>
                <div class="region-name">国内站</div>
                <div class="region-host mono-num">account.minimax.cn</div>
              </div>
            </label>
            <label class="region-option" :class="{ active: selectedRegion === 'global' }">
              <input v-model="selectedRegion" type="radio" value="global" />
              <div>
                <div class="region-name">国际站</div>
                <div class="region-host mono-num">account.minimax.io</div>
              </div>
            </label>
          </div>
          <div class="auth-actions">
            <button
              type="button"
              class="btn btn-primary"
              style="flex: 1"
              :disabled="isStartingOAuth"
              @click="startOAuthStage2"
            >
              {{ isStartingOAuth ? '请求设备码中...' : '下一步：生成授权码' }}
            </button>
            <button type="button" class="btn" @click="cancelOAuthFlow">取消</button>
          </div>
        </div>

        <!-- Stage 2: Sandbox-safe QR Code + User Code Copy -->
        <div v-else-if="oauthStage === 'authorizing' && deviceAuth" class="oauth-stage-box">
          <div class="stage-step-header">
            <span class="step-num mono-num">阶段 2 / 2</span>
            <span class="step-title">扫码或复制链接完成绑定</span>
          </div>

          <div class="qr-container">
            <div class="qr-svg-box" v-html="qrSvgHtml"></div>
            <div class="user-code-panel">
              <div class="code-caption">8 位设备授权验证码</div>
              <div class="user-code-badge mono-num" @click="copyToClipboard(deviceAuth.user_code, '授权验证码')">
                {{ deviceAuth.user_code }}
              </div>
              <button
                type="button"
                class="btn"
                style="width: 100%; font-size: 11.5px"
                @click="copyToClipboard(deviceAuth.verification_uri_complete, '完整授权链接')"
              >
                复制完整授权链接
              </button>
              <div class="countdown-text mono-num">
                轮询等待中 · {{ Math.floor(remainingSeconds / 60) }}:{{ String(remainingSeconds % 60).padStart(2, '0') }}
              </div>
            </div>
          </div>

          <div class="sandbox-note">
            AstrBot 页面沙箱已禁用外链弹窗，请使用手机扫码或复制上方链接在浏览器打开。
          </div>

          <div class="auth-actions">
            <button
              type="button"
              class="btn btn-primary"
              style="flex: 1"
              :disabled="isSyncingOAuth"
              @click="checkOAuthNow"
            >
              {{ isSyncingOAuth ? '同步中…' : '我已完成授权，立即同步' }}
            </button>
            <button type="button" class="btn" @click="cancelOAuthFlow">取消</button>
          </div>
        </div>
      </section>

      <!-- Section 2: M-Plan 2.0 Dual-Cycle Quota -->
      <section class="panel-card">
        <div class="card-title-row">
          <span class="section-label">{{
            status.oauth_connected ? '账户用量' : quota.plan_name
          }}</span>
          <button
            type="button"
            class="btn-ghost icon-btn"
            title="刷新账户用量"
            :disabled="isRefreshingQuota"
            @click="refreshQuota"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 2v6h-6" />
              <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
              <path d="M3 22v-6h6" />
              <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
            </svg>
          </button>
        </div>

        <!-- OAuth 账户：真实积分 / 套餐余量 / 签到 -->
        <template v-if="status.oauth_connected">
          <div v-if="accountUsage">
            <div class="credit-hero">
              <div>
                <div class="credit-bal mono-num">
                  <template v-if="accountUsage.credit_split">
                    {{ accountUsage.credit_split.base }} + {{ accountUsage.credit_split.bonus }}
                  </template>
                  <template v-else>{{ accountUsage.credit_balance || '—' }}</template>
                  <span class="pts">PTS</span>
                </div>
                <div class="credit-sub">
                  {{ accountUsage.credit_split ? '订阅积分 + 赠送积分' : '积分余额' }}
                </div>
              </div>
              <div
                v-if="accountUsage.signin && accountUsage.signin.today_day_no !== null"
                class="signin-mini"
              >
                <div class="mono-num">第 {{ accountUsage.signin.today_day_no }} 天</div>
                <div class="credit-sub">
                  {{ accountUsage.signin.today_claimed ? '今日已签到' : '今日可签到' }}
                </div>
              </div>
            </div>

            <template v-if="accountUsage.interval_5h">
              <div class="divider"></div>
              <div class="quota-5h-box">
                <div class="ring-wrap">
                  <svg width="60" height="60" viewBox="0 0 60 60">
                    <circle cx="30" cy="30" r="24" fill="none" stroke="var(--border-hairline)" stroke-width="5" />
                    <circle
                      cx="30"
                      cy="30"
                      r="24"
                      fill="none"
                      stroke="var(--accent)"
                      stroke-width="5"
                      stroke-linecap="round"
                      :stroke-dasharray="ringCircumference"
                      :stroke-dashoffset="ringCircumference * (1 - Math.max(0, Math.min(100, accountUsage.interval_5h.remaining_percent)) / 100)"
                      transform="rotate(-90 30 30)"
                    />
                  </svg>
                  <span class="ring-text mono-num">{{ Math.round(accountUsage.interval_5h.remaining_percent) }}%</span>
                </div>
                <div class="quota-5h-info">
                  <div class="quota-title">{{ accountUsage.interval_5h.label }}</div>
                  <div class="quota-numbers mono-num">
                    剩余 <strong>{{ accountUsage.interval_5h.remaining ?? '—' }}</strong> /
                    {{ accountUsage.interval_5h.total ?? '∞' }} 次
                  </div>
                  <div class="quota-reset">{{ formatResetCountdown(accountUsage.interval_5h.reset_at_ms) }}</div>
                </div>
              </div>
            </template>
            <template v-if="accountUsage.weekly_video">
              <div class="divider"></div>
              <div class="weekly-video-box">
                <div class="weekly-top">
                  <span class="weekly-label">{{ accountUsage.weekly_video.label }}</span>
                  <span class="weekly-val mono-num">
                    {{ accountUsage.weekly_video.remaining ?? '—' }} /
                    {{ accountUsage.weekly_video.total ?? '∞' }} 部
                  </span>
                </div>
                <div class="progress-track">
                  <div
                    class="progress-fill"
                    :style="{ width: `${Math.min(100, accountUsage.weekly_video.remaining_percent)}%` }"
                  ></div>
                </div>
                <div class="weekly-sub">
                  <span>独立周额度池，不消耗 5h 对话额度</span>
                  <span class="mono-num">{{ formatResetCountdown(accountUsage.weekly_video.reset_at_ms) }}</span>
                </div>
              </div>
            </template>

            <div
              v-if="accountUsage.credit_details.length"
              class="usage-line mono-num"
            >
              积分明细 {{ accountUsage.credit_details.length }} 条 · 合计剩余
              {{ accountUsage.credit_total_remaining }}
            </div>

            <div v-if="accountUsage.model_remains.length" class="usage-models">
              <div
                v-for="m in accountUsage.model_remains"
                :key="m.model_name"
                class="usage-model-row"
              >
                <span class="usage-model-name">{{ m.model_name }}</span>
                <span class="mono-num">剩余 {{ m.usage_count ?? '?' }} 次</span>
              </div>
            </div>

            <div class="usage-updated mono-num">
              更新于 {{ new Date(accountUsage.updated_at_ms).toTimeString().slice(0, 5) }}
            </div>
          </div>
          <div v-else class="usage-empty">
            {{ usageError || '积分与套餐数据加载中…' }}
          </div>
        </template>

        <!-- API Key 模式：Token Plan 额度 -->
        <template v-else>
        <div class="quota-5h-box">
          <div class="ring-wrap">
            <svg width="60" height="60" viewBox="0 0 60 60">
              <circle
                cx="30"
                cy="30"
                r="24"
                fill="none"
                stroke="var(--border-hairline)"
                stroke-width="5"
              />
              <circle
                cx="30"
                cy="30"
                r="24"
                fill="none"
                stroke="var(--accent)"
                stroke-width="5"
                stroke-linecap="round"
                :stroke-dasharray="ringCircumference"
                :stroke-dashoffset="ringDashOffset"
                transform="rotate(-90 30 30)"
              />
            </svg>
            <span class="ring-text mono-num">{{ Math.round(quota.interval_5h.remaining_percent) }}%</span>
          </div>
          <div class="quota-5h-info">
            <div class="quota-title">5 小时滚动交互额度</div>
            <div class="quota-numbers mono-num">
              剩余 <strong>{{ quota.interval_5h.remaining }}</strong> / {{ quota.interval_5h.total }} 次
            </div>
            <div class="quota-reset">{{ formatResetCountdown(quota.interval_5h.reset_at_ms) }}</div>
          </div>
        </div>

        <div class="divider"></div>

        <!-- Weekly Video Limit Bar -->
        <div class="weekly-video-box">
          <div class="weekly-top">
            <span class="weekly-label">每周视频专用额度</span>
            <span class="weekly-val mono-num">
              {{ quota.weekly_video.remaining }} / {{ quota.weekly_video.total }} 部
            </span>
          </div>
          <div class="progress-track">
            <div
              class="progress-fill"
              :style="{ width: `${Math.min(100, quota.weekly_video.remaining_percent)}%` }"
            ></div>
          </div>
          <div class="weekly-sub">
            <span>独立周额度池，不消耗 5h 对话额度</span>
            <span class="mono-num">{{ formatResetCountdown(quota.weekly_video.reset_at_ms) }}</span>
          </div>
        </div>

        <div class="divider"></div>

        <!-- Credit Fallback Row -->
        <div class="credit-row">
          <div>
            <div class="credit-title">积分兜底</div>
            <div class="credit-sub">超额后按时间衰减自动抵扣</div>
          </div>
          <div class="credit-right mono-num">
            <div class="credit-bal">{{ quota.credit_fallback.balance }} PTS</div>
            <div class="credit-status">{{ quota.credit_fallback.active_deducting ? '抵扣中' : '待命' }}</div>
          </div>
        </div>
        </template>
      </section>

      <!-- Section 3: Session Deliverable Assets (<deliver-assets>) -->
      <section class="panel-card">
        <div class="card-title-row">
          <span class="section-label">画布资产节点 ({{ documentState.nodes.length }})</span>
          <span class="mono-num" style="font-size: 11px; color: var(--text-tertiary)">
            seq #{{ documentState.changeSeq }}
          </span>
        </div>
        <div class="asset-list">
          <button
            v-for="node in documentState.nodes"
            :key="node.id"
            type="button"
            class="asset-item"
            :class="{ selected: selectedNodeId === node.id }"
            @click="focusCanvasNode(node.id)"
          >
            <span class="asset-type-dot" :class="node.file.mimeType?.startsWith('image/') ? 'img' : 'doc'"></span>
            <span class="asset-name">{{ node.file.fileName || node.file.relativePath }}</span>
            <span v-if="node.annotations.length" class="pill-badge accent mono-num">
              {{ node.annotations.length }} 批注
            </span>
          </button>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 320px;
  min-width: 320px;
  height: 100%;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border-hairline);
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--border-hairline);
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--accent-border);
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-title {
  font-size: 16px;
  color: var(--text-primary);
  line-height: 1.2;
}

.brand-logo {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  display: block;
}


.sidebar-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 12px;
  box-shadow: var(--shadow-card);
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.01em;
}

.icon-btn {
  padding: 4px;
  border-radius: var(--radius-sm);
  border: none;
  cursor: pointer;
}

.auth-meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
}

.meta-line {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.meta-key {
  color: var(--text-secondary);
}

.meta-val {
  color: var(--text-primary);
  font-weight: 500;
}

.auth-actions {
  display: flex;
  gap: 6px;
}

.oauth-stage-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stage-step-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.step-num {
  color: var(--accent);
  font-weight: 600;
}

.step-title {
  color: var(--text-primary);
  font-weight: 500;
}

.region-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.region-option {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  cursor: pointer;
  transition: border-color 0.14s ease, background-color 0.14s ease;
}

.region-option.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.region-name {
  font-size: 12.5px;
  font-weight: 600;
}

.region-host {
  font-size: 11px;
  color: var(--text-secondary);
}

.qr-container {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
}

.qr-svg-box {
  width: 104px;
  height: 104px;
  min-width: 104px;
  background: #ffffff;
  padding: 4px;
  border-radius: 6px;
  border: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: center;
}

.qr-svg-box :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.user-code-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.code-caption {
  font-size: 11px;
  color: var(--text-secondary);
}

.user-code-badge {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--accent);
  background: var(--bg-surface);
  border: 1px dashed var(--accent-border);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  text-align: center;
  cursor: pointer;
}

.countdown-text {
  font-size: 11px;
  color: var(--text-tertiary);
}

.sandbox-note {
  font-size: 11px;
  color: var(--text-secondary);
  line-height: 1.4;
}

.quota-5h-box {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ring-wrap {
  position: relative;
  width: 60px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ring-text {
  position: absolute;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.quota-5h-info {
  flex: 1;
}

.quota-title {
  font-size: 12.5px;
  font-weight: 600;
}

.quota-numbers {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.quota-numbers strong {
  color: var(--text-primary);
}

.quota-reset {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.divider {
  height: 1px;
  background: var(--border-hairline);
  margin: 10px 0;
}
.weekly-top {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 6px;
}

.weekly-label {
  font-weight: 500;
}

.progress-track {
  width: 100%;
  height: 6px;
  background: var(--bg-surface-muted);
  border-radius: 999px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--status-ok);
  border-radius: 999px;
  transition: width 0.25s ease;
}

.weekly-sub {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 5px;
}

.credit-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.credit-title {
  font-size: 12px;
  font-weight: 500;
}

.credit-sub {
  font-size: 11px;
  color: var(--text-tertiary);
}

.credit-right {
  text-align: right;
}

.credit-bal {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.credit-status {
  font-size: 11px;
  color: var(--status-ok);
}

.credit-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.credit-hero .credit-bal {
  font-size: 20px;
}

.credit-hero .pts {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.signin-mini {
  text-align: right;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.usage-line {
  font-size: 11.5px;
  color: var(--text-secondary);
  margin-top: 8px;
}

.usage-models {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.usage-model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
}

.usage-model-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
}

.usage-updated {
  font-size: 10.5px;
  color: var(--text-tertiary);
  margin-top: 8px;
}

.usage-empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 12px 0;
  line-height: 1.5;
}
.asset-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.asset-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.asset-item:hover {
  background: var(--bg-surface-muted);
}

.asset-item.selected {
  background: var(--accent-soft);
  border-color: var(--accent-border);
}

.asset-type-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
}

.asset-type-dot.doc {
  background: var(--status-ok);
}

.asset-name {
  flex: 1;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .sidebar {
    width: 100%;
    min-width: 0;
  }
}
</style>
