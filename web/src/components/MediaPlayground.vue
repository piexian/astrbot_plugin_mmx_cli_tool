<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue';
import type { ImageSearchItem, MusicTrackItem, ReverseImageMatchItem } from '../types/mcode';
import {
  bridgeDownload,
  bridgeGet,
  bridgePost,
  bridgeUpload,
  copyToClipboard,
  pushToast,
  reportError,
  useBridge,
} from '../composables/useBridge';
import { useCanvas } from '../composables/useCanvas';
import { usePersistedRef } from '../composables/usePersistedRef';
import { useSlidingIndicator } from '../composables/useSlidingIndicator';
import { usePersistedTextareaSize } from '../composables/usePersistedTextareaSize';

const emit = defineEmits<{
  (e: 'switch-tab', tab: 'canvas' | 'playground' | 'logs'): void;
}>();

const { documentState, addFileNodeToCanvas } = useCanvas();
const { backendOnline } = useBridge();

const activeSubTab = ref<'search' | 'reverse' | 'music'>('search');
const subtabBarRef = ref<HTMLElement | null>(null);
const { indicatorStyle: subtabIndicatorStyle } = useSlidingIndicator(subtabBarRef, activeSubTab);

const audioKind = ref<'music' | 'sfx'>('music');
const audioModeRef = ref<HTMLElement | null>(null);
const { indicatorStyle: audioModeIndicatorStyle } = useSlidingIndicator(audioModeRef, [
  audioKind,
  activeSubTab,
]);

/**
 * Result list with an offline-preview side (demo seed + offline fallback results) and a
 * live side (real backend results). Only one side is visible at a time, so demo entries
 * never show up, or get mixed in, once the backend is online.
 */
function useResultList<T>(demoSeed: T[]) {
  const demo = shallowRef<T[]>(demoSeed);
  const live = shallowRef<T[]>([]);
  const items = computed(() => (backendOnline.value ? live : demo).value);
  // Evaluated after the bridge call: a successful call has already flipped backendOnline.
  const replace = (next: T[]) => {
    (backendOnline.value ? live : demo).value = next;
  };
  return { items, replace };
}

const NOT_CACHED_HINT = '演示样本或未缓存到本地的条目无法置入画布';
const MIME_BY_EXT: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  svg: 'image/svg+xml',
  mp3: 'audio/mpeg',
};

/**
 * Only files actually cached by the backend (local_path) can be referenced by the
 * canvas; demo samples and offline fallbacks carry no local_path on purpose.
 */
async function placeOnCanvas(
  localPath: string | undefined,
  kind: 'image' | 'audio',
  previewUrl: string,
): Promise<void> {
  if (!localPath) {
    pushToast(NOT_CACHED_HINT, 'warn');
    return;
  }
  const ext = localPath.split('.').pop()?.toLowerCase() || '';
  try {
    await addFileNodeToCanvas({
      fileName: localPath,
      relativePath: localPath,
      mimeType: MIME_BY_EXT[ext] || `${kind}/${ext}`,
      previewUrl,
    });
    emit('switch-tab', 'canvas');
  } catch (err) {
    reportError(err, '置入画布失败');
  }
}

function makeSampleSvgUrl(label: string, sub: string, hueHex: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="520" height="340" viewBox="0 0 520 340">
    <rect width="520" height="340" fill="#f6f5f0"/>
    <rect x="20" y="20" width="480" height="300" rx="12" fill="#ffffff" stroke="#e5e4dd"/>
    <circle cx="400" cy="130" r="68" fill="${hueHex}" opacity="0.18"/>
    <rect x="44" y="54" width="110" height="22" rx="11" fill="${hueHex}" opacity="0.15"/>
    <text x="99" y="69" font-family="monospace" font-size="10.5" fill="${hueHex}" text-anchor="middle">MATRIX SEARCH</text>
    <text x="44" y="132" font-family="Georgia, serif" font-size="22" fill="#262624" font-weight="600">${label}</text>
    <text x="44" y="164" font-family="sans-serif" font-size="13" fill="#6b6a63">${sub}</text>
    <text x="44" y="274" font-family="monospace" font-size="11" fill="#94928a">Delivered via /mavis/api/v1/mcp/images_search_and_download</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

/**
 * Generates a short, warm major-chord WAV data URL so the inline <audio> player
 * is genuinely playable even before the backend route is mounted.
 */
function createWarmChordWavDataUrl(durationSec = 2.4): string {
  const sampleRate = 22050;
  const numSamples = Math.floor(sampleRate * durationSec);
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);

  const freqs = [261.63, 329.63, 392.0, 523.25]; // C4 Major 7th warm pad
  for (let i = 0; i < numSamples; i++) {
    const t = i / sampleRate;
    const env = Math.sin((Math.PI * t) / durationSec);
    let sample = 0;
    for (const f of freqs) {
      sample += Math.sin(2 * Math.PI * f * t) * 0.18;
    }
    const pcm = Math.max(-1, Math.min(1, sample * env));
    view.setInt16(44 + i * 2, pcm * 32767, true);
  }

  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return `data:audio/wav;base64,${window.btoa(binary)}`;
}

/* =========================================================================
 * Sub-Tab 1: Image Search & Download
 * ========================================================================= */
const searchQuery = usePersistedRef('mcode:playground:search-query', '建筑极简主义 暖光材质');
const searchCount = usePersistedRef('mcode:playground:search-count', 3);
const isSearchingImages = ref(false);
const lightboxItem = ref<ImageSearchItem | null>(null);

const { items: searchResults, replace: setSearchResults } = useResultList<ImageSearchItem>([
  {
    id: 'img_res_1',
    title: 'Warm Minimalist Concrete & Travertine Courtyard',
    image_url: makeSampleSvgUrl('Travertine Courtyard', 'Architectural Photography · 1600×1060', '#cc5a2b'),
    source_domain: 'archdaily.com',
    width: 1600,
    height: 1060,
  },
  {
    id: 'img_res_2',
    title: 'Nordic Warm Oak & Parchment Interior Study',
    image_url: makeSampleSvgUrl('Warm Oak Study', 'Interior Material Palette · 1440×960', '#26734d'),
    source_domain: 'dezeen.com',
    width: 1440,
    height: 960,
  },
  {
    id: 'img_res_3',
    title: 'Terracotta Ceramic Vessel Still Life',
    image_url: makeSampleSvgUrl('Terracotta Still Life', 'Editorial Lighting · 1280×960', '#b4690e'),
    source_domain: 'ignant.com',
    width: 1280,
    height: 960,
  },
]);

async function handleImageSearch(): Promise<void> {
  if (!searchQuery.value.trim()) {
    pushToast('请输入图片搜索关键词', 'warn');
    return;
  }
  isSearchingImages.value = true;
  try {
    const res = await bridgePost<{ items: ImageSearchItem[] }>(
      'tools/image-search',
      {
        query: searchQuery.value.trim(),
        count: searchCount.value,
      },
      () => {
        const q = searchQuery.value.trim();
        const palettes = ['#cc5a2b', '#26734d', '#b4690e', '#52637a', '#8c4a6b'];
        const items: ImageSearchItem[] = Array.from({ length: searchCount.value }, (_, idx) => ({
          id: `search_${Date.now()}_${idx}`,
          title: `${q} · 检索样本 #${idx + 1}`,
          image_url: makeSampleSvgUrl(`${q} #${idx + 1}`, 'Matrix MCP Search Result', palettes[idx % palettes.length]),
          source_domain: 'cdn.hailuo.ai',
          width: 1440,
          height: 960,
        }));
        return { items };
      },
    );
    setSearchResults(res.items || []);
    pushToast(`已检索并缓存 ${searchResults.value.length} 张图片`, 'ok');
  } catch (err) {
    reportError(err, '图片检索失败');
  } finally {
    isSearchingImages.value = false;
  }
}

async function sendImageToCanvas(item: ImageSearchItem): Promise<void> {
  await placeOnCanvas(item.local_path, 'image', item.image_url);
}

/* =========================================================================
 * Sub-Tab 2: Reverse Image Search (image_reverse_search)
 * ========================================================================= */
const reverseSourceUrl = ref('');
const isReversing = ref(false);
const { items: reverseMatches, replace: setReverseMatches } = useResultList<ReverseImageMatchItem>([
  {
    title: 'Anthropic Editorial Design System & Warm Typography Guidelines',
    link: 'https://www.anthropic.com/design/editorial-surfaces',
    source: 'anthropic.com',
    snippet: 'Explores warm parchment backgrounds (#FBFBF9), hairline borders, and human-ist serif headings for AI workspaces.',
  },
  {
    title: 'MiniMax Code 3.1.4 Official Release Notes & Matrix Agent Tools',
    link: 'https://platform.minimax.cn/docs/m-plan/usage-rules',
    source: 'platform.minimax.cn',
    snippet: 'Details M-Plan 2.0 dual-cycle quota (5-hour interaction limit + weekly video quota) and Matrix MCP tool endpoints.',
  },
  {
    title: 'Canvas Document V1 Specification — Normalized Region Annotations',
    link: 'https://agent.minimax.cn/docs/canvas-annotations',
    source: 'agent.minimax.cn',
    snippet: 'Defines CanvasFileNodeV1 and CanvasImageAnnotationV1 for interactive multi-modal agent feedback loops.',
  },
]);

async function handleReverseSearch(): Promise<void> {
  if (!reverseSourceUrl.value.trim()) {
    pushToast('请选择画布图片节点或输入图片路径', 'warn');
    return;
  }
  isReversing.value = true;
  try {
    const res = await bridgePost<{ matches: ReverseImageMatchItem[] }>(
      'tools/image-reverse',
      { image: reverseSourceUrl.value.trim() },
      () => ({
        matches: [
          {
            title: `溯源匹配：${reverseSourceUrl.value.split('/').pop()} 原始设计稿与高清图源`,
            link: `https://cdn.hailuo.ai/source/${encodeURIComponent(reverseSourceUrl.value.split('/').pop() || 'image')}`,
            source: 'cdn.hailuo.ai',
            snippet: '匹配度 96.4% · 分辨率 2048×1280 · 包含原始图层元数据与色彩配置描述。',
          },
          ...reverseMatches.value,
        ],
      }),
    );
    setReverseMatches(res.matches || []);
    pushToast(`以图搜图完成，找到 ${reverseMatches.value.length} 条来源出处`, 'ok');
  } catch (err) {
    reportError(err, '以图搜图失败');
  } finally {
    isReversing.value = false;
  }
}

async function handleReverseFileSelect(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  reverseSourceUrl.value = file.name;
  isReversing.value = true;
  try {
    const res = await bridgeUpload<{ matches: ReverseImageMatchItem[] }>(
      'tools/image-reverse-upload',
      file,
      () => ({
        matches: [
          {
            title: `${file.name} · 视觉同源出处与高分辨率原图`,
            link: `https://platform.minimax.cn/reverse-match/${encodeURIComponent(file.name)}`,
            source: 'platform.minimax.cn',
            snippet: `本地上传文件 (${Math.round(file.size / 1024)} KB) 通过 get_upload_url / Base64 内联完成特征比对。`,
          },
          ...reverseMatches.value.slice(0, 2),
        ],
      }),
    );
    setReverseMatches(res.matches || []);
    pushToast(`已完成上传图片「${file.name}」的溯源检索`, 'ok');
  } catch (err) {
    reportError(err, '上传图片溯源失败');
  } finally {
    isReversing.value = false;
  }
}

/* =========================================================================
 * Sub-Tab 3: AI Music & Sound Effects (batch_text_to_music / batch_text_to_audio)
 * ========================================================================= */
const musicPrompt = usePersistedRef(
  'mcode:playground:music-prompt',
  '温暖的独立民谣原声吉他与轻柔钢琴，午后阳光氛围，舒缓治愈',
);
const musicLyrics = usePersistedRef(
  'mcode:playground:music-lyrics',
  '[Verse]\n暖风吹过羊皮纸的页角\n一行行代码在光影里跳动\n[Chorus]\n我们在无垠的画布上勾勒未来',
);
const musicFormat = usePersistedRef<'mp3' | 'wav'>('mcode:playground:music-format', 'mp3');
const musicLyricsRef = ref<HTMLTextAreaElement | null>(null);
const audioHistoryOpen = usePersistedRef('mcode:playground:audio-history-open', true);
const audioHistoryFilter = ref<'all' | 'music' | 'sfx'>('all');
const isGeneratingAudio = ref(false);
const isLoadingAudioHistory = ref(false);

usePersistedTextareaSize(musicLyricsRef, 'mcode:playground:music-lyrics-height', {
  defaultHeight: 164,
  minHeight: 88,
  maxHeight: 340,
  maxViewportRatio: 0.36,
});

const DEMO_AUDIO_SEED: MusicTrackItem[] = [
  {
    id: 'track_demo_01',
    kind: 'music',
    prompt: '温暖的独立民谣原声吉他与轻柔钢琴，午后阳光氛围',
    lyrics: '[Verse] 暖风吹过羊皮纸的页角...',
    format: 'wav',
    output_url: createWarmChordWavDataUrl(2.6),
    created_at_ms: Date.now() - 120000,
    duration_hint: '00:03 (预览和弦)',
  },
];

const demoAudioHistory = usePersistedRef<MusicTrackItem[]>(
  'mcode:playground:audio-demo-history',
  DEMO_AUDIO_SEED,
  { syncBackend: false },
);
const liveAudioHistory = shallowRef<MusicTrackItem[]>([]);

const generatedTracks = computed(() =>
  backendOnline.value ? liveAudioHistory.value : demoAudioHistory.value,
);

const filteredTracks = computed(() => {
  if (audioHistoryFilter.value === 'all') return generatedTracks.value;
  return generatedTracks.value.filter((t) => t.kind === audioHistoryFilter.value);
});

function setGeneratedTracks(next: MusicTrackItem[]): void {
  if (backendOnline.value) {
    liveAudioHistory.value = next;
  } else {
    demoAudioHistory.value = next.slice(0, 30);
  }
}

async function loadAudioHistory(): Promise<void> {
  isLoadingAudioHistory.value = true;
  try {
    const res = await bridgeGet<{ items: MusicTrackItem[] }>(
      'tools/audio-history',
      undefined,
      () => ({ items: demoAudioHistory.value }),
    );
    if (res && Array.isArray(res.items)) {
      if (backendOnline.value) {
        liveAudioHistory.value = res.items;
      } else if (!demoAudioHistory.value.length && res.items.length) {
        demoAudioHistory.value = res.items;
      }
    }
  } catch {
    // Keep existing list on transient error
  } finally {
    isLoadingAudioHistory.value = false;
  }
}

async function handleGenerateAudio(): Promise<void> {
  if (musicPrompt.value.trim().length < 4) {
    pushToast('请输入更具体的音乐或音效描述词', 'warn');
    return;
  }
  isGeneratingAudio.value = true;
  try {
    const endpoint = audioKind.value === 'music' ? 'tools/music' : 'tools/sfx';
    const res = await bridgePost<MusicTrackItem>(
      endpoint,
      {
        kind: audioKind.value,
        prompt: musicPrompt.value.trim(),
        lyrics: audioKind.value === 'music' ? musicLyrics.value.trim() : undefined,
        format: musicFormat.value,
      },
      () => ({
        id: `track_${Date.now()}`,
        kind: audioKind.value,
        prompt: musicPrompt.value.trim(),
        lyrics: audioKind.value === 'music' ? musicLyrics.value.trim() : undefined,
        format: musicFormat.value,
        output_url: createWarmChordWavDataUrl(2.8),
        created_at_ms: Date.now(),
        duration_hint: audioKind.value === 'music' ? 'AI 编曲合成' : 'AI 拟音音效',
      }),
    );
    setGeneratedTracks([res, ...generatedTracks.value.filter((item) => item.id !== res.id)]);
    audioHistoryOpen.value = true;
    pushToast(audioKind.value === 'music' ? 'AI 音乐生成完成，已写入记录' : 'AI 音效生成完成，已写入记录', 'ok');
  } catch (err) {
    reportError(err, audioKind.value === 'music' ? 'AI 音乐生成失败' : 'AI 音效生成失败');
  } finally {
    isGeneratingAudio.value = false;
  }
}

async function handleDeleteTrack(trackId: string): Promise<void> {
  try {
    const res = await bridgePost<{ ok: boolean; items: MusicTrackItem[] }>(
      'tools/audio-history/delete',
      { id: trackId },
      () => ({ ok: true, items: generatedTracks.value.filter((t) => t.id !== trackId) }),
    );
    setGeneratedTracks(Array.isArray(res?.items) ? res.items : generatedTracks.value.filter((t) => t.id !== trackId));
    pushToast('已删除该条生成记录', 'info', 1800);
  } catch (err) {
    reportError(err, '删除生成记录失败');
  }
}

async function handleClearAudioHistory(): Promise<void> {
  if (!generatedTracks.value.length) return;
  try {
    await bridgePost('tools/audio-history/delete', { clear: true }, () => ({ ok: true, items: [] }));
    setGeneratedTracks([]);
    pushToast('已清空音频生成记录', 'info', 2000);
  } catch (err) {
    reportError(err, '清空生成记录失败');
  }
}

function reuseTrackPreset(track: MusicTrackItem): void {
  audioKind.value = track.kind;
  musicPrompt.value = track.prompt;
  if (track.kind === 'music' && track.lyrics !== undefined) {
    musicLyrics.value = track.lyrics;
  }
  musicFormat.value = track.format;
  pushToast('已回填该记录的创作参数', 'ok', 1800);
}

function formatTrackTime(ms: number): string {
  if (!ms || !Number.isFinite(ms)) return '';
  const d = new Date(ms);
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${m}-${day} ${hh}:${mm}`;
}

async function sendAudioToCanvas(track: MusicTrackItem): Promise<void> {
  await placeOnCanvas(track.local_path, 'audio', track.output_url);
}

onMounted(() => {
  loadAudioHistory();
});
</script>

<template>
  <div class="playground-root">
    <!-- Top Sub-navigation (Centered Tab Bar with Sliding Indicator) -->
    <header class="playground-header">
      <div class="header-title-slot">
        <h2 class="serif-heading pg-title">多模态试验台</h2>
      </div>
      <div ref="subtabBarRef" class="subtab-bar sliding-track" role="tablist" aria-label="多模态功能切换">
        <span class="sliding-pill" :style="subtabIndicatorStyle()" aria-hidden="true"></span>
        <button
          type="button"
          class="subtab-btn"
          :class="{ active: activeSubTab === 'search' }"
          @click="activeSubTab = 'search'"
        >
          网络搜图并下载
        </button>
        <button
          type="button"
          class="subtab-btn"
          :class="{ active: activeSubTab === 'reverse' }"
          @click="activeSubTab = 'reverse'"
        >
          以图搜图识源
        </button>
        <button
          type="button"
          class="subtab-btn"
          :class="{ active: activeSubTab === 'music' }"
          @click="activeSubTab = 'music'"
        >
          AI 音乐与音效创作
        </button>
      </div>
      <div class="header-right-spacer" aria-hidden="true"></div>
    </header>

    <div class="playground-body">
      <Transition name="subtab-fade" mode="out-in">
      <!-- 1. Image Search & Download -->
      <section v-if="activeSubTab === 'search'" key="search" class="pg-panel">
        <div class="control-bar-card">
          <div class="field-grow">
            <label class="field-label">检索关键词</label>
            <input
              v-model="searchQuery"
              type="text"
              class="input-control"
              placeholder="输入需要搜索并下载的高清图片主题..."
              @keyup.enter="handleImageSearch"
            />
          </div>
          <div class="field-narrow">
            <label class="field-label">数量</label>
            <select v-model.number="searchCount" class="input-control">
              <option :value="2">2 张</option>
              <option :value="3">3 张</option>
              <option :value="4">4 张</option>
              <option :value="5">5 张</option>
            </select>
          </div>
          <div class="field-action">
            <button
              type="button"
              class="btn btn-primary"
              :disabled="isSearchingImages"
              @click="handleImageSearch"
            >
              {{ isSearchingImages ? '检索下载中...' : '搜索并缓存至本地' }}
            </button>
          </div>
        </div>

        <div class="image-grid">
          <article v-for="item in searchResults" :key="item.id" class="image-card">
            <div class="image-thumb-wrap" @click="lightboxItem = item">
              <img :src="item.image_url" :alt="item.title" class="image-thumb" />
              <span class="zoom-badge">点击放大</span>
            </div>
            <div class="image-card-body">
              <div class="image-card-title">{{ item.title }}</div>
              <div class="image-card-meta mono-num">
                <span>{{ item.source_domain || 'matrix.cdn' }}</span>
                <span>{{ item.width || 1440 }}×{{ item.height || 960 }}</span>
              </div>
              <div class="image-card-actions">
                <button
                  type="button"
                  class="btn btn-primary"
                  style="flex: 1"
                  :disabled="!item.local_path"
                  :title="item.local_path ? undefined : NOT_CACHED_HINT"
                  @click="sendImageToCanvas(item)"
                >
                  置入无限画布
                </button>
                <button
                  type="button"
                  class="btn"
                  @click="
                    bridgeDownload(
                      'tools/download-temp',
                      { path: item.local_path },
                      `${item.id}.png`,
                      item.image_url,
                    )
                  "
                >
                  下载
                </button>
              </div>
            </div>
          </article>
        </div>
        <div v-if="!searchResults.length" class="pg-empty">暂无检索结果：输入关键词后点击「搜索并缓存至本地」</div>
      </section>

      <!-- 2. Reverse Image Search (Sandbox-Safe Source Cards) -->
      <section v-else-if="activeSubTab === 'reverse'" key="reverse" class="pg-panel">
        <div class="control-bar-card">
          <div class="field-grow">
            <label class="field-label">选择画布已有图片或输入路径</label>
            <div class="reverse-input-group">
              <input
                v-model="reverseSourceUrl"
                type="text"
                class="input-control"
                placeholder="输入图片路径或公网 URL，或从右侧选择画布节点..."
              />
              <select
                class="input-control"
                style="max-width: 220px"
                @change="reverseSourceUrl = ($event.target as HTMLSelectElement).value"
              >
                <option value="">-- 从画布节点选择 --</option>
                <option
                  v-for="n in documentState.nodes"
                  :key="n.id"
                  :value="n.file.relativePath"
                >
                  {{ n.file.fileName || n.file.relativePath }}
                </option>
              </select>
            </div>
          </div>
          <div class="field-action">
            <label class="btn" style="cursor: pointer">
              上传本地图片
              <input type="file" accept="image/*" style="display: none" @change="handleReverseFileSelect" />
            </label>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="isReversing"
              @click="handleReverseSearch"
            >
              {{ isReversing ? '识图溯源中...' : '开始以图搜图' }}
            </button>
          </div>
        </div>

        <div class="reverse-notice">
          提示：因 AstrBot 插件页面运行于无外链弹窗的沙箱环境，点击下方卡片右侧按钮可直接复制来源链接。
        </div>

        <div class="source-card-list">
          <div v-for="(match, idx) in reverseMatches" :key="idx" class="source-card">
            <div class="source-rank mono-num">#{{ idx + 1 }}</div>
            <div class="source-main">
              <div class="source-top">
                <span class="pill-badge accent mono-num">{{ match.source }}</span>
                <span class="source-title">{{ match.title }}</span>
              </div>
              <p v-if="match.snippet" class="source-snippet">{{ match.snippet }}</p>
              <div class="source-url mono-num">{{ match.link }}</div>
            </div>
            <div class="source-side">
              <button
                type="button"
                class="btn"
                @click="copyToClipboard(match.link, '来源网页链接')"
              >
                复制来源链接
              </button>
            </div>
          </div>
        </div>
        <div v-if="!reverseMatches.length" class="pg-empty">暂无溯源结果：选择画布图片或上传本地图片后开始以图搜图</div>
      </section>

      <!-- 3. AI Music & Sound Effects Studio (Left Composer + Right Collapsible Persistent History) -->
      <section v-else key="music" class="music-studio-shell">
        <div class="music-composer-col">
          <div class="music-composer-card">
            <div class="composer-row">
              <div ref="audioModeRef" class="mode-toggle sliding-track">
                <span class="sliding-pill" :style="audioModeIndicatorStyle()" aria-hidden="true"></span>
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ active: audioKind === 'music' }"
                  @click="audioKind = 'music'"
                >
                  AI 编曲音乐
                </button>
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ active: audioKind === 'sfx' }"
                  @click="audioKind = 'sfx'"
                >
                  AI 拟音音效
                </button>
              </div>

              <div class="composer-right-controls">
                <div class="format-select">
                  <span class="field-label" style="margin: 0">格式</span>
                  <select v-model="musicFormat" class="input-control" style="width: 86px">
                    <option value="mp3">MP3</option>
                    <option value="wav">WAV</option>
                  </select>
                </div>

                <button
                  type="button"
                  class="btn history-toggle-btn"
                  :class="{ 'btn-soft': !audioHistoryOpen && generatedTracks.length > 0 }"
                  :title="audioHistoryOpen ? '收起右侧生成记录列表' : '展开右侧生成记录列表'"
                  @click="audioHistoryOpen = !audioHistoryOpen"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="3" width="18" height="18" rx="2.5" />
                    <line x1="15" y1="3" x2="15" y2="21" />
                  </svg>
                  <span>{{ audioHistoryOpen ? '收起记录' : `生成记录 (${generatedTracks.length})` }}</span>
                </button>
              </div>
            </div>

            <div class="composer-fields-scroll">
              <div class="composer-field-block">
                <label class="field-label">
                  {{ audioKind === 'music' ? '曲风、配器与情绪描述' : '音效场景描述' }}
                </label>
                <input
                  v-model="musicPrompt"
                  type="text"
                  class="input-control"
                  placeholder="例如：温暖的独立民谣吉他，雨夜咖啡馆爵士钢琴..."
                />
              </div>

              <div v-if="audioKind === 'music'" class="composer-field-block">
                <div class="lyrics-label-row">
                  <label class="field-label" style="margin: 0">
                    歌词结构 (可选，支持 [Verse] / [Chorus] 标记)
                  </label>
                  <span class="resize-hint">可拖拽右下角调整高度（自动记忆）</span>
                </div>
                <textarea
                  ref="musicLyricsRef"
                  v-model="musicLyrics"
                  class="input-control mono-num lyrics-textarea"
                  placeholder="[Verse] 输入自定义歌词，留空则生成纯音乐伴奏..."
                ></textarea>
              </div>
            </div>

            <div class="composer-footer">
              <span class="hint-text">生成音频会自动落盘并持久化至右侧生成记录列表</span>
              <button
                type="button"
                class="btn btn-primary"
                :disabled="isGeneratingAudio"
                @click="handleGenerateAudio"
              >
                {{ isGeneratingAudio ? 'Matrix 音频合成中...' : audioKind === 'music' ? '生成 AI 音乐' : '生成 AI 音效' }}
              </button>
            </div>
          </div>
        </div>

        <!-- Right Collapsible Persistent Audio History List -->
        <Transition name="history-slide">
          <aside v-if="audioHistoryOpen" class="music-history-aside" aria-label="AI 音频生成历史">
            <div class="history-header">
              <div class="history-title-wrap">
                <span class="history-title">生成记录</span>
                <span class="pill-badge mono-num">{{ generatedTracks.length }}</span>
              </div>
              <div class="history-header-actions">
                <button
                  type="button"
                  class="btn-ghost history-icon-btn"
                  title="刷新持久化记录"
                  :disabled="isLoadingAudioHistory"
                  @click="loadAudioHistory"
                >
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    :class="{ 'is-spinning': isLoadingAudioHistory }"
                  >
                    <path d="M21 2v6h-6" />
                    <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                    <path d="M3 22v-6h6" />
                    <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                  </svg>
                </button>
                <button
                  v-if="generatedTracks.length"
                  type="button"
                  class="btn-ghost history-text-btn"
                  @click="handleClearAudioHistory"
                >
                  清空
                </button>
                <button
                  type="button"
                  class="btn-ghost history-icon-btn"
                  title="收起记录面板"
                  aria-label="收起记录面板"
                  @click="audioHistoryOpen = false"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="history-filter-bar">
              <button
                type="button"
                class="mini-filter-chip"
                :class="{ active: audioHistoryFilter === 'all' }"
                @click="audioHistoryFilter = 'all'"
              >
                全部
              </button>
              <button
                type="button"
                class="mini-filter-chip"
                :class="{ active: audioHistoryFilter === 'music' }"
                @click="audioHistoryFilter = 'music'"
              >
                音乐
              </button>
              <button
                type="button"
                class="mini-filter-chip"
                :class="{ active: audioHistoryFilter === 'sfx' }"
                @click="audioHistoryFilter = 'sfx'"
              >
                音效
              </button>
            </div>

            <div class="history-list-scroll">
              <div v-if="filteredTracks.length" class="track-list">
                <article v-for="track in filteredTracks" :key="track.id" class="track-card">
                  <div class="track-card-top">
                    <span class="pill-badge accent mono-num">
                      {{ track.kind === 'music' ? 'MUSIC' : 'SFX' }} · {{ track.format.toUpperCase() }}
                    </span>
                    <span class="track-time mono-num">{{ formatTrackTime(track.created_at_ms) }}</span>
                  </div>

                  <div class="track-prompt" :title="track.prompt">{{ track.prompt }}</div>
                  <div v-if="track.lyrics" class="track-lyrics mono-num">{{ track.lyrics }}</div>

                  <audio :src="track.output_url" controls class="track-audio"></audio>

                  <div class="track-actions-row">
                    <button
                      type="button"
                      class="btn btn-mini"
                      @click="reuseTrackPreset(track)"
                    >
                      复用参数
                    </button>
                    <button
                      type="button"
                      class="btn btn-mini"
                      :disabled="!track.local_path"
                      :title="track.local_path ? undefined : NOT_CACHED_HINT"
                      @click="sendAudioToCanvas(track)"
                    >
                      置入画布
                    </button>
                    <button
                      type="button"
                      class="btn btn-mini"
                      @click="
                        bridgeDownload(
                          'tools/download-temp',
                          { path: track.local_path },
                          `${track.id}.${track.format}`,
                          track.output_url,
                        )
                      "
                    >
                      下载
                    </button>
                    <button
                      type="button"
                      class="btn-ghost track-del-btn"
                      title="删除此记录"
                      aria-label="删除此记录"
                      @click="handleDeleteTrack(track.id)"
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                        <path d="M10 11v6M14 11v6" />
                      </svg>
                    </button>
                  </div>
                </article>
              </div>

              <div v-else class="history-empty">
                {{
                  generatedTracks.length
                    ? '当前分类暂无记录'
                    : '暂无持久化生成记录，点击左侧生成按钮即可自动保存至此列表'
                }}
              </div>
            </div>
          </aside>
        </Transition>
      </section>
      </Transition>
    </div>

    <!-- Inline Image Lightbox Modal (Top-right X only) -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div v-if="lightboxItem" class="lightbox-backdrop" @click="lightboxItem = null">
          <div class="lightbox-dialog" @click.stop>
            <button
              type="button"
              class="lightbox-close-btn"
              title="关闭预览"
              aria-label="关闭预览"
              @click="lightboxItem = null"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
            <img :src="lightboxItem.image_url" :alt="lightboxItem.title" class="lightbox-img" />
            <div class="lightbox-bar">
              <div>
                <div class="lightbox-title">{{ lightboxItem.title }}</div>
                <div class="lightbox-sub mono-num">{{ lightboxItem.local_path || '未缓存到本地' }}</div>
              </div>
              <button
                type="button"
                class="btn btn-primary"
                :disabled="!lightboxItem.local_path"
                :title="lightboxItem.local_path ? undefined : NOT_CACHED_HINT"
                @click="sendImageToCanvas(lightboxItem); lightboxItem = null"
              >
                置入无限画布
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.playground-root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.playground-header {
  padding: 14px 22px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 16px;
}

.header-title-slot {
  min-width: 0;
}

.header-right-spacer {
  min-width: 0;
}

.pg-title {
  margin: 0;
  font-size: 18px;
}

.subtab-bar {
  display: inline-flex;
  align-items: center;
  justify-self: center;
  padding: 3px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
  gap: 2px;
}

.subtab-btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: color 160ms var(--ease-out), transform 120ms var(--ease-out);
}

.subtab-btn:hover {
  color: var(--text-primary);
}

.subtab-btn:active {
  transform: scale(0.97);
}

.subtab-btn.active {
  color: var(--accent);
  font-weight: 600;
}

@media (max-width: 860px) {
  .playground-header {
    grid-template-columns: 1fr;
    justify-items: center;
    gap: 10px;
    padding: 12px 14px;
  }

  .header-right-spacer {
    display: none;
  }

  .subtab-bar {
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .subtab-bar::-webkit-scrollbar {
    display: none;
  }
}

.playground-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 22px;
}

.pg-panel {
  max-width: 1080px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.control-bar-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  display: flex;
  align-items: flex-end;
  gap: 12px;
  box-shadow: var(--shadow-card);
}

.field-grow {
  flex: 1;
}

.field-narrow {
  width: 96px;
}

.field-action {
  display: flex;
  gap: 8px;
}

.field-label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.reverse-input-group {
  display: flex;
  gap: 8px;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 16px;
}

.image-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  overflow: hidden;
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
}

.image-thumb-wrap {
  position: relative;
  height: 185px;
  background: var(--bg-surface-muted);
  cursor: zoom-in;
  overflow: hidden;
}

.image-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.2s ease;
}

.image-thumb-wrap:hover .image-thumb {
  transform: scale(1.02);
}

.zoom-badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: rgba(24, 24, 22, 0.68);
  color: #ffffff;
  font-size: 11px;
}

.image-card-body {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.image-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.image-card-meta {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.image-card-actions {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}

.pg-empty {
  padding: 28px 16px;
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
}

.reverse-notice {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
}

.source-card-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.source-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: var(--shadow-card);
}

.source-rank {
  font-size: 15px;
  font-weight: 700;
  color: var(--accent);
  min-width: 28px;
}

.source-main {
  flex: 1;
  min-width: 0;
}

.source-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.source-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.source-snippet {
  margin: 4px 0;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.source-url {
  font-size: 11.5px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.music-studio-shell {
  max-width: 1240px;
  width: 100%;
  height: 100%;
  min-height: 0;
  margin: 0 auto;
  display: flex;
  gap: 16px;
  align-items: stretch;
  overflow: hidden;
}

.music-composer-col {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.music-composer-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: var(--shadow-card);
  max-height: 100%;
  min-height: 0;
  overflow: hidden;
}

.composer-fields-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-right: 2px;
}

.composer-field-block {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex-shrink: 0;
}

.lyrics-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.resize-hint {
  font-size: 11px;
  color: var(--text-tertiary);
}

.lyrics-textarea {
  width: 100%;
  max-width: 100%;
  min-height: 88px;
  max-height: min(36vh, 340px);
  flex-shrink: 0;
  resize: vertical;
  overflow-y: auto;
}

.composer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.composer-right-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.history-toggle-btn {
  padding: 5px 10px;
  font-size: 12px;
}

.mode-toggle {
  display: inline-flex;
  padding: 3px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
  gap: 2px;
}

.seg-btn {
  padding: 5px 11px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: color 160ms var(--ease-out), transform 120ms var(--ease-out);
}

.seg-btn:hover {
  color: var(--text-primary);
}

.seg-btn:active {
  transform: scale(0.97);
}

.seg-btn.active {
  color: var(--accent);
  font-weight: 600;
}

.format-select {
  display: flex;
  align-items: center;
  gap: 6px;
}

.composer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
  padding-top: 8px;
  border-top: 1px solid var(--border-hairline);
}

.hint-text {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

/* Right Collapsible History Sidebar */
.music-history-aside {
  width: 356px;
  min-width: 320px;
  max-width: 42%;
  max-height: 100%;
  min-height: 0;
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.history-header {
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-shrink: 0;
}

.history-title-wrap {
  display: flex;
  align-items: center;
  gap: 7px;
}

.history-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.history-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.history-icon-btn {
  padding: 5px;
  border-radius: var(--radius-xs);
  border: none;
  cursor: pointer;
}

.history-text-btn {
  padding: 4px 7px;
  font-size: 11.5px;
  border: none;
  border-radius: var(--radius-xs);
  cursor: pointer;
}

.history-filter-bar {
  padding: 8px 14px;
  background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.mini-filter-chip {
  padding: 3px 10px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 140ms var(--ease-out);
}

.mini-filter-chip:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.mini-filter-chip.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.history-list-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
}

.track-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.track-card {
  background: var(--bg-canvas);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-sm);
  padding: 11px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color 140ms var(--ease-out);
}

.track-card:hover {
  border-color: var(--border-strong);
}

.track-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.track-time {
  font-size: 11px;
  color: var(--text-tertiary);
}

.track-prompt {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.track-lyrics {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: pre-line;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.track-audio {
  width: 100%;
  height: 34px;
}

.track-actions-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.btn-mini {
  padding: 4px 8px;
  font-size: 11.5px;
}

.track-del-btn {
  margin-left: auto;
  padding: 4px 6px;
  border-radius: var(--radius-xs);
  border: none;
  cursor: pointer;
  color: var(--text-tertiary);
}

.track-del-btn:hover {
  color: var(--status-danger);
  background: var(--status-danger-bg);
}

.history-empty {
  padding: 36px 12px;
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.history-slide-enter-active,
.history-slide-leave-active {
  transition: opacity 180ms var(--ease-out), transform 200ms var(--ease-out);
}

.history-slide-enter-from,
.history-slide-leave-to {
  opacity: 0;
  transform: translateX(12px);
}

@media (max-width: 980px) {
  .music-studio-shell {
    flex-direction: column;
    height: auto;
    overflow: visible;
  }

  .music-composer-col,
  .music-composer-card {
    max-height: none;
    overflow: visible;
  }

  .music-history-aside {
    width: 100%;
    min-width: 0;
    max-width: 100%;
    max-height: 440px;
  }
}

.lightbox-backdrop {
  position: fixed;
  inset: 0;
  background: var(--bg-backdrop);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 260;
  padding: 24px;
}

.lightbox-dialog {
  position: relative;
  max-width: 820px;
  width: 100%;
  background: var(--bg-surface);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-strong);
  overflow: hidden;
  box-shadow: var(--shadow-modal);
  transition: transform 200ms var(--ease-out), opacity 170ms var(--ease-out);
}

.modal-fade-enter-from .lightbox-dialog,
.modal-fade-leave-to .lightbox-dialog {
  opacity: 0;
  transform: scale(0.96) translateY(8px);
}

.lightbox-close-btn {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 2;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-strong);
  background: var(--bg-elevated);
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: var(--shadow-xs);
  transition: background-color 140ms var(--ease-out), transform 120ms var(--ease-out);
}

.lightbox-close-btn:hover {
  background: var(--bg-surface-hover);
}

.lightbox-close-btn:active {
  transform: scale(0.93);
}

.lightbox-img {
  width: 100%;
  max-height: 68vh;
  object-fit: contain;
  background: var(--bg-surface-muted);
  display: block;
}

.lightbox-bar {
  padding: 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border-hairline);
}

.lightbox-title {
  font-size: 14px;
  font-weight: 600;
}

.lightbox-sub {
  font-size: 11.5px;
  color: var(--text-tertiary);
}
</style>
