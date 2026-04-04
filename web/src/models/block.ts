import { BlockSource, BlockType, ShapeType } from "./enums.ts";

/** Bounding box as [x1, y1, x2, y2] in pixel coordinates. */
export type CoordsPx = [number, number, number, number];

/** Bounding box as [x1, y1, x2, y2] normalized to 0..1 range. */
export type CoordsNorm = [number, number, number, number];

/** A single annotation block, matching Python rd_core/models/block.py. */
export interface Block {
  id: string;
  page_index: number;
  coords_px: CoordsPx;
  coords_norm: CoordsNorm;
  block_type: BlockType;
  source: BlockSource;
  shape_type: ShapeType;
  polygon_points?: [number, number][];
  ocr_text?: string;
  ocr_html?: string;
  ocr_json?: Record<string, unknown>;
  ocr_meta?: Record<string, unknown>;
  is_correction?: boolean;
  created_at: string;
  linked_block_id?: string;
  category_code?: string;
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/** Generate a UUID v4 string. */
export function generateBlockId(): string {
  return crypto.randomUUID();
}

/** Convert pixel coordinates to normalized (0..1) coordinates. */
export function pxToNorm(
  coordsPx: CoordsPx,
  pageWidth: number,
  pageHeight: number,
): CoordsNorm {
  return [
    coordsPx[0] / pageWidth,
    coordsPx[1] / pageHeight,
    coordsPx[2] / pageWidth,
    coordsPx[3] / pageHeight,
  ];
}

/** Convert normalized (0..1) coordinates to pixel coordinates. */
export function normToPx(
  coordsNorm: CoordsNorm,
  pageWidth: number,
  pageHeight: number,
): CoordsPx {
  return [
    coordsNorm[0] * pageWidth,
    coordsNorm[1] * pageHeight,
    coordsNorm[2] * pageWidth,
    coordsNorm[3] * pageHeight,
  ];
}

const BLOCK_COLORS: Record<BlockType, string> = {
  [BlockType.TEXT]: "#4CAF50",
  [BlockType.IMAGE]: "#FF9800",
  [BlockType.STAMP]: "#2196F3",
} as const;

/** Return the display color for a given block type. */
export function getBlockColor(blockType: BlockType): string {
  return BLOCK_COLORS[blockType];
}
