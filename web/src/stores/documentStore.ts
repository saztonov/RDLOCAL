import { create } from "zustand";
import type { Document } from "../models/document";
import type { Block } from "../models/block";
import type { PdfInfo } from "../api/pdf";
import * as pdfApi from "../api/pdf";
import * as annotationsApi from "../api/annotations";
import { useUndoStore } from "./undoStore";

interface DocumentStore {
  nodeId: string | null;
  document: Document | null;
  pdfInfo: PdfInfo | null;
  currentPage: number;
  loading: boolean;
  error: string | null;
  dirty: boolean;
  syncTimer: ReturnType<typeof setTimeout> | null;

  loadDocument: (nodeId: string) => Promise<void>;
  setCurrentPage: (page: number) => void;
  addBlock: (block: Block) => void;
  updateBlock: (blockId: string, updates: Partial<Block>) => void;
  deleteBlock: (blockId: string) => void;
  deleteBlocks: (blockIds: string[]) => void;
  moveBlock: (blockId: string, newCoords: [number, number, number, number]) => void;
  getCurrentPageBlocks: () => Block[];
  markDirty: () => void;
  saveAnnotation: () => Promise<void>;
  undo: () => void;
  redo: () => void;
}

function updateBlocksInDocument(
  doc: Document,
  currentPage: number,
  updater: (blocks: Block[]) => Block[],
): Document {
  return {
    ...doc,
    pages: doc.pages.map((page, index) =>
      index === currentPage
        ? { ...page, blocks: updater(page.blocks) }
        : page,
    ),
  };
}

function pushUndo() {
  const { document } = useDocumentStore.getState();
  if (document) useUndoStore.getState().pushUndo(document);
}

export const useDocumentStore = create<DocumentStore>((set, get) => ({
  nodeId: null,
  document: null,
  pdfInfo: null,
  currentPage: 0,
  loading: false,
  error: null,
  dirty: false,
  syncTimer: null,

  loadDocument: async (nodeId: string) => {
    const { syncTimer } = get();
    if (syncTimer) clearTimeout(syncTimer);

    useUndoStore.getState().clear();
    set({
      loading: true,
      nodeId,
      error: null,
      pdfInfo: null,
      document: null,
      currentPage: 0,
      dirty: false,
      syncTimer: null,
    });
    try {
      const info = await pdfApi.getPdfInfo(nodeId);
      const doc: Document = {
        pdf_path: "",
        format_version: 1,
        pages: info.pages.map((p) => ({
          page_number: p.page_index,
          width: p.width,
          height: p.height,
          blocks: [],
        })),
      };
      set({ pdfInfo: info, document: doc, loading: false });
    } catch (err) {
      console.error("Failed to load PDF info:", err);
      set({
        error: err instanceof Error ? err.message : "Failed to load PDF",
        loading: false,
      });
    }
  },

  setCurrentPage: (page: number) => {
    set({ currentPage: page });
  },

  addBlock: (block: Block) => {
    const { document, currentPage } = get();
    if (!document) return;

    pushUndo();
    const updated = updateBlocksInDocument(document, currentPage, (blocks) => [
      ...blocks,
      block,
    ]);
    set({ document: updated });
    get().markDirty();
  },

  updateBlock: (blockId: string, updates: Partial<Block>) => {
    const { document, currentPage } = get();
    if (!document) return;

    const updated = updateBlocksInDocument(document, currentPage, (blocks) =>
      blocks.map((b) => (b.id === blockId ? { ...b, ...updates } : b)),
    );
    set({ document: updated });
    get().markDirty();
  },

  deleteBlock: (blockId: string) => {
    const { document, currentPage } = get();
    if (!document) return;

    pushUndo();
    const updated = updateBlocksInDocument(document, currentPage, (blocks) =>
      blocks.filter((b) => b.id !== blockId),
    );
    set({ document: updated });
    get().markDirty();
  },

  deleteBlocks: (blockIds: string[]) => {
    const { document, currentPage } = get();
    if (!document) return;

    pushUndo();
    const ids = new Set(blockIds);
    const updated = updateBlocksInDocument(document, currentPage, (blocks) =>
      blocks.filter((b) => !ids.has(b.id)),
    );
    set({ document: updated });
    get().markDirty();
  },

  moveBlock: (
    blockId: string,
    newCoords: [number, number, number, number],
  ) => {
    const { document, currentPage } = get();
    if (!document) return;

    pushUndo();
    const updated = updateBlocksInDocument(document, currentPage, (blocks) =>
      blocks.map((b) =>
        b.id === blockId ? { ...b, coords_norm: newCoords } : b,
      ),
    );
    set({ document: updated });
    get().markDirty();
  },

  getCurrentPageBlocks: () => {
    const { document, currentPage } = get();
    if (!document || !document.pages[currentPage]) return [];
    return document.pages[currentPage].blocks;
  },

  markDirty: () => {
    const { syncTimer } = get();
    if (syncTimer) clearTimeout(syncTimer);

    const timer = setTimeout(() => {
      get().saveAnnotation();
    }, 3000);

    set({ dirty: true, syncTimer: timer });
  },

  saveAnnotation: async () => {
    const { nodeId, document, syncTimer } = get();
    if (!nodeId || !document) return;
    if (syncTimer) clearTimeout(syncTimer);

    try {
      await annotationsApi.saveAnnotation(nodeId, document);
      set({ dirty: false, syncTimer: null });
    } catch (err) {
      console.error("Failed to save annotation:", err);
    }
  },

  undo: () => {
    const restored = useUndoStore.getState().undo();
    if (restored) {
      set({ document: restored });
      get().markDirty();
    }
  },

  redo: () => {
    const restored = useUndoStore.getState().redo();
    if (restored) {
      set({ document: restored });
      get().markDirty();
    }
  },
}));
