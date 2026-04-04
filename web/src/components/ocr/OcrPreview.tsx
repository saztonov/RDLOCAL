import { useMemo } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { useViewerStore } from "../../stores/viewerStore";
import type { Block } from "../../models/block";
import { getBlockColor } from "../../models/block";
import { BlockType } from "../../models/enums";

const BLOCK_TYPE_LABELS: Record<BlockType, string> = {
  [BlockType.TEXT]: "Текст",
  [BlockType.IMAGE]: "Изображение",
  [BlockType.STAMP]: "Печать",
};

export default function OcrPreview() {
  const document = useDocumentStore((s) => s.document);
  const selectedBlockIds = useViewerStore((s) => s.selectedBlockIds);

  const selectedBlock: Block | null = useMemo(() => {
    if (!document || selectedBlockIds.length === 0) return null;

    const targetId = selectedBlockIds[0];
    for (const page of document.pages) {
      const found = page.blocks.find((b) => b.id === targetId);
      if (found) return found;
    }
    return null;
  }, [document, selectedBlockIds]);

  if (!selectedBlock) {
    return (
      <div className="flex h-full items-center justify-center bg-gray-900 text-sm text-gray-500">
        {!document
          ? "Нет документа"
          : selectedBlockIds.length === 0
            ? "Блок не выбран"
            : "Блок не найден"}
      </div>
    );
  }

  const label =
    BLOCK_TYPE_LABELS[selectedBlock.block_type] ?? selectedBlock.block_type;
  const color = getBlockColor(selectedBlock.block_type);
  const hasHtml = !!selectedBlock.ocr_html;
  const hasText = !!selectedBlock.ocr_text;

  return (
    <div className="flex h-full flex-col bg-gray-900 text-gray-300">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-700 px-3 py-2">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span className="text-xs font-medium text-white">{label}</span>
        <span className="ml-auto truncate text-[10px] text-gray-500">
          {selectedBlock.id.slice(0, 8)}
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        {hasHtml ? (
          <div
            className="prose prose-invert prose-sm max-w-none"
            dangerouslySetInnerHTML={{ __html: selectedBlock.ocr_html! }}
          />
        ) : hasText ? (
          <pre className="whitespace-pre-wrap break-words text-sm leading-relaxed text-gray-300">
            {selectedBlock.ocr_text}
          </pre>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-gray-500">
            Результат OCR отсутствует
          </div>
        )}
      </div>
    </div>
  );
}
