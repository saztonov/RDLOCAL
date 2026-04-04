import { useCallback, useRef, useState } from "react";
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

/**
 * Konva-based overlay that renders annotation blocks on top of the PDF page
 * and handles rectangle drawing, selection, and drag-move interactions.
 */
export function BlockCanvas({ width, height }: Props) {
  const getCurrentPageBlocks = useDocumentStore((s) => s.getCurrentPageBlocks);
  const addBlock = useDocumentStore((s) => s.addBlock);
  const moveBlock = useDocumentStore((s) => s.moveBlock);
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
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null);
  const [drawCurrent, setDrawCurrent] = useState<{ x: number; y: number } | null>(null);
  const isDraggingBlock = useRef(false);

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
      if (viewerState !== ViewerState.DRAWING_RECT || !drawStart) return;
      const pos = e.target.getStage()?.getPointerPosition();
      if (!pos) return;
      setDrawCurrent({ x: pos.x, y: pos.y });
    },
    [viewerState, drawStart],
  );

  const handleMouseUp = useCallback(() => {
    if (viewerState === ViewerState.DRAWING_RECT && drawStart && drawCurrent) {
      const x1 = Math.min(drawStart.x, drawCurrent.x);
      const y1 = Math.min(drawStart.y, drawCurrent.y);
      const x2 = Math.max(drawStart.x, drawCurrent.x);
      const y2 = Math.max(drawStart.y, drawCurrent.y);

      if (x2 - x1 > MIN_RECT_SIZE && y2 - y1 > MIN_RECT_SIZE) {
        const coordsNorm: CoordsNorm = pxToNorm([x1, y1, x2, y2], width, height);

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

  // Attach keyboard listener to the stage container via a wrapper div.
  const stageContainerRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) return;
      node.tabIndex = 0;
      node.style.outline = "none";
      node.addEventListener("keydown", handleKeyDown);
      return () => node.removeEventListener("keydown", handleKeyDown);
    },
    [handleKeyDown],
  );

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

  return (
    <div ref={stageContainerRef}>
      <Stage
        width={width}
        height={height}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        style={{ cursor: viewerState === ViewerState.DRAWING_RECT ? "crosshair" : "default" }}
      >
        <Layer>
          {/* Existing blocks */}
          {blocks.map((block, index) => {
            const r = blockRect(block);
            const color = getBlockColor(block.block_type);
            const isSelected = selectedBlockIds.includes(block.id);

            return (
              <Group
                key={block.id}
                x={r.x}
                y={r.y}
                draggable
                onClick={(e) => handleBlockClick(e, block)}
                onDragStart={(e) => handleBlockDragStart(e, block)}
                onDragEnd={(e) => handleBlockDragEnd(e, block)}
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
