import { create } from "zustand";
import type { Document } from "../models/document";

const DEFAULT_MAX_SIZE = 50;

interface UndoStore {
  undoStack: string[];
  redoStack: string[];
  maxSize: number;

  pushUndo: (document: Document) => void;
  undo: () => Document | null;
  redo: () => Document | null;
  canUndo: () => boolean;
  canRedo: () => boolean;
  clear: () => void;
}

export const useUndoStore = create<UndoStore>((set, get) => ({
  undoStack: [],
  redoStack: [],
  maxSize: DEFAULT_MAX_SIZE,

  pushUndo: (document: Document) => {
    const { undoStack, maxSize } = get();
    const snapshot = JSON.stringify(document);
    const next = [...undoStack, snapshot];

    if (next.length > maxSize) {
      next.shift();
    }

    set({ undoStack: next, redoStack: [] });
  },

  undo: () => {
    const { undoStack, redoStack } = get();
    if (undoStack.length === 0) return null;

    const next = [...undoStack];
    const snapshot = next.pop()!;

    set({ undoStack: next, redoStack: [...redoStack, snapshot] });
    return JSON.parse(snapshot) as Document;
  },

  redo: () => {
    const { undoStack, redoStack } = get();
    if (redoStack.length === 0) return null;

    const next = [...redoStack];
    const snapshot = next.pop()!;

    set({ undoStack: [...undoStack, snapshot], redoStack: next });
    return JSON.parse(snapshot) as Document;
  },

  canUndo: () => {
    return get().undoStack.length > 0;
  },

  canRedo: () => {
    return get().redoStack.length > 0;
  },

  clear: () => {
    set({ undoStack: [], redoStack: [] });
  },
}));
