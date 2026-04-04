import { useEffect, useRef } from "react";

interface BlockContextMenuProps {
  x: number;
  y: number;
  visible: boolean;
  onClose: () => void;
  onDelete: () => void;
}

export default function BlockContextMenu({
  x,
  y,
  visible,
  onClose,
  onDelete,
}: BlockContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!visible) return;

    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    }

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [visible, onClose]);

  if (!visible) return null;

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[160px] rounded border border-gray-700 bg-gray-800 py-1 shadow-lg"
      style={{ left: x, top: y }}
    >
      <button
        className="w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-gray-700 hover:text-white"
        onClick={() => {
          onDelete();
          onClose();
        }}
      >
        Удалить блок
      </button>
    </div>
  );
}
