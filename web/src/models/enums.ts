export const BlockType = {
  TEXT: "text",
  IMAGE: "image",
  STAMP: "stamp",
} as const;
export type BlockType = (typeof BlockType)[keyof typeof BlockType];

export const BlockSource = {
  USER: "user",
  AUTO: "auto",
} as const;
export type BlockSource = (typeof BlockSource)[keyof typeof BlockSource];

export const ShapeType = {
  RECTANGLE: "rectangle",
  POLYGON: "polygon",
} as const;
export type ShapeType = (typeof ShapeType)[keyof typeof ShapeType];

export const ViewerState = {
  IDLE: "idle",
  DRAWING_RECT: "drawing_rect",
  DRAWING_POLYGON: "drawing_polygon",
  SELECTING: "selecting",
  MOVING_BLOCK: "moving_block",
  RESIZING_BLOCK: "resizing_block",
  PANNING: "panning",
} as const;
export type ViewerState = (typeof ViewerState)[keyof typeof ViewerState];
