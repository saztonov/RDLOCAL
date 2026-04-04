import { useViewerStore } from "../../stores/viewerStore";
import { BlockType, ShapeType } from "../../models/enums";

const BLOCK_TYPES: { value: BlockType; label: string }[] = [
  { value: BlockType.TEXT, label: "Текст" },
  { value: BlockType.IMAGE, label: "Изображение" },
  { value: BlockType.STAMP, label: "Печать" },
];

const SHAPE_TYPES: { value: ShapeType; label: string }[] = [
  { value: ShapeType.RECTANGLE, label: "\u25AD Прямоугольник" },
  { value: ShapeType.POLYGON, label: "\u2B1F Полигон" },
];

/**
 * Toolbar for selecting the active block type (TEXT / IMAGE / STAMP) and
 * shape mode (rectangle / polygon).
 */
export function BlockToolbar() {
  const activeBlockType = useViewerStore((s) => s.activeBlockType);
  const setActiveBlockType = useViewerStore((s) => s.setActiveBlockType);
  const activeShapeType = useViewerStore((s) => s.activeShapeType);
  const setActiveShapeType = useViewerStore((s) => s.setActiveShapeType);

  return (
    <div className="flex items-center gap-4 text-sm text-white">
      {/* Block type radio group */}
      <fieldset className="flex items-center gap-1">
        <legend className="sr-only">Тип блока</legend>
        {BLOCK_TYPES.map(({ value, label }) => (
          <button
            key={value}
            onClick={() => setActiveBlockType(value)}
            className={`rounded px-3 py-1 transition-colors ${
              activeBlockType === value
                ? "bg-blue-600 text-white"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600"
            }`}
            role="radio"
            aria-checked={activeBlockType === value}
          >
            {label}
          </button>
        ))}
      </fieldset>

      <div className="h-5 w-px bg-gray-600" aria-hidden="true" />

      {/* Shape type toggle */}
      <fieldset className="flex items-center gap-1">
        <legend className="sr-only">Форма блока</legend>
        {SHAPE_TYPES.map(({ value, label }) => (
          <button
            key={value}
            onClick={() => setActiveShapeType(value)}
            className={`rounded px-3 py-1 transition-colors ${
              activeShapeType === value
                ? "bg-blue-600 text-white"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600"
            }`}
            role="radio"
            aria-checked={activeShapeType === value}
          >
            {label}
          </button>
        ))}
      </fieldset>
    </div>
  );
}
