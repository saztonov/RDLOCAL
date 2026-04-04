import { useState, useCallback } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { useViewerStore } from "../../stores/viewerStore";
import type { Block } from "../../models/block";
import { getBlockColor } from "../../models/block";
import { BlockType } from "../../models/enums";
import BlockContextMenu from "./BlockContextMenu";

const BLOCK_TYPE_LABELS: Record<BlockType, string> = {
  [BlockType.TEXT]: "ТЕКСТ",
  [BlockType.IMAGE]: "ИЗОБРАЖЕНИЕ",
  [BlockType.STAMP]: "ПЕЧАТЬ",
};

export default function BlocksTree() {
  const document = useDocumentStore((s) => s.document);
  const currentPage = useDocumentStore((s) => s.currentPage);
  const setCurrentPage = useDocumentStore((s) => s.setCurrentPage);
  const deleteBlock = useDocumentStore((s) => s.deleteBlock);

  const selectedBlockIds = useViewerStore((s) => s.selectedBlockIds);
  const selectBlock = useViewerStore((s) => s.selectBlock);

  const [expandedPages, setExpandedPages] = useState<Set<number>>(new Set());
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    blockId: string;
    pageIndex: number;
  } | null>(null);

  const togglePage = useCallback((pageIndex: number) => {
    setExpandedPages((prev) => {
      const next = new Set(prev);
      if (next.has(pageIndex)) {
        next.delete(pageIndex);
      } else {
        next.add(pageIndex);
      }
      return next;
    });
  }, []);

  const handlePageClick = useCallback(
    (pageIndex: number) => {
      setCurrentPage(pageIndex);
      togglePage(pageIndex);
    },
    [setCurrentPage, togglePage],
  );

  const handleBlockClick = useCallback(
    (block: Block, pageIndex: number) => {
      if (currentPage !== pageIndex) {
        setCurrentPage(pageIndex);
      }
      selectBlock(block.id);
    },
    [currentPage, setCurrentPage, selectBlock],
  );

  const handleBlockContextMenu = useCallback(
    (e: React.MouseEvent, blockId: string, pageIndex: number) => {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY, blockId, pageIndex });
    },
    [],
  );

  const handleDeleteBlock = useCallback(() => {
    if (!contextMenu) return;
    // Navigate to the block's page before deleting so the store targets the right page
    if (currentPage !== contextMenu.pageIndex) {
      setCurrentPage(contextMenu.pageIndex);
    }
    deleteBlock(contextMenu.blockId);
  }, [contextMenu, currentPage, setCurrentPage, deleteBlock]);

  if (!document) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-500">
        Нет документа
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-gray-900 text-sm text-gray-300">
      <div className="border-b border-gray-700 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
        Блоки
      </div>

      <div className="flex-1 overflow-y-auto">
        {document.pages.map((page, pageIndex) => {
          const isExpanded = expandedPages.has(pageIndex);
          const isCurrentPage = pageIndex === currentPage;

          return (
            <div key={pageIndex}>
              {/* Page header */}
              <button
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-gray-800 ${
                  isCurrentPage
                    ? "bg-gray-800 text-white"
                    : "text-gray-300"
                }`}
                onClick={() => handlePageClick(pageIndex)}
              >
                <span
                  className={`text-[10px] transition-transform ${
                    isExpanded ? "rotate-90" : ""
                  }`}
                >
                  &#9654;
                </span>
                <span className="font-medium">
                  Страница {pageIndex + 1}
                </span>
                <span className="ml-auto text-xs text-gray-500">
                  {page.blocks.length}
                </span>
              </button>

              {/* Block list */}
              {isExpanded && (
                <div className="pl-4">
                  {page.blocks.length === 0 ? (
                    <div className="px-3 py-1 text-xs text-gray-600">
                      Нет блоков
                    </div>
                  ) : (
                    page.blocks.map((block, blockIdx) => {
                      const isSelected = selectedBlockIds.includes(block.id);
                      const color = getBlockColor(block.block_type);
                      const label =
                        BLOCK_TYPE_LABELS[block.block_type] ??
                        block.block_type;

                      return (
                        <button
                          key={block.id}
                          className={`flex w-full items-center gap-2 px-3 py-1 text-left text-xs hover:bg-gray-800 ${
                            isSelected
                              ? "bg-blue-900/40 text-white"
                              : "text-gray-400"
                          }`}
                          onClick={() => handleBlockClick(block, pageIndex)}
                          onContextMenu={(e) =>
                            handleBlockContextMenu(e, block.id, pageIndex)
                          }
                        >
                          <span
                            className="inline-block h-2 w-2 flex-shrink-0 rounded-full"
                            style={{ backgroundColor: color }}
                          />
                          <span className="truncate">
                            Блок {blockIdx + 1} — {label}
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <BlockContextMenu
        x={contextMenu?.x ?? 0}
        y={contextMenu?.y ?? 0}
        visible={contextMenu !== null}
        onClose={() => setContextMenu(null)}
        onDelete={handleDeleteBlock}
      />
    </div>
  );
}
