<script setup lang="ts">
import { ref, reactive } from 'vue';
import type { CanvasFileNodeV1 } from '../types/mcode';
import { useCanvas } from '../composables/useCanvas';
import { bridgeDownload, copyToClipboard } from '../composables/useBridge';

const {
  documentState,
  selectedNodeId,
  selectedNode,
  interactionMode,
  viewport,
  totalAnnotationsCount,
  isSubmittingIteration,
  updateNodeLayout,
  bringNodeToFront,
  addFileNodeToCanvas,
  addImageAnnotation,
  removeAnnotation,
  removeNode,
  submitIterationToAgent,
  resetViewport,
  zoomBy,
} = useCanvas();

const inspectorOpen = ref(true);
const extraPrompt = ref('');
const lastSubmittedPreview = ref('');

// Panning state
const isPanning = ref(false);
const panStart = reactive({ x: 0, y: 0, originPanX: 0, originPanY: 0 });

// Node dragging / resizing state
const draggingNode = reactive<{
  nodeId: string | null;
  mode: 'move' | 'resize';
  startX: number;
  startY: number;
  origX: number;
  origY: number;
  origW: number;
  origH: number;
  zIndex: number;
}>({
  nodeId: null,
  mode: 'move',
  startX: 0,
  startY: 0,
  origX: 0,
  origY: 0,
  origW: 0,
  origH: 0,
  zIndex: 1,
});

// Image rectangular annotation drawing state
const draftRect = reactive<{
  active: boolean;
  drawing: boolean;
  nodeId: string | null;
  startX: number;
  startY: number;
  x: number;
  y: number;
  width: number;
  height: number;
  comment: string;
  action: 'comment' | 'erase';
}>({
  active: false,
  drawing: false,
  nodeId: null,
  startX: 0,
  startY: 0,
  x: 0,
  y: 0,
  width: 0,
  height: 0,
  comment: '',
  action: 'comment',
});

function isImageNode(node: CanvasFileNodeV1): boolean {
  return Boolean(node.file.mimeType?.startsWith('image/') || node.file.previewUrl);
}

function isAudioNode(node: CanvasFileNodeV1): boolean {
  return Boolean(node.file.mimeType?.startsWith('audio/'));
}

function handleSurfaceMouseDown(e: MouseEvent): void {
  if (e.target !== e.currentTarget && !(e.target as HTMLElement)?.classList.contains('canvas-world')) {
    return;
  }
  isPanning.value = true;
  panStart.x = e.clientX;
  panStart.y = e.clientY;
  panStart.originPanX = viewport.value.panX;
  panStart.originPanY = viewport.value.panY;
}

function startNodeDrag(e: MouseEvent, node: CanvasFileNodeV1, mode: 'move' | 'resize'): void {
  e.stopPropagation();
  bringNodeToFront(node.id);
  draggingNode.nodeId = node.id;
  draggingNode.mode = mode;
  draggingNode.startX = e.clientX;
  draggingNode.startY = e.clientY;
  draggingNode.origX = node.layout.x;
  draggingNode.origY = node.layout.y;
  draggingNode.origW = node.layout.width;
  draggingNode.origH = node.layout.height;
  draggingNode.zIndex = node.layout.zIndex;
}

function startImageDraw(e: MouseEvent, node: CanvasFileNodeV1): void {
  if (interactionMode.value !== 'annotate' || !isImageNode(node)) return;
  e.stopPropagation();
  e.preventDefault();
  bringNodeToFront(node.id);
  const container = e.currentTarget as HTMLElement;
  const rect = container.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;

  const nx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const ny = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));

  draftRect.active = true;
  draftRect.drawing = true;
  draftRect.nodeId = node.id;
  draftRect.startX = nx;
  draftRect.startY = ny;
  draftRect.x = nx;
  draftRect.y = ny;
  draftRect.width = 0.01;
  draftRect.height = 0.01;
  draftRect.comment = '';
  draftRect.action = 'comment';
}

function onImageMouseMove(e: MouseEvent, node: CanvasFileNodeV1): void {
  if (!draftRect.drawing || draftRect.nodeId !== node.id) return;
  const container = e.currentTarget as HTMLElement;
  const rect = container.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;

  const curX = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const curY = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));

  draftRect.x = Math.min(draftRect.startX, curX);
  draftRect.y = Math.min(draftRect.startY, curY);
  draftRect.width = Math.max(0.04, Math.abs(curX - draftRect.startX));
  draftRect.height = Math.max(0.04, Math.abs(curY - draftRect.startY));
}

function endImageDraw(): void {
  if (!draftRect.drawing) return;
  draftRect.drawing = false;
}

async function confirmDraftAnnotation(): Promise<void> {
  if (!draftRect.active || !draftRect.nodeId) return;
  await addImageAnnotation(
    draftRect.nodeId,
    {
      x: draftRect.x,
      y: draftRect.y,
      width: draftRect.width,
      height: draftRect.height,
    },
    draftRect.comment,
    draftRect.action,
  );
  cancelDraftAnnotation();
}

function cancelDraftAnnotation(): void {
  draftRect.active = false;
  draftRect.drawing = false;
  draftRect.nodeId = null;
  draftRect.comment = '';
}

function handleGlobalMouseMove(e: MouseEvent): void {
  if (isPanning.value) {
    viewport.value = {
      ...viewport.value,
      panX: Math.round(panStart.originPanX + (e.clientX - panStart.x)),
      panY: Math.round(panStart.originPanY + (e.clientY - panStart.y)),
    };
    return;
  }

  if (draggingNode.nodeId) {
    const scale = viewport.value.scale || 1;
    const dx = (e.clientX - draggingNode.startX) / scale;
    const dy = (e.clientY - draggingNode.startY) / scale;
    const target = documentState.value.nodes.find((n) => n.id === draggingNode.nodeId);
    if (!target) return;

    if (draggingNode.mode === 'move') {
      target.layout.x = Math.round(draggingNode.origX + dx);
      target.layout.y = Math.round(draggingNode.origY + dy);
    } else {
      target.layout.width = Math.max(240, Math.round(draggingNode.origW + dx));
      target.layout.height = Math.max(150, Math.round(draggingNode.origH + dy));
    }
  }
}

async function handleGlobalMouseUp(): Promise<void> {
  if (isPanning.value) {
    isPanning.value = false;
  }
  if (draggingNode.nodeId) {
    const nodeId = draggingNode.nodeId;
    draggingNode.nodeId = null;
    const target = documentState.value.nodes.find((n) => n.id === nodeId);
    if (target) {
      await updateNodeLayout(nodeId, { ...target.layout });
    }
  }
  if (draftRect.drawing) {
    draftRect.drawing = false;
  }
}

function handleWheel(e: WheelEvent): void {
  const delta = e.deltaY < 0 ? 0.08 : -0.08;
  zoomBy(delta);
}

async function handleAddSampleNote(): Promise<void> {
  await addFileNodeToCanvas({
    fileName: `iteration_note_${documentState.value.nodes.length + 1}.md`,
    mimeType: 'text/markdown',
    textContent: `# 视觉迭代备注\n\n- 保持羊皮纸低饱和底色\n- 重点核对归一化批注区域与主体边缘对齐`,
  });
}

async function handleTriggerIteration(): Promise<void> {
  const res = await submitIterationToAgent(extraPrompt.value);
  if (res.prompt_preview) {
    lastSubmittedPreview.value = res.prompt_preview;
    extraPrompt.value = '';
  }
}
</script>

<template>
  <div
    class="canvas-root"
    @mousemove="handleGlobalMouseMove"
    @mouseup="handleGlobalMouseUp"
  >
    <!-- Top Canvas Control Bar (Claude Artifact Bar style) -->
    <header class="canvas-toolbar">
      <div class="toolbar-left">
        <div class="mode-segmented">
          <button
            type="button"
            class="seg-btn"
            :class="{ active: interactionMode === 'pan_select' }"
            @click="interactionMode = 'pan_select'"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20" />
            </svg>
            拖拽排版
          </button>
          <button
            type="button"
            class="seg-btn"
            :class="{ active: interactionMode === 'annotate' }"
            @click="interactionMode = 'annotate'"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="4 2" />
              <circle cx="9" cy="9" r="2" />
            </svg>
            框选批注 (NormalizedRect)
          </button>
        </div>

        <div class="zoom-controls mono-num">
          <button type="button" class="btn-ghost zoom-btn" @click="zoomBy(-0.1)">-</button>
          <span class="zoom-label">{{ Math.round(viewport.scale * 100) }}%</span>
          <button type="button" class="btn-ghost zoom-btn" @click="zoomBy(0.1)">+</button>
          <button type="button" class="btn-ghost zoom-reset" @click="resetViewport">重置视口</button>
        </div>
      </div>

      <div class="toolbar-right">
        <button type="button" class="btn" @click="handleAddSampleNote">
          + 新建规格便签
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="isSubmittingIteration"
          @click="handleTriggerIteration"
        >
          {{ isSubmittingIteration ? '推送迭代中...' : `提交批注给 Bot 迭代 (${totalAnnotationsCount})` }}
        </button>
        <button
          type="button"
          class="btn"
          :title="inspectorOpen ? '收起检查器' : '展开检查器'"
          @click="inspectorOpen = !inspectorOpen"
        >
          {{ inspectorOpen ? '收起侧栏' : '属性与批注' }}
        </button>
      </div>
    </header>

    <!-- Main Infinite Canvas Stage + Right Inspector -->
    <div class="canvas-stage-wrap">
      <div
        class="canvas-viewport"
        :class="{ 'annotate-cursor': interactionMode === 'annotate' }"
        @mousedown="handleSurfaceMouseDown"
        @wheel.prevent="handleWheel"
      >
        <div
          class="canvas-world"
          :style="{
            transform: `translate(${viewport.panX}px, ${viewport.panY}px) scale(${viewport.scale})`,
          }"
        >
          <!-- Render CanvasFileNodeV1 items -->
          <div
            v-for="node in documentState.nodes"
            :key="node.id"
            class="canvas-node"
            :class="{ selected: selectedNodeId === node.id }"
            :style="{
              left: `${node.layout.x}px`,
              top: `${node.layout.y}px`,
              width: `${node.layout.width}px`,
              height: `${node.layout.height}px`,
              zIndex: node.layout.zIndex,
            }"
            @mousedown="bringNodeToFront(node.id)"
          >
            <!-- Node Header (Drag Handle) -->
            <div class="node-header" @mousedown="startNodeDrag($event, node, 'move')">
              <span class="node-title mono-num">{{ node.file.fileName || node.file.relativePath }}</span>
              <div class="node-header-actions">
                <span class="node-coords mono-num">{{ node.layout.x }}, {{ node.layout.y }}</span>
                <button
                  type="button"
                  class="node-icon-btn"
                  title="下载此节点资产"
                  @click.stop="
                    bridgeDownload(
                      'canvas/download',
                      { path: node.file.relativePath },
                      node.file.fileName || 'asset.png',
                      node.file.previewUrl,
                    )
                  "
                >
                  ↓
                </button>
                <button
                  type="button"
                  class="node-icon-btn danger"
                  title="移除节点"
                  @click.stop="removeNode(node.id)"
                >
                  ×
                </button>
              </div>
            </div>

            <!-- Node Body: Image with NormalizedRect Annotations -->
            <div
              v-if="isImageNode(node)"
              class="node-image-stage"
              @mousedown="startImageDraw($event, node)"
              @mousemove="onImageMouseMove($event, node)"
              @mouseup="endImageDraw"
            >
              <img
                :src="node.file.previewUrl"
                :alt="node.file.fileName"
                class="node-img"
                draggable="false"
              />

              <!-- Persisted Annotations -->
              <div
                v-for="ann in node.annotations"
                :key="ann.id"
                class="annotation-box"
                :class="{ erase: ann.action === 'erase' }"
                :style="{
                  left: `${ann.normalizedRect.x * 100}%`,
                  top: `${ann.normalizedRect.y * 100}%`,
                  width: `${ann.normalizedRect.width * 100}%`,
                  height: `${ann.normalizedRect.height * 100}%`,
                }"
                :title="ann.comment"
              >
                <span class="annotation-pin mono-num">{{ ann.number }}</span>
              </div>

              <!-- Active Draft Annotation Selection -->
              <div
                v-if="draftRect.active && draftRect.nodeId === node.id"
                class="annotation-box draft"
                :style="{
                  left: `${draftRect.x * 100}%`,
                  top: `${draftRect.y * 100}%`,
                  width: `${draftRect.width * 100}%`,
                  height: `${draftRect.height * 100}%`,
                }"
              ></div>
            </div>

            <!-- Node Body: Audio Player -->
            <div v-else-if="isAudioNode(node)" class="node-audio-stage">
              <div class="audio-caption">Matrix 音频资产节点</div>
              <audio v-if="node.file.previewUrl" :src="node.file.previewUrl" controls class="audio-el"></audio>
              <div class="audio-meta mono-num">{{ node.file.relativePath }}</div>
            </div>

            <!-- Node Body: Markdown / Text Spec -->
            <div v-else class="node-text-stage">
              <pre class="node-markdown-pre">{{ node.file.textContent || node.file.relativePath }}</pre>
            </div>

            <!-- Floating Claude Annotation Composer Bar -->
            <div
              v-if="draftRect.active && !draftRect.drawing && draftRect.nodeId === node.id"
              class="floating-annotation-composer"
              @mousedown.stop
            >
              <div class="composer-top">
                <span class="pill-badge accent mono-num">
                  选区 ({{ draftRect.x.toFixed(2) }}, {{ draftRect.y.toFixed(2) }}, {{ draftRect.width.toFixed(2) }}×{{ draftRect.height.toFixed(2) }})
                </span>
                <div class="action-switch">
                  <button
                    type="button"
                    class="mini-tab"
                    :class="{ active: draftRect.action === 'comment' }"
                    @click="draftRect.action = 'comment'"
                  >
                    修改批注
                  </button>
                  <button
                    type="button"
                    class="mini-tab"
                    :class="{ active: draftRect.action === 'erase' }"
                    @click="draftRect.action = 'erase'"
                  >
                    局部擦除
                  </button>
                </div>
              </div>
              <div class="composer-input-row">
                <input
                  v-model="draftRect.comment"
                  type="text"
                  class="input-control"
                  placeholder="输入针对此矩形选区的修改意见，回车保存..."
                  @keyup.enter="confirmDraftAnnotation"
                />
                <button type="button" class="btn btn-primary" @click="confirmDraftAnnotation">确定</button>
                <button type="button" class="btn" @click="cancelDraftAnnotation">取消</button>
              </div>
            </div>

            <!-- Bottom-Right Resize Handle -->
            <div
              class="node-resize-handle"
              title="拖拽缩放尺寸"
              @mousedown="startNodeDrag($event, node, 'resize')"
            ></div>
          </div>
        </div>

        <!-- Bottom Floating Mode Hint Pill -->
        <div class="canvas-bottom-hint">
          <span v-if="interactionMode === 'annotate'">
            当前处于<strong>框选批注模式</strong>：在任意图片节点上按住鼠标拖拽即可拉出 NormalizedRect 选区并添加修改指令
          </span>
          <span v-else>
            当前处于<strong>拖拽排版模式</strong>：拖拽节点标题栏移动位置，拖拽右下角缩放大小，滚轮缩放画布视口
          </span>
        </div>
      </div>

      <!-- Right Node & Annotation Inspector Drawer -->
      <aside v-if="inspectorOpen" class="canvas-inspector">
        <div class="inspector-section">
          <div class="inspector-title">当前选中节点</div>
          <div v-if="selectedNode" class="node-detail-box">
            <div class="detail-row">
              <span class="detail-k">文件名</span>
              <span class="detail-v mono-num">{{ selectedNode.file.fileName }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-k">相对路径</span>
              <span
                class="detail-v mono-num clickable"
                title="点击复制路径"
                @click="copyToClipboard(selectedNode.file.relativePath, '节点路径')"
              >
                {{ selectedNode.file.relativePath }}
              </span>
            </div>
            <div class="detail-row">
              <span class="detail-k">画布坐标</span>
              <span class="detail-v mono-num">
                X:{{ selectedNode.layout.x }} Y:{{ selectedNode.layout.y }} ({{ selectedNode.layout.width }}×{{ selectedNode.layout.height }})
              </span>
            </div>
          </div>
          <div v-else class="empty-hint">点击画布上的任意卡片查看详情</div>
        </div>

        <div class="inspector-section" style="flex: 1; overflow-y: auto">
          <div class="inspector-title">
            图片局部批注 ({{ selectedNode?.annotations.length || 0 }})
          </div>
          <div v-if="selectedNode && selectedNode.annotations.length" class="ann-list">
            <div v-for="ann in selectedNode.annotations" :key="ann.id" class="ann-item">
              <div class="ann-item-head">
                <span class="pill-badge accent mono-num">#{{ ann.number }} · {{ ann.action }}</span>
                <span class="ann-rect mono-num">
                  ({{ ann.normalizedRect.x }}, {{ ann.normalizedRect.y }})
                </span>
                <button
                  type="button"
                  class="btn-ghost"
                  style="padding: 1px 5px; font-size: 11px"
                  @click="removeAnnotation(selectedNode.id, ann.id)"
                >
                  删除
                </button>
              </div>
              <div class="ann-comment">{{ ann.comment }}</div>
            </div>
          </div>
          <div v-else class="empty-hint">
            切换到顶部「框选批注」模式后，在图片上拖拽即可创建精准的局部重绘/分析锚点。
          </div>
        </div>

        <!-- Global Iteration Instruction Footer -->
        <div class="inspector-footer">
          <div class="inspector-title">附加全局迭代要求</div>
          <textarea
            v-model="extraPrompt"
            rows="2"
            class="input-control"
            placeholder="可选：补充整体风格或光影指令..."
          ></textarea>
          <button
            type="button"
            class="btn btn-primary"
            style="width: 100%; margin-top: 8px"
            :disabled="isSubmittingIteration"
            @click="handleTriggerIteration"
          >
            推送至会话执行重绘
          </button>
          <div v-if="lastSubmittedPreview" class="submitted-preview mono-num">
            最近提交载荷：{{ lastSubmittedPreview }}
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.canvas-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.canvas-toolbar {
  height: 48px;
  padding: 0 16px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mode-segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
  border: 1px solid var(--border-hairline);
}

.seg-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
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

.zoom-controls {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  font-size: 12px;
}

.zoom-btn {
  padding: 2px 7px;
  border: none;
  cursor: pointer;
}

.zoom-label {
  min-width: 42px;
  text-align: center;
}

.zoom-reset {
  padding: 2px 6px;
  font-size: 11px;
  border: none;
  cursor: pointer;
}

.canvas-stage-wrap {
  flex: 1;
  display: flex;
  min-height: 0;
  position: relative;
}

.canvas-viewport {
  flex: 1;
  position: relative;
  overflow: hidden;
  background-color: var(--bg-canvas);
  background-image: radial-gradient(var(--grid-dot) 1.25px, transparent 1.25px);
  background-size: 20px 20px;
  cursor: grab;
  user-select: none;
}

.canvas-viewport:active {
  cursor: grabbing;
}

.canvas-viewport.annotate-cursor {
  cursor: crosshair;
}

.canvas-world {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  transform-origin: 0 0;
}

.canvas-node {
  position: absolute;
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  overflow: visible;
  transition: box-shadow 0.14s ease, border-color 0.14s ease;
}

.canvas-node.selected {
  border-color: var(--accent);
  box-shadow: var(--shadow-float);
}

.node-header {
  height: 30px;
  padding: 0 10px;
  background: var(--bg-surface-muted);
  border-bottom: 1px solid var(--border-hairline);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: move;
}

.node-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-header-actions {
  display: flex;
  align-items: center;
  gap: 5px;
}

.node-coords {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.node-icon-btn {
  width: 20px;
  height: 20px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.node-icon-btn:hover {
  background: var(--bg-surface-hover);
  color: var(--text-primary);
}

.node-icon-btn.danger:hover {
  color: var(--status-danger);
}

.node-image-stage {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border-bottom-left-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  background: var(--bg-surface-muted);
}

.node-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  pointer-events: none;
}

.annotation-box {
  position: absolute;
  border: 2px solid var(--accent);
  background: rgba(204, 90, 43, 0.16);
  border-radius: 4px;
  pointer-events: none;
}

.annotation-box.erase {
  border-color: var(--status-danger);
  background: rgba(185, 56, 44, 0.18);
  border-style: dashed;
}

.annotation-box.draft {
  border-style: dashed;
  background: rgba(204, 90, 43, 0.22);
}

.annotation-pin {
  position: absolute;
  top: -10px;
  left: -10px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--accent);
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
}

.node-audio-stage {
  flex: 1;
  padding: 14px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
}

.audio-caption {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.audio-el {
  width: 100%;
  height: 34px;
}

.audio-meta {
  font-size: 11px;
  color: var(--text-tertiary);
}

.node-text-stage {
  flex: 1;
  padding: 12px;
  overflow: auto;
}

.node-markdown-pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.55;
}

.floating-annotation-composer {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: -86px;
  background: var(--bg-surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  box-shadow: var(--shadow-float);
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.composer-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.action-switch {
  display: flex;
  gap: 4px;
}

.mini-tab {
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
  font-size: 11px;
  cursor: pointer;
}

.mini-tab.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.composer-input-row {
  display: flex;
  gap: 6px;
}

.node-resize-handle {
  position: absolute;
  right: 2px;
  bottom: 2px;
  width: 14px;
  height: 14px;
  cursor: nwse-resize;
  background: linear-gradient(135deg, transparent 50%, var(--border-strong) 50%);
  border-bottom-right-radius: 6px;
}

.canvas-bottom-hint {
  position: absolute;
  left: 16px;
  bottom: 14px;
  padding: 6px 12px;
  border-radius: var(--radius-pill);
  background: var(--bg-elevated);
  border: 1px solid var(--border-hairline);
  box-shadow: var(--shadow-card);
  font-size: 12px;
  color: var(--text-secondary);
  pointer-events: none;
}

.canvas-inspector {
  width: 290px;
  min-width: 290px;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-hairline);
  display: flex;
  flex-direction: column;
}

.inspector-section {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-hairline);
}

.inspector-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.node-detail-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface-muted);
}

.detail-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 11.5px;
}

.detail-k {
  color: var(--text-secondary);
  white-space: nowrap;
}

.detail-v {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-v.clickable {
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
}

.empty-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.45;
}

.ann-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ann-item {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-hairline);
  background: var(--bg-surface-muted);
}

.ann-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.ann-rect {
  font-size: 11px;
  color: var(--text-tertiary);
}

.ann-comment {
  font-size: 12px;
  color: var(--text-primary);
}

.inspector-footer {
  padding: 12px 14px;
  background: var(--bg-surface-muted);
}

.submitted-preview {
  margin-top: 8px;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  font-size: 11px;
  color: var(--text-secondary);
  max-height: 64px;
  overflow-y: auto;
}
</style>
