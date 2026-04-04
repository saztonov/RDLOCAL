import { create } from "zustand";
import { ViewerState, BlockType, ShapeType } from "../models/enums";

const ZOOM_STEP = 0.1;
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 5.0;

interface ViewerStore {
  state: ViewerState;
  zoom: number;
  selectedBlockIds: string[];
  activeBlockType: BlockType;
  activeShapeType: ShapeType;

  setState: (state: ViewerState) => void;
  setZoom: (zoom: number) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  selectBlock: (blockId: string) => void;
  selectBlocks: (blockIds: string[]) => void;
  clearSelection: () => void;
  toggleBlockSelection: (blockId: string) => void;
  setActiveBlockType: (type: BlockType) => void;
  setActiveShapeType: (type: ShapeType) => void;
}

export const useViewerStore = create<ViewerStore>((set, get) => ({
  state: ViewerState.IDLE,
  zoom: 1.0,
  selectedBlockIds: [],
  activeBlockType: BlockType.TEXT,
  activeShapeType: ShapeType.RECTANGLE,

  setState: (state: ViewerState) => {
    set({ state });
  },

  setZoom: (zoom: number) => {
    set({ zoom: Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoom)) });
  },

  zoomIn: () => {
    const { zoom } = get();
    set({ zoom: Math.min(ZOOM_MAX, zoom + ZOOM_STEP) });
  },

  zoomOut: () => {
    const { zoom } = get();
    set({ zoom: Math.max(ZOOM_MIN, zoom - ZOOM_STEP) });
  },

  selectBlock: (blockId: string) => {
    set({ selectedBlockIds: [blockId] });
  },

  selectBlocks: (blockIds: string[]) => {
    set({ selectedBlockIds: blockIds });
  },

  clearSelection: () => {
    set({ selectedBlockIds: [] });
  },

  toggleBlockSelection: (blockId: string) => {
    const { selectedBlockIds } = get();
    if (selectedBlockIds.includes(blockId)) {
      set({
        selectedBlockIds: selectedBlockIds.filter((id) => id !== blockId),
      });
    } else {
      set({ selectedBlockIds: [...selectedBlockIds, blockId] });
    }
  },

  setActiveBlockType: (type: BlockType) => {
    set({ activeBlockType: type });
  },

  setActiveShapeType: (type: ShapeType) => {
    set({ activeShapeType: type });
  },
}));
