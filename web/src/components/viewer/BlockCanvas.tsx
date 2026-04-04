import { useCallback, useEffect, useRef, useState } from "react";
import { Stage, Layer, Rect, Text, Group } from "react-konva";
import type { KonvaEventObject } from "konva/lib/Node";
import { useDocumentStore } from "../../stores/documentStore";
import { useViewerStore } from "../../stores/viewerStore";
import { ViewerState, BlockSource, ShapeType } from "../../models/enums";
import type { Block, CoordsNorm } from "../../models/block";
import {
  generateBlockId,
  normToPx,
  pxToNorm,
  getBlockColor,
} from "../../models/block";

interface Props {
  width: number;
  height: number;
  pageWidth: number;
  pageHeight: number;
}

/** Minimum drag distance (px) to count as a drawn rectangle. */
const MIN_RECT_SIZE = 4;

/** Size of resize handle in pixels. */
const HANDLE_SIZE = 8;
const HANDLE_HALF = HANDLE_SIZE / 2;

/** Minimum block dimension during resize (px). */
const MIN_BLOCK_DIM = 10;

type HandlePos = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

const HANDLE_CURSORS: Record<HandlePos, string> = {
  nw: "nwse-resize",
  se: "nwse-resize",
  ne: "nesw-resize",
  sw: "nesw-resize",
  n: "ns-resize",
  s: "ns-resize",
  w: "ew-resize",
  e: "ew-resize",
};

function getHandlePositions(
  w: number,
  h: number,
): { pos: HandlePos; x: number; y: number }[] {
  return [
    { pos: "nw", x: 0, y: 0 },
    { pos: "n", x: w / 2, y: 0 },
    { pos: "ne", x: w, y: 0 },
    { pos: "e", x: w, y: h / 2 },
    { pos: "se", x: w, y: h },
    { pos: "s", x: w / 2, y: h },
    { pos: "sw", x: 0, y: h },
    { pos: "w", x: 0, y: h / 2 },
  ];
}

/**
 * Konva-based overlay that renders annotation blocks on top of the PDF page
 * and handles rectangle drawing, selection, drag-move, and resize interactions.
 */
export function BlockCanvas({ width, height }: Props) {
  const getCurrentPageBlocks = useDocumentStore((s) => s.getCurrentPageBlocks);
  const addBlock = useDocumentStore((s) => s.addBlock);
  const moveBlock = useDocumentStore((s) => s.moveBlock);
  const updateBlock = useDocumentStore((s) => s.updateBlock);
  const deleteBlocks = useDocumentStore((s) => s.deleteBlocks);
  const currentPage = useDocumentStore((s) => s.currentPage);

  const viewerState = useViewerStore((s) => s.state);
  const setState = useViewerStore((s) => s.setState);
  const selectedBlockIds = useViewerStore((s) => s.selectedBlockIds);
  const selectBlock = useViewerStore((s) => s.selectBlock);
  const toggleBlockSelection = useViewerStore((s) => s.toggleBlockSelection);
  const clearSelection = useViewerStore((s) => s.clearSelection);
  const activeBlockType = useViewerStore((s) => s.activeBlockType);
  const zoom = useViewerStore((s) => s.zoom);

  const blocks = getCurrentPageBlocks();

  // Drawing state kept local -- not persisted until mouseup.
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(
    null,
  );
  const [drawCurrent, setDrawCurrent] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const isDraggingBlock = useRef(false);

  // Resize state
  const [resizeHandle, setResizeHandle] = useState<HandlePos | null>(null);
  const [resizeBlockId, setResizeBlockId] = useState<string | null>(null);
  const [resizeStartCoords, setResizeStartCoords] =
    useState<CoordsNorm | null>(null);
  const [resizeStartPos, setResizeStartPos] = useState<{
    x: number;
    y: number;
  } | null>(null);

  // Hover cursor state — updated by block/handle mouse enter/leave
  const [stageCursor, setStageCursor] = useState("crosshair");

  // -----------------------------------------------------------------------
  // Coordinate helpers (canvas px, accounting for zoom)
  // -----------------------------------------------------------------------
  const blockRect = useCallback(
    (block: Block) => {
      const [x1, y1, x2, y2] = normToPx(block.coords_norm, width, height);
      return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
    },
    [width, height],
  );

  // -----------------------------------------------------------------------
  // Stage mouse handlers -- rectangle drawing
  // -----------------------------------------------------------------------
  const handleMouseDown = useCallback(
    (e: KonvaEventObject<MouseEvent>) => {
      // Ignore if clicking on an existing block (handled by block handlers).
      if (e.target !== e.target.getStage()) return;

      if (viewerState === ViewerState.IDLE) {
        const pos = e.target.getStage()?.getPointerPosition();
        if (!pos) return;
        clearSelection();
        setDrawStart({ x: pos.x, y: pos.y });
        setDrawCurrent({ x: pos.x, y: pos.y });
        setState(ViewerState.DRAWING_RECT);
      }
    },
    [viewerState, clearSelection, setState],
  );

  const handleMouseMove = useCallback(
    (e: KonvaEventObject<MouseEvent>) => {
      const pos = e.target.getStage()?.getPointerPosition();
      if (!pos) return;

      if (viewerState === ViewerState.DRAWING_RECT && drawStart) {
        setDrawCurrent({ x: pos.x, y: pos.y });
        return;
      }

      if (
        viewerState === ViewerState.RESIZING_BLOCK &&
        resizeHandle &&
        resizeBlockId &&
        resizeStartCoords &&
        resizeStartPos
      ) {
        const dx = (pos.x - resizeStartPos.x) / width;
        const dy = (pos.y - resizeStartPos.y) / height;

        const [nx1, ny1, nx2, ny2] = resizeStartCoords;
        let newX1 = nx1,
          newY1 = ny1,
          newX2 = nx2,
          newY2 = ny2;

        // Adjust edges based on handle
        if (resizeHandle.includes("w")) newX1 = nx1 + dx;
        if (resizeHandle.includes("e")) newX2 = nx2 + dx;
        if (resizeHandle === "n" || resizeHandle === "nw" || resizeHandle === "ne")
          newY1 = ny1 + dy;
        if (resizeHandle === "s" || resizeHandle === "sw" || resizeHandle === "se")
          newY2 = ny2 + dy;

        // Enforce minimum size
        const minW = MIN_BLOCK_DIM / width;
        const minH = MIN_BLOCK_DIM / height;
        if (newX2 - newX1 < minW) {
          if (resizeHandle.includes("w")) newX1 = newX2 - minW;
          else newX2 = newX1 + minW;
        }
        if (newY2 - newY1 < minH) {
          if (
            resizeHandle === "n" ||
            resizeHandle === "nw" ||
            resizeHandle === "ne"
          )
            newY1 = newY2 - minH;
          else newY2 = newY1 + minH;
        }

        // Clamp to 0..1
        newX1 = Math.max(0, Math.min(1, newX1));
        newY1 = Math.max(0, Math.min(1, newY1));
        newX2 = Math.max(0, Math.min(1, newX2));
        newY2 = Math.max(0, Math.min(1, newY2));

        updateBlock(resizeBlockId, {
          coords_norm: [newX1, newY1, newX2, newY2],
        });
        return;
      }
    },
    [
      viewerState,
      drawStart,
      resizeHandle,
      resizeBlockId,
      resizeStartCoords,
      resizeStartPos,
      width,
      height,
      updateBlock,
    ],
  );

  const handleMouseUp = useCallback(() => {
    if (viewerState === ViewerState.DRAWING_RECT && drawStart && drawCurrent) {
      const x1 = Math.min(drawStart.x, drawCurrent.x);
      const y1 = Math.min(drawStart.y, drawCurrent.y);
      const x2 = Math.max(drawStart.x, drawCurrent.x);
      const y2 = Math.max(drawStart.y, drawCurrent.y);

      if (x2 - x1 > MIN_RECT_SIZE && y2 - y1 > MIN_RECT_SIZE) {
        const coordsNorm: CoordsNorm = pxToNorm(
          [x1, y1, x2, y2],
          width,
          height,
        );

        const block: Block = {
          id: generateBlockId(),
          page_index: currentPage,
          coords_px: [
            Math.round(x1 / zoom),
            Math.round(y1 / zoom),
            Math.round(x2 / zoom),
            Math.round(y2 / zoom),
          ],
          coords_norm: coordsNorm,
          block_type: activeBlockType,
          source: BlockSource.USER,
          shape_type: ShapeType.RECTANGLE,
          created_at: new Date().toISOString(),
        };

        addBlock(block);
        selectBlock(block.id);
      }
    }

    if (viewerState === ViewerState.RESIZING_BLOCK) {
      setResizeHandle(null);
      setResizeBlockId(null);
      setResizeStartCoords(null);
      setResizeStartPos(null);
    }

    setDrawStart(null);
    setDrawCurrent(null);
    setState(ViewerState.IDLE);
  }, [
    viewerState,
    drawStart,
    drawCurrent,
    width,
    height,
    zoom,
    currentPage,
    activeBlockType,
    addBlock,
    selectBlock,
    setState,
  ]);

  // -----------------------------------------------------------------------
  // Block interaction handlers
  // -----------------------------------------------------------------------
  const handleBlockClick = useCallback(
    (e: KonvaEventObject<MouseEvent>, block: Block) => {
      e.cancelBubble = true;
      if (e.evt.ctrlKey || e.evt.metaKey) {
        toggleBlockSelection(block.id);
      } else {
        selectBlock(block.id);
      }
    },
    [selectBlock, toggleBlockSelection],
  );

  const handleBlockDragStart = useCallback(
    (_e: KonvaEventObject<DragEvent>, block: Block) => {
      isDraggingBlock.current = true;
      if (!selectedBlockIds.includes(block.id)) {
        selectBlock(block.id);
      }
      setState(ViewerState.MOVING_BLOCK);
    },
    [selectedBlockIds, selectBlock, setState],
  );

  const handleBlockDragEnd = useCallback(
    (e: KonvaEventObject<DragEvent>, block: Block) => {
      isDraggingBlock.current = false;
      const node = e.target;
      const rect = blockRect(block);

      const dx = node.x() - rect.x;
      const dy = node.y() - rect.y;

      const [nx1, ny1, nx2, ny2] = block.coords_norm;
      const dxNorm = dx / width;
      const dyNorm = dy / height;

      const newCoords: [number, number, number, number] = [
        Math.max(0, Math.min(1, nx1 + dxNorm)),
        Math.max(0, Math.min(1, ny1 + dyNorm)),
        Math.max(0, Math.min(1, nx2 + dxNorm)),
        Math.max(0, Math.min(1, ny2 + dyNorm)),
      ];

      moveBlock(block.id, newCoords);
      setState(ViewerState.IDLE);
    },
    [blockRect, width, height, moveBlock, setState],
  );

  // -----------------------------------------------------------------------
  // Resize handle interaction
  // -----------------------------------------------------------------------
  const handleResizeStart = useCallback(
    (
      e: KonvaEventObject<MouseEvent>,
      block: Block,
      handlePos: HandlePos,
    ) => {
      e.cancelBubble = true;
      const pos = e.target.getStage()?.getPointerPosition();
      if (!pos) return;

      setResizeHandle(handlePos);
      setResizeBlockId(block.id);
      setResizeStartCoords([...block.coords_norm]);
      setResizeStartPos({ x: pos.x, y: pos.y });
      setState(ViewerState.RESIZING_BLOCK);
    },
    [setState],
  );

  // -----------------------------------------------------------------------
  // Keyboard: Delete selected blocks
  // -----------------------------------------------------------------------
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        selectedBlockIds.length > 0
      ) {
        deleteBlocks(selectedBlockIds);
        clearSelection();
      }
    },
    [selectedBlockIds, deleteBlocks, clearSelection],
  );

  // Proper effect-based keyboard listener (fixed leak from callback ref)
  const stageContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = stageContainerRef.current;
    if (!node) return;
    node.addEventListener("keydown", handleKeyDown);
    return () => node.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  // -----------------------------------------------------------------------
  // Drawing preview rect
  // -----------------------------------------------------------------------
  const drawRect =
    drawStart && drawCurrent
      ? {
          x: Math.min(drawStart.x, drawCurrent.x),
          y: Math.min(drawStart.y, drawCurrent.y),
          w: Math.abs(drawCurrent.x - drawStart.x),
          h: Math.abs(drawCurrent.y - drawStart.y),
        }
      : null;

  // Compute cursor: active interaction overrides hover state
  let cursor = stageCursor;
  if (viewerState === ViewerState.DRAWING_RECT) cursor = "crosshair";
  else if (viewerState === ViewerState.MOVING_BLOCK) cursor = "move";
  else if (viewerState === ViewerState.RESIZING_BLOCK && resizeHandle)
    cursor = HANDLE_CURSORS[resizeHandle];

  return (
    <div
      ref={stageContainerRef}
      tabIndex={0}
      style={{ outline: "none" }}
    >
      <Stage
        width={width}
        height={height}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        style={{ cursor }}
      >
        <Layer>
          {/* Existing blocks */}
          {blocks.map((block, index) => {
            const r = blockRect(block);
            const color = getBlockColor(block.block_type);
            const isSelected = selectedBlockIds.includes(block.id);

            return (
              <Group key={block.id}>
                {/* Main block group - draggable */}
                <Group
                  x={r.x}
                  y={r.y}
                  draggable={viewerState !== ViewerState.RESIZING_BLOCK}
                  onClick={(e) => handleBlockClick(e, block)}
                  onDragStart={(e) => handleBlockDragStart(e, block)}
                  onDragEnd={(e) => handleBlockDragEnd(e, block)}
                  onMouseEnter={() => {
                    if (viewerState === ViewerState.IDLE)
                      setStageCursor("move");
                  }}
                  onMouseLeave={() => {
                    if (viewerState === ViewerState.IDLE)
                      setStageCursor("crosshair");
                  }}
                >
                  {/* Block fill */}
                  <Rect
                    width={r.w}
                    height={r.h}
                    fill={color}
                    opacity={0.15}
                  />
                  {/* Block border */}
                  <Rect
                    width={r.w}
                    height={r.h}
                    stroke={isSelected ? "#3B82F6" : color}
                    strokeWidth={isSelected ? 3 : 2}
                  />
                  {/* Block number label */}
                  <Rect
                    x={r.w - 22}
                    y={0}
                    width={22}
                    height={18}
                    fill={color}
                    cornerRadius={[0, 0, 0, 4]}
                  />
                  <Text
                    x={r.w - 22}
                    y={2}
                    width={22}
                    height={16}
                    text={String(index + 1)}
                    fontSize={12}
                    fontStyle="bold"
                    fill="#fff"
                    align="center"
                  />
                </Group>

                {/* Resize handles -- rendered outside the draggable group so they don't move the block */}
                {isSelected &&
                  viewerState !== ViewerState.DRAWING_RECT &&
                  getHandlePositions(r.w, r.h).map((h) => (
                    <Rect
                      key={h.pos}
                      x={r.x + h.x - HANDLE_HALF}
                      y={r.y + h.y - HANDLE_HALF}
                      width={HANDLE_SIZE}
                      height={HANDLE_SIZE}
                      fill="#fff"
                      stroke="#3B82F6"
                      strokeWidth={1.5}
                      onMouseDown={(e) => handleResizeStart(e, block, h.pos)}
                      onMouseEnter={() =>
                        setStageCursor(HANDLE_CURSORS[h.pos])
                      }
                      onMouseLeave={() => setStageCursor("crosshair")}
                    />
                  ))}
              </Group>
            );
          })}

          {/* Drawing preview rectangle */}
          {drawRect && (
            <Rect
              x={drawRect.x}
              y={drawRect.y}
              width={drawRect.w}
              height={drawRect.h}
              stroke={getBlockColor(activeBlockType)}
              strokeWidth={2}
              dash={[6, 3]}
              fill={getBlockColor(activeBlockType)}
              opacity={0.1}
              listening={false}
            />
          )}
        </Layer>
      </Stage>
    </div>
  );
}
