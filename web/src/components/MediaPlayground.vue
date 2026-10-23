<script setup lang="ts">
import { computed, ref, shallowRef } from 'vue';
import type { ImageSearchItem, MusicTrackItem, ReverseImageMatchItem } from '../types/mcode';
import {
  bridgeDownload,
  bridgePost,
  bridgeUpload,
  copyToClipboard,
  pushToast,
  reportError,
  useBridge,
} from '../composables/useBridge';
import { useCanvas } from '../composables/useCanvas';
import { usePersistedRef } from '../composables/usePersistedRef';

const emit = defineEmits<{
  (e: 'switch-tab', tab: 'canvas' | 'playground' | 'plan'): void;
}>();

const { documentState, addFileNodeToCanvas } = useCanvas();
const { backendOnline } = useBridge();

const activeSubTab = ref<'search' | 'reverse' | 'music'>('search');

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
const audioKind = ref<'music' | 'sfx'>('music');
const musicPrompt = ref('温暖的独立民谣原声吉他与轻柔钢琴，午后阳光氛围，舒缓治愈');
const musicLyrics = ref('[Verse]\n暖风吹过羊皮纸的页角\n一行行代码在光影里跳动\n[Chorus]\n我们在无垠的画布上勾勒未来');
const musicFormat = ref<'mp3' | 'wav'>('mp3');
const isGeneratingAudio = ref(false);

const { items: generatedTracks, replace: setGeneratedTracks } = useResultList<MusicTrackItem>([
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
]);

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
    setGeneratedTracks([res, ...generatedTracks.value]);
    pushToast(audioKind.value === 'music' ? 'AI 音乐生成完成' : 'AI 音效生成完成', 'ok');
  } catch (err) {
    reportError(err, audioKind.value === 'music' ? 'AI 音乐生成失败' : 'AI 音效生成失败');
  } finally {
    isGeneratingAudio.value = false;
  }
}

async function sendAudioToCanvas(track: MusicTrackItem): Promise<void> {
  await placeOnCanvas(track.local_path, 'audio', track.output_url);
}
</script>

<template>
  <div class="playground-root">
    <!-- Top Sub-navigation -->
    <header class="playground-header">
      <div>
        <h2 class="serif-heading pg-title">多模态 Matrix 工具试验台</h2>
      </div>
      <div class="subtab-bar">
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
    </header>

    <div class="playground-body">
      <!-- 1. Image Search & Download -->
      <section v-if="activeSubTab === 'search'" class="pg-panel">
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
      <section v-else-if="activeSubTab === 'reverse'" class="pg-panel">
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

      <!-- 3. AI Music & Sound Effects Studio -->
      <section v-else class="pg-panel">
        <div class="music-composer-card">
          <div class="composer-row">
            <div class="mode-toggle">
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
            <div class="format-select">
              <span class="field-label" style="margin: 0">输出格式：</span>
              <select v-model="musicFormat" class="input-control" style="width: 96px">
                <option value="mp3">MP3</option>
                <option value="wav">WAV</option>
              </select>
            </div>
          </div>

          <div>
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

          <div v-if="audioKind === 'music'">
            <label class="field-label">歌词结构 (可选，支持 [Verse] / [Chorus] 标记)</label>
            <textarea
              v-model="musicLyrics"
              rows="6"
              class="input-control mono-num"
              placeholder="[Verse] 输入自定义歌词，留空则生成纯音乐伴奏..."
            ></textarea>
          </div>

          <div class="composer-footer">
            <span class="hint-text">生成音频将自动落盘至 get_shared_temp_dir() 白名单目录</span>
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

        <!-- Generated Audio Deck List -->
        <div class="track-list">
          <div v-for="track in generatedTracks" :key="track.id" class="track-card">
            <div class="track-info">
              <div class="track-head">
                <span class="pill-badge accent mono-num">
                  {{ track.kind === 'music' ? 'MUSIC' : 'SFX' }} · {{ track.format.toUpperCase() }}
                </span>
                <span class="track-prompt">{{ track.prompt }}</span>
              </div>
              <div v-if="track.lyrics" class="track-lyrics mono-num">{{ track.lyrics }}</div>
            </div>

            <div class="track-player-row">
              <audio :src="track.output_url" controls class="track-audio"></audio>
              <button
                type="button"
                class="btn"
                :disabled="!track.local_path"
                :title="track.local_path ? undefined : NOT_CACHED_HINT"
                @click="sendAudioToCanvas(track)"
              >
                置入画布
              </button>
              <button
                type="button"
                class="btn"
                @click="
                  bridgeDownload(
                    'tools/download-temp',
                    { path: track.local_path },
                    `${track.id}.${track.format}`,
                    track.output_url,
                  )
                "
              >
                下载音频
              </button>
            </div>
          </div>
        </div>
        <div v-if="!generatedTracks.length" class="pg-empty">暂无生成记录：输入描述词后点击上方按钮生成</div>
      </section>
    </div>

    <!-- Inline Image Lightbox Modal -->
    <div v-if="lightboxItem" class="lightbox-backdrop" @click="lightboxItem = null">
      <div class="lightbox-dialog" @click.stop>
        <img :src="lightboxItem.image_url" :alt="lightboxItem.title" class="lightbox-img" />
        <div class="lightbox-bar">
          <div>
            <div class="lightbox-title">{{ lightboxItem.title }}</div>
            <div class="lightbox-sub mono-num">{{ lightboxItem.local_path || '未缓存到本地' }}</div>
          </div>
          <div style="display: flex; gap: 8px">
            <button
              type="button"
              class="btn btn-primary"
              :disabled="!lightboxItem.local_path"
              :title="lightboxItem.local_path ? undefined : NOT_CACHED_HINT"
              @click="sendImageToCanvas(lightboxItem); lightboxItem = null"
            >
              置入无限画布
            </button>
            <button type="button" class="btn" @click="lightboxItem = null">关闭</button>
          </div>
        </div>
      </div>
    </div>
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
  padding: 16px 22px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.pg-title {
  margin: 0;
  font-size: 18px;
}


.subtab-bar {
  display: inline-flex;
  padding: 3px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
}

.subtab-btn {
  padding: 5px 12px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
}

.subtab-btn.active {
  background: var(--bg-surface);
  color: var(--accent);
  box-shadow: var(--shadow-card);
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

.music-composer-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: var(--shadow-card);
}

.composer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.mode-toggle {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
}

.seg-btn {
  padding: 5px 10px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.seg-btn.active {
  background: var(--bg-surface);
  color: var(--accent);
  box-shadow: var(--shadow-card);
}

.format-select {
  display: flex;
  align-items: center;
  gap: 8px;
}

.composer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 4px;
}

.hint-text {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.track-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.track-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.track-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.track-prompt {
  font-size: 13px;
  font-weight: 600;
}

.track-lyrics {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--text-secondary);
  white-space: pre-line;
}

.track-player-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.track-audio {
  flex: 1;
  height: 36px;
}

.lightbox-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(18, 18, 16, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
  padding: 28px;
}

.lightbox-dialog {
  max-width: 820px;
  width: 100%;
  background: var(--bg-surface);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-hairline);
  overflow: hidden;
  box-shadow: var(--shadow-float);
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
