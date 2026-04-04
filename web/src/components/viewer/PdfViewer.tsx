import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { useViewerStore } from "../../stores/viewerStore";
import { BlockCanvas } from "./BlockCanvas";
import { PageNavigation } from "./PageNavigation";
import { BlockToolbar } from "./BlockToolbar";

const BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? window.location.origin;

/**
 * Main PDF viewer component.
 *
 * Fetches the current page as a raster image from the server-side rendering
 * endpoint and overlays the Konva-based BlockCanvas for annotation drawing.
 */
export function PdfViewer() {
  const nodeId = useDocumentStore((s) => s.nodeId);
  const document = useDocumentStore((s) => s.document);
  const currentPage = useDocumentStore((s) => s.currentPage);
  const loading = useDocumentStore((s) => s.loading);

  const zoom = useViewerStore((s) => s.zoom);
  const setZoom = useViewerStore((s) => s.setZoom);

  const containerRef = useRef<HTMLDivElement>(null);
  const [pageImageUrl, setPageImageUrl] = useState<string | null>(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  // Dimensions of the current page from the document model (in PDF points).
  const page = document?.pages[currentPage];
  const pageWidth = page?.width ?? 0;
  const pageHeight = page?.height ?? 0;

  // Scaled dimensions used for both the image and the Konva overlay.
  const canvasWidth = Math.round(pageWidth * zoom);
  const canvasHeight = Math.round(pageHeight * zoom);

  // -----------------------------------------------------------------------
  // Fetch page image whenever nodeId / currentPage change
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!nodeId) {
      setPageImageUrl(null);
      return;
    }

    setImageLoaded(false);
    setImageError(false);

    // pageNum is 1-based for the API.
    const pageNum = currentPage + 1;
    const url = `${BASE_URL}/api/pdf/${encodeURIComponent(nodeId)}/page/${pageNum}?dpi=150`;
    setPageImageUrl(url);
  }, [nodeId, currentPage]);

  // -----------------------------------------------------------------------
  // Ctrl+Wheel zoom
  // -----------------------------------------------------------------------
  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      setZoom(zoom + delta);
    },
    [zoom, setZoom],
  );

  // -----------------------------------------------------------------------
  // Empty state
  // -----------------------------------------------------------------------
  if (!nodeId || !document) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gray-900 text-gray-400">
        <p className="text-lg">Откройте документ</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gray-900 text-gray-400">
        <p className="text-lg">Загрузка...</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-gray-900">
      {/* Top toolbar */}
      <div className="flex items-center gap-4 border-b border-gray-700 bg-gray-800 px-4 py-2">
        <BlockToolbar />
        <div className="mx-auto" />
        <PageNavigation />
        <span className="ml-4 text-sm text-gray-400">
          {Math.round(zoom * 100)}%
        </span>
      </div>

      {/* Scrollable viewer area */}
      <div
        ref={containerRef}
        className="relative flex-1 overflow-auto"
        onWheel={handleWheel}
      >
        <div
          className="relative mx-auto my-4"
          style={{ width: canvasWidth, height: canvasHeight }}
        >
          {/* PDF page image */}
          {pageImageUrl && (
            <img
              src={pageImageUrl}
              alt={`Page ${currentPage + 1}`}
              width={canvasWidth}
              height={canvasHeight}
              className="absolute left-0 top-0 select-none"
              style={{ imageRendering: "auto" }}
              draggable={false}
              onLoad={() => setImageLoaded(true)}
              onError={() => setImageError(true)}
            />
          )}

          {/* Error fallback */}
          {imageError && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-800 text-red-400">
              Не удалось загрузить страницу
            </div>
          )}

          {/* Konva block overlay */}
          {imageLoaded && pageWidth > 0 && pageHeight > 0 && (
            <div className="absolute left-0 top-0">
              <BlockCanvas
                width={canvasWidth}
                height={canvasHeight}
                pageWidth={pageWidth}
                pageHeight={pageHeight}
              />
            </div>
          )}

          {/* Page number badge */}
          <div className="pointer-events-none absolute bottom-2 right-2 rounded bg-black/60 px-2 py-1 text-xs text-white">
            {currentPage + 1} / {document.pages.length}
          </div>
        </div>
      </div>
    </div>
  );
}
