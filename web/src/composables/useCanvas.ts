import { ref, computed } from 'vue';
import type {
  CanvasDocumentV1,
  CanvasFileNodeV1,
  CanvasImageAnnotationV1,
  CanvasLayoutV1,
  CanvasMutationV1,
  CanvasOperationV1,
} from '../types/mcode';
import { bridgeGet, bridgePost, pushToast } from './useBridge';

/**
 * Generates clean inline SVG data URLs for default demo nodes so the canvas
 * renders crisp visuals offline with zero external network dependencies.
 */
function createPosterSvgDataUrl(title: string, subtitle: string, accentHex: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">
    <defs>
      <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="#f7f5ef"/>
        <stop offset="100%" stop-color="#ebe7dc"/>
      </linearGradient>
    </defs>
    <rect width="640" height="400" fill="url(#bg)"/>
    <rect x="28" y="28" width="584" height="344" rx="14" fill="#ffffff" stroke="#dedad0" stroke-width="1.5"/>
    <circle cx="510" cy="140" r="76" fill="${accentHex}" opacity="0.14"/>
    <circle cx="465" cy="215" r="54" fill="#262624" opacity="0.06"/>
    <rect x="56" y="62" width="96" height="24" rx="12" fill="${accentHex}" opacity="0.16"/>
    <text x="104" y="78" font-family="Georgia, serif" font-size="11" fill="${accentHex}" text-anchor="middle" font-weight="bold">MCODE CANVAS</text>
    <text x="56" y="138" font-family="Georgia, serif" font-size="28" fill="#262624" font-weight="600">${title}</text>
    <text x="56" y="174" font-family="sans-serif" font-size="14" fill="#6b6a63">${subtitle}</text>
    <line x1="56" y1="210" x2="340" y2="210" stroke="#e5e4dd" stroke-width="1"/>
    <rect x="56" y="236" width="160" height="86" rx="8" fill="#fbfbf9" stroke="#e5e4dd"/>
    <text x="72" y="266" font-family="monospace" font-size="12" fill="#262624">Res: 2048 × 1280</text>
    <text x="72" y="290" font-family="monospace" font-size="11" fill="#6b6a63">Matrix MCP Asset</text>
    <rect x="234" y="236" width="160" height="86" rx="8" fill="#fbfbf9" stroke="#e5e4dd"/>
    <text x="250" y="266" font-family="monospace" font-size="12" fill="#262624">Format: PNG</text>
    <text x="250" y="290" font-family="monospace" font-size="11" fill="${accentHex}">Annotatable</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

const initialNodes: CanvasFileNodeV1[] = [
  {
    id: 'node_poster_hero',
    type: 'file_ref',
    file: {
      relativePath: '.mavis/canvas/assets/brand_key_visual.png',
      assetId: 'asset_kv_01',
      fileName: 'brand_key_visual.png',
      mimeType: 'image/png',
      sizeBytes: 418240,
      previewUrl: createPosterSvgDataUrl('Autumn Editorial Hero', 'MiniMax M3.1 Visual Synthesis · 16:10', '#cc5a2b'),
    },
    layout: { x: 80, y: 70, width: 420, height: 310, zIndex: 2 },
    annotations: [
      {
        id: 'ann_initial_1',
        number: 1,
        normalizedRect: { x: 0.64, y: 0.16, width: 0.28, height: 0.42 },
        comment: '将右侧几何圆环调整为暖陶土渐变，增强质感对比',
        action: 'comment',
        createdAtMs: Date.now() - 180000,
      },
    ],
  },
  {
    id: 'node_poster_variant',
    type: 'file_ref',
    file: {
      relativePath: '.mavis/canvas/assets/product_card_v2.png',
      assetId: 'asset_kv_02',
      fileName: 'product_card_v2.png',
      mimeType: 'image/png',
      sizeBytes: 312900,
      previewUrl: createPosterSvgDataUrl('Minimalist Card Spec', 'Typography & Warm Parchment Surface', '#26734d'),
    },
    layout: { x: 540, y: 90, width: 380, height: 285, zIndex: 1 },
    annotations: [],
  },
  {
    id: 'node_spec_markdown',
    type: 'file_ref',
    file: {
      relativePath: 'workspace/deliverables/mcode_design_spec.md',
      fileName: 'mcode_design_spec.md',
      mimeType: 'text/markdown',
      sizeBytes: 2048,
      textContent: `# M-Plan 视觉交付规范\n\n- 主色调：Warm Parchment (#FBFBF9) + Terracotta (#CC5A2B)\n- 图像资产支持框选归一化坐标 (NormalizedRect 0..1)\n- 提交批注后由 VLM + 图像生成模型协同重绘`,
    },
    layout: { x: 80, y: 410, width: 420, height: 195, zIndex: 3 },
    annotations: [],
  },
];

const documentState = ref<CanvasDocumentV1>({
  schemaVersion: 1,
  canvasId: 'canvas_default_session',
  sessionId: 'astrbot_active_session',
  changeSeq: 3,
  nodes: initialNodes,
  updatedAtMs: Date.now(),
});

const selectedNodeId = ref<string | null>('node_poster_hero');
const interactionMode = ref<'pan_select' | 'annotate'>('pan_select');
const viewport = ref({ panX: 24, panY: 16, scale: 0.95 });
const isSubmittingIteration = ref(false);

export function useCanvas() {
  const selectedNode = computed(() =>
    documentState.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null,
  );

  const totalAnnotationsCount = computed(() =>
    documentState.value.nodes.reduce((acc, n) => acc + n.annotations.length, 0),
  );

  async function loadCanvasDocument(sessionId = 'astrbot_active_session'): Promise<void> {
    const doc = await bridgeGet<CanvasDocumentV1>(
      'canvas/doc',
      { session_id: sessionId },
      () => documentState.value,
    );
    if (doc && Array.isArray(doc.nodes)) {
      documentState.value = doc;
    }
  }

  async function applyMutations(mutations: CanvasMutationV1[]): Promise<CanvasDocumentV1> {
    if (!mutations.length) return documentState.value;
    const operation: CanvasOperationV1 = {
      schemaVersion: 1,
      operationId: `op_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      mutations,
    };

    const result = await bridgePost<{ operationId: string; document: CanvasDocumentV1 }>(
      'canvas/mutate',
      {
        sessionId: documentState.value.sessionId,
        operation,
      },
      () => {
        // Local state reducer matching @mavis/local-runtime-v2/src/service/canvas/local-canvas.service.ts
        let nodes = [...documentState.value.nodes];
        for (const m of mutations) {
          if (m.kind === 'add_file') {
            nodes.push({
              id: m.nodeId,
              type: 'file_ref',
              file: {
                relativePath: m.relativePath,
                fileName: m.fileMeta?.fileName || m.relativePath.split('/').pop() || 'asset.bin',
                mimeType: m.fileMeta?.mimeType || 'image/png',
                sizeBytes: m.fileMeta?.sizeBytes || 102400,
                previewUrl: m.fileMeta?.previewUrl,
                textContent: m.fileMeta?.textContent,
              },
              layout: { ...m.layout },
              annotations: [],
            });
          } else if (m.kind === 'update_layout') {
            nodes = nodes.map((n) => (n.id === m.nodeId ? { ...n, layout: { ...m.layout } } : n));
          } else if (m.kind === 'remove_node') {
            nodes = nodes.filter((n) => n.id !== m.nodeId);
            if (selectedNodeId.value === m.nodeId) {
              selectedNodeId.value = nodes[0]?.id ?? null;
            }
          } else if (m.kind === 'add_annotation') {
            nodes = nodes.map((n) =>
              n.id === m.nodeId ? { ...n, annotations: [...n.annotations, m.annotation] } : n,
            );
          } else if (m.kind === 'update_annotation') {
            nodes = nodes.map((n) =>
              n.id === m.nodeId
                ? {
                    ...n,
                    annotations: n.annotations.map((a) => (a.id === m.annotation.id ? m.annotation : a)),
                  }
                : n,
            );
          } else if (m.kind === 'remove_annotation') {
            nodes = nodes.map((n) => {
              if (n.id !== m.nodeId) return n;
              const filtered = n.annotations
                .filter((a) => a.id !== m.annotationId)
                .map((a, idx) => ({ ...a, number: idx + 1 }));
              return { ...n, annotations: filtered };
            });
          }
        }
        const nextDoc: CanvasDocumentV1 = {
          ...documentState.value,
          changeSeq: documentState.value.changeSeq + 1,
          nodes,
          updatedAtMs: Date.now(),
        };
        return { operationId: operation.operationId, document: nextDoc };
      },
    );

    documentState.value = result.document;
    return result.document;
  }

  async function updateNodeLayout(nodeId: string, layout: CanvasLayoutV1): Promise<void> {
    await applyMutations([{ kind: 'update_layout', nodeId, layout }]);
  }

  async function bringNodeToFront(nodeId: string): Promise<void> {
    selectedNodeId.value = nodeId;
    const target = documentState.value.nodes.find((n) => n.id === nodeId);
    if (!target) return;
    const maxZ = documentState.value.nodes.reduce((m, n) => Math.max(m, n.layout.zIndex), 1);
    if (target.layout.zIndex < maxZ) {
      await updateNodeLayout(nodeId, { ...target.layout, zIndex: maxZ + 1 });
    }
  }

  async function addFileNodeToCanvas(input: {
    fileName: string;
    relativePath?: string;
    mimeType: string;
    previewUrl?: string;
    textContent?: string;
  }): Promise<string> {
    const maxZ = documentState.value.nodes.reduce((m, n) => Math.max(m, n.layout.zIndex), 1);
    const count = documentState.value.nodes.length;
    const nodeId = `node_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const isAudio = input.mimeType.startsWith('audio/');
    const layout: CanvasLayoutV1 = {
      x: 120 + (count % 4) * 48,
      y: 90 + (count % 4) * 42,
      width: isAudio ? 340 : 380,
      height: isAudio ? 160 : 280,
      zIndex: maxZ + 1,
    };

    await applyMutations([
      {
        kind: 'add_file',
        nodeId,
        relativePath: input.relativePath || `.mavis/canvas/assets/${input.fileName}`,
        layout,
        fileMeta: {
          fileName: input.fileName,
          mimeType: input.mimeType,
          previewUrl: input.previewUrl,
          textContent: input.textContent,
        },
      },
    ]);
    selectedNodeId.value = nodeId;
    pushToast(`已将「${input.fileName}」置入无限画布`, 'ok');
    return nodeId;
  }

  async function addImageAnnotation(
    nodeId: string,
    rect: { x: number; y: number; width: number; height: number },
    comment: string,
    action: 'comment' | 'erase' = 'comment',
  ): Promise<void> {
    const node = documentState.value.nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const nextNumber = node.annotations.length + 1;
    const annotation: CanvasImageAnnotationV1 = {
      id: `ann_${Date.now()}_${nextNumber}`,
      number: nextNumber,
      normalizedRect: {
        x: Number(rect.x.toFixed(4)),
        y: Number(rect.y.toFixed(4)),
        width: Number(rect.width.toFixed(4)),
        height: Number(rect.height.toFixed(4)),
      },
      comment: comment.trim() || (action === 'erase' ? '擦除此选区内容' : '优化此选区细节'),
      action,
      createdAtMs: Date.now(),
    };
    await applyMutations([{ kind: 'add_annotation', nodeId, annotation }]);
    pushToast(`已添加选区批注 #${nextNumber}`, 'ok');
  }

  async function removeAnnotation(nodeId: string, annotationId: string): Promise<void> {
    await applyMutations([{ kind: 'remove_annotation', nodeId, annotationId }]);
  }

  async function removeNode(nodeId: string): Promise<void> {
    await applyMutations([{ kind: 'remove_node', nodeId }]);
    pushToast('已从画布移除节点', 'info');
  }

  async function submitIterationToAgent(extraInstruction = ''): Promise<{ submitted: number; prompt_preview: string }> {
    const annotatedNodes = documentState.value.nodes.filter((n) => n.annotations.length > 0);
    if (annotatedNodes.length === 0) {
      pushToast('请先在图片节点上框选区域并添加批注', 'warn');
      return { submitted: 0, prompt_preview: '' };
    }
    isSubmittingIteration.value = true;
    try {
      const payload = {
        session_id: documentState.value.sessionId,
        canvas_id: documentState.value.canvasId,
        change_seq: documentState.value.changeSeq,
        instruction: extraInstruction,
        nodes: annotatedNodes.map((n) => ({
          node_id: n.id,
          relative_path: n.file.relativePath,
          file_name: n.file.fileName,
          annotations: n.annotations,
        })),
      };

      const res = await bridgePost<{ ok: boolean; submitted: number; prompt_preview: string }>(
        'canvas/iterate',
        payload,
        () => {
          const lines = annotatedNodes.flatMap((n) =>
            n.annotations.map(
              (a) =>
                `[${n.file.fileName} #${a.number} (${a.action}) rect=(${a.normalizedRect.x},${a.normalizedRect.y},${a.normalizedRect.width},${a.normalizedRect.height})]: ${a.comment}`,
            ),
          );
          return {
            ok: true,
            submitted: lines.length,
            prompt_preview: lines.join(' | '),
          };
        },
      );
      pushToast(`已提交 ${res.submitted} 条画布批注至 MCode Agent 迭代队列`, 'ok');
      return res;
    } finally {
      isSubmittingIteration.value = false;
    }
  }

  function resetViewport(): void {
    viewport.value = { panX: 24, panY: 16, scale: 0.95 };
  }

  function zoomBy(delta: number): void {
    const next = Math.min(2.2, Math.max(0.35, Number((viewport.value.scale + delta).toFixed(2))));
    viewport.value = { ...viewport.value, scale: next };
  }

  return {
    documentState,
    selectedNodeId,
    selectedNode,
    interactionMode,
    viewport,
    totalAnnotationsCount,
    isSubmittingIteration,
    loadCanvasDocument,
    applyMutations,
    updateNodeLayout,
    bringNodeToFront,
    addFileNodeToCanvas,
    addImageAnnotation,
    removeAnnotation,
    removeNode,
    submitIterationToAgent,
    resetViewport,
    zoomBy,
  };
}
