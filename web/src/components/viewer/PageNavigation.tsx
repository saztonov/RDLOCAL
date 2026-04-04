import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentStore } from "../../stores/documentStore";

/**
 * Page navigation bar: previous / next buttons and an inline-editable page
 * number display.
 */
export function PageNavigation() {
  const document = useDocumentStore((s) => s.document);
  const currentPage = useDocumentStore((s) => s.currentPage);
  const setCurrentPage = useDocumentStore((s) => s.setCurrentPage);

  const totalPages = document?.pages.length ?? 0;

  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const canPrev = currentPage > 0;
  const canNext = currentPage < totalPages - 1;

  const goPrev = useCallback(() => {
    if (canPrev) setCurrentPage(currentPage - 1);
  }, [canPrev, currentPage, setCurrentPage]);

  const goNext = useCallback(() => {
    if (canNext) setCurrentPage(currentPage + 1);
  }, [canNext, currentPage, setCurrentPage]);

  // Focus input when entering edit mode.
  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const startEditing = useCallback(() => {
    setInputValue(String(currentPage + 1));
    setEditing(true);
  }, [currentPage]);

  const commitEdit = useCallback(() => {
    setEditing(false);
    const num = parseInt(inputValue, 10);
    if (!isNaN(num) && num >= 1 && num <= totalPages) {
      setCurrentPage(num - 1);
    }
  }, [inputValue, totalPages, setCurrentPage]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") commitEdit();
      if (e.key === "Escape") setEditing(false);
    },
    [commitEdit],
  );

  if (!document || totalPages === 0) return null;

  return (
    <div className="flex items-center gap-1 text-sm text-white">
      <button
        onClick={goPrev}
        disabled={!canPrev}
        className="rounded px-2 py-1 hover:bg-gray-700 disabled:opacity-30"
        aria-label="Предыдущая страница"
      >
        &lt;
      </button>

      {editing ? (
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={handleKeyDown}
          className="w-12 rounded border border-gray-600 bg-gray-700 px-1 py-0.5 text-center text-sm text-white"
        />
      ) : (
        <button
          onClick={startEditing}
          className="min-w-[4rem] rounded px-2 py-1 hover:bg-gray-700"
        >
          {currentPage + 1} / {totalPages}
        </button>
      )}

      <button
        onClick={goNext}
        disabled={!canNext}
        className="rounded px-2 py-1 hover:bg-gray-700 disabled:opacity-30"
        aria-label="Следующая страница"
      >
        &gt;
      </button>
    </div>
  );
}
