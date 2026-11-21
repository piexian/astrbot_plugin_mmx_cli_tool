<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import QRCode from 'qrcode';
import logoSvg from '../assets/logo.svg';
import CreditDetailsModal from './CreditDetailsModal.vue';
import SigninModal from './SigninModal.vue';
import type {
  AccountModelRemainItem,
  AccountSigninClaimResponse,
  AccountUsageResponse,
  MPlanQuotaResponse,
  OAuthDeviceStartResponse,
  OAuthPollResponse,
  PluginStatusResponse,
  QuotaBucket,
  ServiceRegion,
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
  statusReady: boolean;
}>();

const emit = defineEmits<{
  (e: 'status-updated', next: Partial<PluginStatusResponse>): void;
  (e: 'switch-tab', tab: 'canvas' | 'playground' | 'logs'): void;
  (e: 'close-sidebar'): void;
}>();

const { documentState, selectedNodeId, bringNodeToFront } = useCanvas();

const EXPIRING_SOON_MS = 7 * 24 * 3600 * 1000;

function createPreviewQuota(): MPlanQuotaResponse {
  return {
    plan_name: 'Token Plan · 国内站 (预览)',
    region: 'cn',
    has_plan_quota: true,
    has_interval: true,
    has_weekly_video: true,
    interval_5h: {
      label: '5小时额度',
      total: 500,
      used: 118,
      remaining: 382,
      has_counts: true,
      remaining_percent: 76.4,
      status: 1,
      reset_at_ms: Date.now() + 3 * 3600 * 1000 + 22 * 60 * 1000,
    },
    weekly_video: {
      label: '1周额度',
      total: 30,
      used: 6,
      remaining: 24,
      has_counts: true,
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
  };
}

function createPreviewAccountUsage(): AccountUsageResponse {
  const now = Date.now();
  const day = 24 * 3600 * 1000;
  return {
    account: {
      name: 'Matrix Preview',
      email: 'studio@minimax.cn',
      region: props.status.region || 'cn',
    },
    credit_balance: '12480',
    credit_split: { base: '4256', bonus: '8224' },
    fallback_enabled: true,
    credit_details: [
      {
        credit_type: 1,
        category: 'base',
        label: 'M-Plan 月度订阅积分',
        granted_amount: '6000',
        remaining_amount: '4256',
        consumed_amount: '1744',
        granted_at_ms: now - 10 * day,
        expire_at_ms: now + 20 * day,
      },
      {
        credit_type: 2,
        category: 'bonus',
        label: '连续签到奖励 (第 7 天)',
        granted_amount: '2400',
        remaining_amount: '2400',
        consumed_amount: '0',
        granted_at_ms: now - 25 * day,
        expire_at_ms: now + 3 * day,
      },
      {
        credit_type: 2,
        category: 'bonus',
        label: '每日签到积分',
        granted_amount: '1424',
        remaining_amount: '1424',
        consumed_amount: '0',
        granted_at_ms: now - 1 * day,
        expire_at_ms: now + 29 * day,
      },
      {
        credit_type: 2,
        category: 'bonus',
        label: '桌面端活动赠送',
        granted_amount: '3000',
        remaining_amount: '2600',
        consumed_amount: '400',
        granted_at_ms: now - 12 * day,
        expire_at_ms: now + 18 * day,
      },
      {
        credit_type: 2,
        category: 'bonus',
        label: '新版本体验补贴',
        granted_amount: '1800',
        remaining_amount: '1800',
        consumed_amount: '0',
        granted_at_ms: now - 5 * day,
        expire_at_ms: now + 5 * day,
      },
      {
        credit_type: 1,
        category: 'base',
        label: '上期订阅周期结转',
        granted_amount: '5000',
        remaining_amount: '0',
        consumed_amount: '5000',
        granted_at_ms: now - 40 * day,
        expire_at_ms: now - 10 * day,
      },
      {
        credit_type: 2,
        category: 'bonus',
        label: '早期签到批次',
        granted_amount: '1000',
        remaining_amount: '0',
        consumed_amount: '1000',
        granted_at_ms: now - 35 * day,
        expire_at_ms: now + 2 * day,
      },
    ],
    credit_details_count: 7,
    credit_total_remaining: '12480',
    model_remains: [
      {
        model_name: 'general',
        remaining: 382,
        total: 500,
        used: 118,
        remaining_percent: 76.4,
        unlimited: false,
        unavailable: false,
        window_label: '5小时额度',
      },
      {
        model_name: 'MiniMax-Hailuo-2.3',
        is_video: true,
        remaining: 24,
        total: 30,
        used: 6,
        remaining_percent: 80.0,
        unlimited: false,
        unavailable: false,
        window_label: '1周额度',
      },
    ],
    signin: {
      scene: 2,
      days_total: 7,
      today_day_no: 2,
      today_points: 400,
      today_claimed: true,
      days: [
        { day_no: 1, points: 400, bonus_points: 0, status: 3, is_today: false, claimed: true },
        { day_no: 2, points: 400, bonus_points: 0, status: 3, is_today: true, claimed: true },
        { day_no: 3, points: 400, bonus_points: 0, status: 1, is_today: false, claimed: false },
        { day_no: 4, points: 1000, bonus_points: 0, status: 1, is_today: false, claimed: false },
        { day_no: 5, points: 400, bonus_points: 0, status: 1, is_today: false, claimed: false },
        { day_no: 6, points: 400, bonus_points: 0, status: 1, is_today: false, claimed: false },
        { day_no: 7, points: 1000, bonus_points: 0, status: 1, is_today: false, claimed: false },
      ],
    },
    plan_name: 'Coding Plan 用量',
    interval_5h: {
      label: '5小时额度',
      total: 500,
      used: 118,
      remaining: 382,
      has_counts: true,
      remaining_percent: 76.4,
      status: 1,
      reset_at_ms: now + 3 * 3600 * 1000 + 22 * 60 * 1000,
    },
    weekly_video: {
      label: '1周额度',
      total: 30,
      used: 6,
      remaining: 24,
      has_counts: true,
      remaining_percent: 80.0,
      status: 1,
      reset_at_ms: now + 4 * day,
    },
    updated_at_ms: now,
  };
}

const quota = ref<MPlanQuotaResponse | null>(null);
const quotaError = ref('');
const accountUsage = ref<AccountUsageResponse | null>(null);
const usageError = ref('');
const isRefreshingQuota = ref(false);
const creditModalOpen = ref(false);
const signinModalOpen = ref(false);

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

function ringOffset(pct: number | undefined): number {
  const safe = Math.max(0, Math.min(100, pct ?? 0));
  return ringCircumference * (1 - safe / 100);
}

function parseNumeric(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(num) ? num : null;
}

function formatCreditNum(value: string | number | null | undefined): string {
  const num = parseNumeric(value);
  if (num === null) return value ? String(value) : '—';
  const hasDecimal = Math.abs(num - Math.trunc(num)) > 1e-6;
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: hasDecimal ? 2 : 0,
  }).format(num);
}

function formatResetCountdown(targetMs: number | null | undefined): string {
  if (!targetMs || !Number.isFinite(targetMs)) return '';
  const diffSec = Math.floor((targetMs - Date.now()) / 1000);
  if (diffSec <= 30) return '';
  const hours = Math.floor(diffSec / 3600);
  const mins = Math.floor((diffSec % 3600) / 60);
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return `${days}天 ${hours % 24}小时后重置`;
  }
  if (hours > 0) return `${hours}小时 ${mins}分后重置`;
  return `${Math.max(1, mins)}分钟后重置`;
}

function formatBucketSubtitle(bucket: QuotaBucket, unit: string): string {
  if (bucket.status === 3) return '当前套餐不限量';
  if (bucket.has_counts === false || (bucket.total === 0 && bucket.remaining_percent > 0)) {
    return `剩余比例 ${Math.round(bucket.remaining_percent)}%`;
  }
  return `剩余 ${bucket.remaining} / ${bucket.total} ${unit}`;
}

function formatModelRemain(m: AccountModelRemainItem): { text: string; tone: string } {
  if (m.unavailable) return { text: '不在套餐内', tone: 'muted' };
  if (m.unlimited) return { text: '不限量', tone: 'ok' };
  if (typeof m.remaining === 'number' && typeof m.total === 'number' && m.total > 0) {
    return {
      text: `${m.remaining} / ${m.total} ${m.is_video ? '部' : '次'}`,
      tone: m.remaining <= 0 ? 'danger' : '',
    };
  }
  if (typeof m.remaining === 'number') {
    return { text: `剩余 ${m.remaining} ${m.is_video ? '部' : '次'}`, tone: '' };
  }
  if (typeof m.remaining_percent === 'number') {
    return { text: `剩余 ${Math.round(m.remaining_percent)}%`, tone: '' };
  }
  if (typeof m.usage_count === 'number') {
    return { text: `剩余 ${m.usage_count} 次`, tone: '' };
  }
  return { text: '暂无数据', tone: 'muted' };
}

const totalCreditsDisplay = computed(() => {
  const usage = accountUsage.value;
  if (!usage) return '—';
  if (usage.credit_details.length > 0 && usage.credit_total_remaining) {
    return formatCreditNum(usage.credit_total_remaining);
  }
  return formatCreditNum(usage.credit_balance);
});

const creditBatchStats = computed(() => {
  const details = accountUsage.value?.credit_details || [];
  const now = Date.now();
  let active = 0;
  let expiringSoon = 0;
  let expiringAmount = 0;
  for (const item of details) {
    const rem = parseNumeric(item.remaining_amount) ?? 0;
    const exp = typeof item.expire_at_ms === 'number' && item.expire_at_ms > 0 ? item.expire_at_ms : null;
    const isExpired = exp !== null && exp <= now;
    if (rem > 0 && !isExpired) {
      active += 1;
      if (exp !== null && exp - now <= EXPIRING_SOON_MS) {
        expiringSoon += 1;
        expiringAmount += rem;
      }
    }
  }
  return {
    totalCount: accountUsage.value?.credit_details_count ?? details.length,
    active,
    expiringSoon,
    expiringAmount,
  };
});

const showApiInterval = computed(() => {
  if (!quota.value) return false;
  if (quota.value.has_interval !== undefined) return quota.value.has_interval;
  return quota.value.interval_5h.total > 0 || quota.value.interval_5h.remaining_percent > 0 || quota.value.interval_5h.status === 3;
});

const showApiWeeklyVideo = computed(() => {
  if (!quota.value) return false;
  if (quota.value.has_weekly_video !== undefined) return quota.value.has_weekly_video;
  return quota.value.weekly_video.total > 0 || quota.value.weekly_video.status === 3;
});

async function refreshQuota(): Promise<void> {
  if (!props.statusReady) return;
  if (props.status.auth_mode === 'none' && !props.status.oauth_connected) {
    accountUsage.value = null;
    quota.value = null;
    usageError.value = '';
    quotaError.value = '';
    return;
  }
  isRefreshingQuota.value = true;
  try {
    if (props.status.oauth_connected) {
      await refreshAccountUsage();
    } else {
      await refreshTokenPlanQuota();
    }
  } finally {
    isRefreshingQuota.value = false;
  }
}

async function refreshTokenPlanQuota(): Promise<void> {
  quotaError.value = '';
  try {
    const res = await bridgeGet<MPlanQuotaResponse>('quota', undefined, () => createPreviewQuota());
    if (res && res.interval_5h) {
      quota.value = res;
    }
  } catch (err) {
    quotaError.value = err instanceof Error ? err.message : 'Token Plan 额度查询失败';
  }
}

async function refreshAccountUsage(): Promise<void> {
  usageError.value = '';
  try {
    const res = await bridgeGet<AccountUsageResponse>('account/usage', undefined, () =>
      createPreviewAccountUsage(),
    );
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
        dark: '#1f1e1d',
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
  bridgeGet<PluginStatusResponse>('status', undefined, () => null).then((fresh) => {
    if (fresh) emit('status-updated', fresh);
  });
}

async function handleLogout(): Promise<void> {
  await bridgePost('oauth/logout', {}, () => ({ ok: true }));
  accountUsage.value = null;
  creditModalOpen.value = false;
  signinModalOpen.value = false;
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

function handleSigninClaimed(res: AccountSigninClaimResponse): void {
  if (backendOnline.value) {
    refreshAccountUsage();
    return;
  }
  // 离线预览态同步更新本地签到状态
  if (accountUsage.value?.signin) {
    accountUsage.value = {
      ...accountUsage.value,
      signin: {
        ...accountUsage.value.signin,
        today_claimed: true,
        days: accountUsage.value.signin.days?.map((d) =>
          d.is_today || (res.day_no && d.day_no === res.day_no)
            ? { ...d, status: 3, claimed: true }
            : d,
        ),
      },
      updated_at_ms: Date.now(),
    };
  }
}

function focusCanvasNode(nodeId: string): void {
  bringNodeToFront(nodeId);
  emit('switch-tab', 'canvas');
}

watch(
  () => [props.statusReady, props.status.oauth_connected, props.status.auth_mode, props.status.region],
  ([ready]) => {
    if (ready) {
      refreshQuota();
    }
  },
  { immediate: true },
);

onMounted(() => {
  if (props.statusReady && !accountUsage.value && !quota.value) {
    refreshQuota();
  }
});

onUnmounted(() => {
  stopOAuthTimers();
});
</script>

<template>
  <aside class="sidebar">
    <!-- Brand Identity -->
    <div class="sidebar-header">
      <div class="brand-row">
        <div class="brand-mark" aria-hidden="true">
          <img :src="logoSvg" alt="" class="brand-logo" />
        </div>
        <div class="brand-text">
          <div class="brand-title serif-heading">Matrix Studio</div>
          <div class="brand-caption">MiniMax Code 工作台</div>
        </div>
        <button
          type="button"
          class="btn-ghost mobile-close-btn"
          title="收起左侧面板"
          aria-label="收起左侧面板"
          @click="emit('close-sidebar')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
    </div>

    <div class="sidebar-scroll">
      <!-- Section 1: OAuth Account Binding -->
      <section class="panel-card">
        <div class="card-title-row">
          <span class="section-label">账号认证</span>
          <span
            class="pill-badge"
            :class="
              status.oauth_connected ? 'ok' : status.auth_mode === 'api_key' ? 'info' : 'warn'
            "
          >
            {{
              status.oauth_connected
                ? 'OAuth 已绑定'
                : status.auth_mode === 'api_key'
                  ? 'API Key 模式'
                  : '未配置'
            }}
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
              {{ status.oauth_connected ? '切换区域 / 重授权' : 'OAuth 扫码绑定' }}
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
            <span class="step-num mono-num">步骤 1 / 2</span>
            <span class="step-title">选择账号服务区域</span>
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
              {{ isStartingOAuth ? '请求设备码…' : '下一步：生成授权码' }}
            </button>
            <button type="button" class="btn" @click="cancelOAuthFlow">取消</button>
          </div>
        </div>

        <!-- Stage 2: QR Code + User Code -->
        <div v-else-if="oauthStage === 'authorizing' && deviceAuth" class="oauth-stage-box">
          <div class="stage-step-header">
            <span class="step-num mono-num">步骤 2 / 2</span>
            <span class="step-title">扫码或复制链接完成绑定</span>
          </div>

          <div class="qr-container">
            <div class="qr-svg-box" v-html="qrSvgHtml"></div>
            <div class="user-code-panel">
              <div class="code-caption">8 位设备授权码</div>
              <div
                class="user-code-badge mono-num"
                title="点击复制验证码"
                @click="copyToClipboard(deviceAuth.user_code, '授权验证码')"
              >
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
                等待确认 · {{ Math.floor(remainingSeconds / 60) }}:{{ String(remainingSeconds % 60).padStart(2, '0') }}
              </div>
            </div>
          </div>

          <div class="sandbox-note">
            沙箱环境不支持直接跳转外链，请扫码或复制链接到浏览器打开。
          </div>

          <div class="auth-actions">
            <button
              type="button"
              class="btn btn-primary"
              style="flex: 1"
              :disabled="isSyncingOAuth"
              @click="checkOAuthNow"
            >
              {{ isSyncingOAuth ? '同步中…' : '已完成授权，立即同步' }}
            </button>
            <button type="button" class="btn" @click="cancelOAuthFlow">取消</button>
          </div>
        </div>
      </section>

      <!-- Section 2: Optimized Account Credits & Quota -->
      <section class="panel-card usage-card">
        <div class="card-title-row">
          <span class="section-label">
            {{
              status.oauth_connected
                ? '积分与用量'
                : status.auth_mode === 'api_key'
                  ? quota?.plan_name || 'API 额度'
                  : '用量概览'
            }}
          </span>
          <button
            type="button"
            class="btn-ghost icon-btn"
            title="刷新积分与用量"
            aria-label="刷新积分与用量"
            :disabled="isRefreshingQuota"
            @click="refreshQuota"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              :class="{ 'is-spinning': isRefreshingQuota }"
            >
              <path d="M21 2v6h-6" />
              <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
              <path d="M3 22v-6h6" />
              <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
            </svg>
          </button>
        </div>

        <!-- Case A: OAuth Account Usage -->
        <template v-if="status.oauth_connected">
          <div v-if="accountUsage" class="usage-content">
            <!-- Hero Credit Card -->
            <div class="credit-surface">
              <div class="credit-surface-head">
                <span class="credit-eyebrow">可用总积分</span>
                <span
                  v-if="accountUsage.fallback_enabled !== null"
                  class="pill-badge"
                  :class="accountUsage.fallback_enabled ? 'ok' : ''"
                  title="套餐额度用尽时是否自动扣除积分"
                >
                  {{ accountUsage.fallback_enabled ? '积分兜底已开' : '积分兜底关闭' }}
                </span>
              </div>

              <div class="credit-hero-figure mono-num">
                <span class="credit-amount">{{ totalCreditsDisplay }}</span>
                <span class="credit-unit">PTS</span>
              </div>

              <!-- Split tiles: Base vs Bonus -->
              <div v-if="accountUsage.credit_split" class="credit-split-grid">
                <div class="split-tile">
                  <span class="split-label">订阅积分</span>
                  <span class="split-val mono-num">
                    {{ formatCreditNum(accountUsage.credit_split.base) }}
                  </span>
                </div>
                <div class="split-tile">
                  <span class="split-label">赠送积分</span>
                  <span class="split-val mono-num">
                    {{ formatCreditNum(accountUsage.credit_split.bonus) }}
                  </span>
                </div>
              </div>

              <!-- Expiring soon callout -->
              <div v-if="creditBatchStats.expiringSoon > 0" class="expiring-callout">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                <span>
                  {{ creditBatchStats.expiringSoon }} 笔（<strong class="mono-num">{{
                    formatCreditNum(creditBatchStats.expiringAmount)
                  }}</strong>
                  PTS）7 天内到期
                </span>
              </div>

              <!-- Trigger Modal Button -->
              <button
                type="button"
                class="btn btn-soft credit-detail-trigger"
                @click="creditModalOpen = true"
              >
                <span>
                  查看全部积分
                  <template v-if="creditBatchStats.totalCount > 0">
                    ({{ creditBatchStats.totalCount }} 笔)
                  </template>
                </span>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            </div>

            <!-- Clickable Daily Sign-in Card -> Mini Cycle Dots + Opens SigninModal -->
            <button
              v-if="accountUsage.signin && (accountUsage.signin.today_day_no !== null || accountUsage.signin.days_total > 0)"
              type="button"
              class="signin-strip"
              @click="signinModalOpen = true"
            >
              <div class="signin-top-row">
                <div class="signin-left">
                  <span class="signin-title">每日签到</span>
                  <span class="signin-meta mono-num">
                    <template v-if="accountUsage.signin.today_day_no !== null">
                      第 {{ accountUsage.signin.today_day_no }} / {{ Math.max(accountUsage.signin.days_total || 0, accountUsage.signin.today_day_no) }} 天
                    </template>
                    <template v-if="accountUsage.signin.today_points">
                      · +{{ formatCreditNum(accountUsage.signin.today_points) }} PTS
                    </template>
                  </span>
                </div>
                <div class="signin-right">
                  <span
                    class="pill-badge"
                    :class="accountUsage.signin.today_claimed ? 'ok' : 'warn'"
                  >
                    {{ accountUsage.signin.today_claimed ? '已签到' : '今日可签' }}
                  </span>
                  <svg class="row-arrow" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </div>
              </div>

              <!-- Full Cycle Mini Progress Dots -->
              <div v-if="accountUsage.signin.days && accountUsage.signin.days.length" class="signin-cycle-dots">
                <span
                  v-for="(day, dIdx) in accountUsage.signin.days"
                  :key="day.day_no ?? dIdx"
                  class="cycle-dot-cell"
                  :class="{
                    claimed: day.claimed,
                    today: day.is_today,
                  }"
                  :title="`第 ${day.day_no ?? dIdx + 1} 天 · +${day.points ?? 0} PTS`"
                >
                  <span class="cycle-dot-bar"></span>
                  <span class="cycle-dot-day mono-num">D{{ day.day_no ?? dIdx + 1 }}</span>
                </span>
              </div>
            </button>

            <!-- 5h / Interval Quota Ring -->
            <template v-if="accountUsage.interval_5h">
              <div class="divider"></div>
              <div class="quota-5h-box">
                <div class="ring-wrap">
                  <svg width="58" height="58" viewBox="0 0 60 60">
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
                      :stroke-dashoffset="ringOffset(accountUsage.interval_5h.remaining_percent)"
                      transform="rotate(-90 30 30)"
                    />
                  </svg>
                  <span class="ring-text mono-num">
                    {{
                      accountUsage.interval_5h.status === 3
                        ? '∞'
                        : `${Math.round(accountUsage.interval_5h.remaining_percent)}%`
                    }}
                  </span>
                </div>
                <div class="quota-5h-info">
                  <div class="quota-title">{{ accountUsage.interval_5h.label }}</div>
                  <div class="quota-numbers mono-num">
                    {{ formatBucketSubtitle(accountUsage.interval_5h, '次') }}
                  </div>
                  <div
                    v-if="formatResetCountdown(accountUsage.interval_5h.reset_at_ms)"
                    class="quota-reset mono-num"
                  >
                    {{ formatResetCountdown(accountUsage.interval_5h.reset_at_ms) }}
                  </div>
                </div>
              </div>
            </template>

            <!-- Weekly Video Quota Bar -->
            <template v-if="accountUsage.weekly_video">
              <div class="divider"></div>
              <div class="weekly-video-box">
                <div class="weekly-top">
                  <span class="weekly-label">{{ accountUsage.weekly_video.label }}</span>
                  <span class="weekly-val mono-num">
                    {{
                      accountUsage.weekly_video.status === 3
                        ? '不限量 ∞'
                        : accountUsage.weekly_video.has_counts === false
                          ? `${Math.round(accountUsage.weekly_video.remaining_percent)}%`
                          : `${accountUsage.weekly_video.remaining} / ${accountUsage.weekly_video.total} 部`
                    }}
                  </span>
                </div>
                <div class="progress-track">
                  <div
                    class="progress-fill"
                    :style="{ width: `${Math.min(100, accountUsage.weekly_video.remaining_percent)}%` }"
                  ></div>
                </div>
                <div
                  v-if="formatResetCountdown(accountUsage.weekly_video.reset_at_ms)"
                  class="weekly-sub"
                >
                  <span>视频独立周期池</span>
                  <span class="mono-num">{{ formatResetCountdown(accountUsage.weekly_video.reset_at_ms) }}</span>
                </div>
              </div>
            </template>

            <!-- Model Remains List -->
            <div v-if="accountUsage.model_remains.length" class="usage-models">
              <div class="sub-section-label">模型余量明细</div>
              <div
                v-for="m in accountUsage.model_remains"
                :key="m.model_name"
                class="usage-model-row"
              >
                <span class="usage-model-name" :title="m.model_name">{{ m.model_name }}</span>
                <span
                  class="usage-model-val mono-num"
                  :class="formatModelRemain(m).tone"
                >
                  {{ formatModelRemain(m).text }}
                </span>
              </div>
            </div>

            <div class="usage-updated mono-num">
              更新于 {{ new Date(accountUsage.updated_at_ms).toTimeString().slice(0, 8) }}
            </div>
          </div>

          <div v-else-if="usageError" class="usage-error-box">
            <div class="usage-error-text">{{ usageError }}</div>
            <button type="button" class="btn" @click="refreshQuota">重试查询</button>
          </div>

          <div v-else class="usage-empty">正在加载积分与套餐数据…</div>
        </template>

        <!-- Case B: API Key Mode -->
        <template v-else-if="status.auth_mode === 'api_key'">
          <div v-if="quota" class="usage-content">
            <template v-if="showApiInterval">
              <div class="quota-5h-box">
                <div class="ring-wrap">
                  <svg width="58" height="58" viewBox="0 0 60 60">
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
                      :stroke-dashoffset="ringOffset(quota.interval_5h.remaining_percent)"
                      transform="rotate(-90 30 30)"
                    />
                  </svg>
                  <span class="ring-text mono-num">
                    {{
                      quota.interval_5h.status === 3
                        ? '∞'
                        : `${Math.round(quota.interval_5h.remaining_percent)}%`
                    }}
                  </span>
                </div>
                <div class="quota-5h-info">
                  <div class="quota-title">{{ quota.interval_5h.label || '当前周期额度' }}</div>
                  <div class="quota-numbers mono-num">
                    {{ formatBucketSubtitle(quota.interval_5h, '次') }}
                  </div>
                  <div
                    v-if="formatResetCountdown(quota.interval_5h.reset_at_ms)"
                    class="quota-reset mono-num"
                  >
                    {{ formatResetCountdown(quota.interval_5h.reset_at_ms) }}
                  </div>
                </div>
              </div>
            </template>

            <template v-if="showApiWeeklyVideo">
              <div v-if="showApiInterval" class="divider"></div>
              <div class="weekly-video-box">
                <div class="weekly-top">
                  <span class="weekly-label">{{ quota.weekly_video.label || '每周视频额度' }}</span>
                  <span class="weekly-val mono-num">
                    {{
                      quota.weekly_video.status === 3
                        ? '不限量 ∞'
                        : `${quota.weekly_video.remaining} / ${quota.weekly_video.total} 部`
                    }}
                  </span>
                </div>
                <div class="progress-track">
                  <div
                    class="progress-fill"
                    :style="{ width: `${Math.min(100, quota.weekly_video.remaining_percent)}%` }"
                  ></div>
                </div>
                <div
                  v-if="formatResetCountdown(quota.weekly_video.reset_at_ms)"
                  class="weekly-sub"
                >
                  <span>独立周额度池</span>
                  <span class="mono-num">{{ formatResetCountdown(quota.weekly_video.reset_at_ms) }}</span>
                </div>
              </div>
            </template>

            <!-- Account Balance / Credit Fallback -->
            <template v-if="quota.credit_fallback.enabled || quota.balance_breakdown">
              <div v-if="showApiInterval || showApiWeeklyVideo" class="divider"></div>
              <div class="credit-surface compact">
                <div class="credit-surface-head">
                  <span class="credit-eyebrow">
                    {{ quota.has_plan_quota === false ? '账户可用余额' : '余额 / 积分兜底' }}
                  </span>
                  <span
                    class="pill-badge"
                    :class="quota.credit_fallback.active_deducting ? 'warn' : 'ok'"
                  >
                    {{ quota.credit_fallback.active_deducting ? '抵扣中' : '可用' }}
                  </span>
                </div>
                <div class="credit-hero-figure mono-num">
                  <span class="credit-amount">{{ quota.credit_fallback.balance }}</span>
                </div>
                <div v-if="quota.balance_breakdown" class="credit-split-grid">
                  <div v-if="quota.balance_breakdown.cash" class="split-tile">
                    <span class="split-label">现金</span>
                    <span class="split-val mono-num">{{ quota.balance_breakdown.cash }}</span>
                  </div>
                  <div v-if="quota.balance_breakdown.voucher" class="split-tile">
                    <span class="split-label">代金券</span>
                    <span class="split-val mono-num">{{ quota.balance_breakdown.voucher }}</span>
                  </div>
                </div>
              </div>
            </template>

            <div class="usage-updated mono-num">
              更新于 {{ new Date(quota.updated_at_ms).toTimeString().slice(0, 8) }}
              <template v-if="quota.keys && quota.keys.total > 1">
                · {{ quota.keys.total }} 把 Key
              </template>
            </div>
          </div>

          <div v-else-if="quotaError" class="usage-error-box">
            <div class="usage-error-text">{{ quotaError }}</div>
            <button type="button" class="btn" @click="refreshQuota">重试查询</button>
          </div>

          <div v-else class="usage-empty">正在查询 API Key 额度…</div>
        </template>

        <!-- Case C: Unconfigured -->
        <template v-else>
          <div class="usage-unconfigured">
            <div class="unconf-title">尚未配置可用凭据</div>
            <p class="unconf-desc">
              完成上方 OAuth 扫码授权或在插件配置中填写 API Key 后，即可实时查看全部积分与套餐用量。
            </p>
          </div>
        </template>
      </section>

      <!-- Section 3: Canvas Deliverable Assets -->
      <section class="panel-card">
        <div class="card-title-row">
          <span class="section-label">画布资产 ({{ documentState.nodes.length }})</span>
          <span class="mono-num seq-tag">seq #{{ documentState.changeSeq }}</span>
        </div>
        <div v-if="documentState.nodes.length" class="asset-list">
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
              {{ node.annotations.length }}
            </span>
          </button>
        </div>
        <div v-else class="usage-empty" style="padding: 6px 0">画布暂无资产节点</div>
      </section>
    </div>

    <!-- All Credits Detail Modal -->
    <CreditDetailsModal
      :open="creditModalOpen"
      :usage="accountUsage"
      :loading="isRefreshingQuota"
      :error="usageError"
      @close="creditModalOpen = false"
      @refresh="refreshAccountUsage"
    />

    <!-- Daily Sign-in Modal -->
    <SigninModal
      :open="signinModalOpen"
      :usage="accountUsage"
      :loading="isRefreshingQuota"
      @close="signinModalOpen = false"
      @refresh="refreshAccountUsage"
      @claimed="handleSigninClaimed"
    />
  </aside>
</template>

<style scoped>
.sidebar {
  width: 316px;
  min-width: 316px;
  height: 100%;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border-hairline);
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 15px 16px 13px;
  border-bottom: 1px solid var(--border-hairline);
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 11px;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  box-shadow: var(--shadow-xs);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.brand-text {
  min-width: 0;
  flex: 1;
}

.mobile-close-btn {
  display: none;
  padding: 6px;
  border-radius: var(--radius-xs);
  border: none;
  cursor: pointer;
}

.brand-title {
  font-size: 16.5px;
  color: var(--text-primary);
  line-height: 1.2;
}

.brand-caption {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 1px;
}

.brand-logo {
  width: 22px;
  height: 22px;
  border-radius: 5px;
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
  padding: 13px;
  box-shadow: var(--shadow-card);
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.01em;
}

.seq-tag {
  font-size: 11px;
  color: var(--text-tertiary);
}

.icon-btn {
  padding: 5px;
  border-radius: var(--radius-xs);
  border: none;
  cursor: pointer;
}

.auth-meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 10px;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
}

.meta-line {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.meta-key {
  color: var(--text-secondary);
}

.meta-val {
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  transition: border-color 0.14s var(--ease-out), background-color 0.14s var(--ease-out);
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
  font-size: 14.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--accent);
  background: var(--bg-surface);
  border: 1px dashed var(--accent-border);
  border-radius: var(--radius-xs);
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
  line-height: 1.45;
}

/* Optimized Credits Hero Surface */
.usage-content {
  display: flex;
  flex-direction: column;
}

.credit-surface {
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-canvas);
  border: 1px solid var(--border-hairline);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.credit-surface.compact {
  gap: 8px;
}

.credit-surface-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.credit-eyebrow {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-secondary);
}

.credit-hero-figure {
  display: flex;
  align-items: baseline;
  gap: 5px;
  line-height: 1.1;
}

.credit-amount {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-primary);
}

.credit-unit {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-tertiary);
}

.credit-split-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.split-tile {
  padding: 7px 9px;
  border-radius: var(--radius-xs);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.split-label {
  font-size: 11px;
  color: var(--text-tertiary);
}

.split-val {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.expiring-callout {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: var(--radius-xs);
  background: var(--status-warn-bg);
  border: 1px solid var(--status-warn-border);
  color: var(--status-warn);
  font-size: 11.5px;
  line-height: 1.35;
}

.credit-detail-trigger {
  width: 100%;
  justify-content: space-between;
  padding: 7px 10px;
  font-size: 12px;
  font-weight: 600;
}

.credit-detail-trigger svg,
.signin-strip .row-arrow {
  transition: transform 160ms var(--ease-out);
}

.credit-detail-trigger:hover svg,
.signin-strip:hover .row-arrow {
  transform: translateX(2.5px);
}

.signin-strip {
  width: 100%;
  margin-top: 9px;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
  cursor: pointer;
  transition:
    background-color 140ms var(--ease-out),
    border-color 140ms var(--ease-out),
    transform 120ms var(--ease-out);
}

.signin-top-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.signin-cycle-dots {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
  width: 100%;
}

.cycle-dot-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.cycle-dot-bar {
  width: 100%;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--border-strong);
}

.cycle-dot-cell.claimed .cycle-dot-bar {
  background: var(--status-ok);
}

.cycle-dot-cell.today .cycle-dot-bar {
  background: var(--accent);
}

.cycle-dot-day {
  font-size: 9.5px;
  color: var(--text-tertiary);
  line-height: 1;
}

.cycle-dot-cell.today .cycle-dot-day {
  color: var(--accent);
  font-weight: 700;
}

.cycle-dot-cell.claimed .cycle-dot-day {
  color: var(--status-ok);
}

.signin-strip:hover {
  background: var(--bg-surface-hover);
  border-color: var(--border-strong);
  transform: translateY(-0.5px);
}

.signin-strip:active {
  transform: scale(0.985);
}

.signin-right {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.signin-left {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.signin-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.signin-meta {
  font-size: 11px;
  color: var(--text-secondary);
}

/* Quota Rings & Bars */
.divider {
  height: 1px;
  background: var(--border-hairline);
  margin: 11px 0;
}

.quota-5h-box {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ring-wrap {
  position: relative;
  width: 58px;
  height: 58px;
  flex-shrink: 0;
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
  min-width: 0;
}

.quota-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.quota-numbers {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.quota-reset {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.weekly-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 6px;
}

.weekly-label {
  font-weight: 600;
  color: var(--text-primary);
}

.weekly-val {
  color: var(--text-secondary);
  font-size: 11.5px;
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
  transition: width 0.24s var(--ease-out);
}

.weekly-sub {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 5px;
}

/* Model remains */
.usage-models {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 10px;
}

.sub-section-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  margin-bottom: 1px;
}

.usage-model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  padding: 5px 9px;
  border-radius: var(--radius-xs);
  background: var(--bg-surface-muted);
}

.usage-model-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
}

.usage-model-val {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-primary);
  flex-shrink: 0;
}

.usage-model-val.ok {
  color: var(--status-ok);
}

.usage-model-val.danger {
  color: var(--status-danger);
}

.usage-model-val.muted {
  color: var(--text-tertiary);
  font-weight: 500;
}

.usage-updated {
  font-size: 10.5px;
  color: var(--text-tertiary);
  margin-top: 10px;
  text-align: right;
}

.usage-empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 12px 0;
  line-height: 1.5;
}

.usage-error-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border-radius: var(--radius-sm);
  background: var(--status-warn-bg);
  border: 1px solid var(--status-warn-border);
}

.usage-error-text {
  font-size: 12px;
  color: var(--status-warn);
  line-height: 1.45;
}

.usage-unconfigured {
  padding: 10px 11px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
}

.unconf-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.unconf-desc {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--text-secondary);
  line-height: 1.5;
}

/* Assets */
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
  border-radius: var(--radius-xs);
  border: 1px solid transparent;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s var(--ease-out);
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
  flex-shrink: 0;
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
    position: fixed;
    inset: 0 auto 0 0;
    width: min(332px, 88vw);
    min-width: 0;
    z-index: 220;
    box-shadow: var(--shadow-modal);
  }

  .mobile-close-btn {
    display: inline-flex;
  }
}
</style>
