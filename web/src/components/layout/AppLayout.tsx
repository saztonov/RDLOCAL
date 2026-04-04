import { useState, useCallback } from 'react'
import { useDocumentStore } from '../../stores/documentStore'
import { useViewerStore } from '../../stores/viewerStore'
import { BlockType, ShapeType } from '../../models/enums'
import { ProjectTree } from '../tree/ProjectTree'

const BLOCK_TYPE_LABELS: Record<BlockType, string> = {
  [BlockType.TEXT]: 'TEXT',
  [BlockType.IMAGE]: 'IMAGE',
  [BlockType.STAMP]: 'STAMP',
}

const SHAPE_TYPE_LABELS: Record<ShapeType, string> = {
  [ShapeType.RECTANGLE]: 'RECT',
  [ShapeType.POLYGON]: 'POLYGON',
}

export function AppLayout() {
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const [rightTab, setRightTab] = useState<'blocks' | 'ocr'>('blocks')

  const currentPage = useDocumentStore((s) => s.currentPage)
  const document = useDocumentStore((s) => s.document)
  const setCurrentPage = useDocumentStore((s) => s.setCurrentPage)

  const zoom = useViewerStore((s) => s.zoom)
  const zoomIn = useViewerStore((s) => s.zoomIn)
  const zoomOut = useViewerStore((s) => s.zoomOut)
  const activeBlockType = useViewerStore((s) => s.activeBlockType)
  const activeShapeType = useViewerStore((s) => s.activeShapeType)
  const setActiveBlockType = useViewerStore((s) => s.setActiveBlockType)
  const setActiveShapeType = useViewerStore((s) => s.setActiveShapeType)

  const totalPages = document?.pages.length ?? 0

  const handlePrevPage = useCallback(() => {
    if (currentPage > 0) setCurrentPage(currentPage - 1)
  }, [currentPage, setCurrentPage])

  const handleNextPage = useCallback(() => {
    if (currentPage < totalPages - 1) setCurrentPage(currentPage + 1)
  }, [currentPage, totalPages, setCurrentPage])

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-white overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-gray-800 border-b border-gray-700 shrink-0">
        {/* Left sidebar toggle */}
        <button
          onClick={() => setLeftOpen((v) => !v)}
          className="px-2 py-1 text-sm rounded hover:bg-gray-700 transition-colors"
          title={leftOpen ? 'Скрыть дерево' : 'Показать дерево'}
        >
          {leftOpen ? '\u25C0' : '\u25B6'}
        </button>

        {/* Page navigation */}
        <div className="flex items-center gap-2">
          <button
            onClick={handlePrevPage}
            disabled={currentPage <= 0}
            className="px-2 py-1 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            \u25C0
          </button>
          <span className="text-sm tabular-nums min-w-[80px] text-center">
            {totalPages > 0 ? `${currentPage + 1} / ${totalPages}` : '-- / --'}
          </span>
          <button
            onClick={handleNextPage}
            disabled={currentPage >= totalPages - 1}
            className="px-2 py-1 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            \u25B6
          </button>
        </div>

        {/* Separator */}
        <div className="w-px h-5 bg-gray-600" />

        {/* Block type selector */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-gray-400 mr-1">Тип:</span>
          {Object.entries(BLOCK_TYPE_LABELS).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setActiveBlockType(value as BlockType)}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                activeBlockType === value
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Separator */}
        <div className="w-px h-5 bg-gray-600" />

        {/* Shape selector */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-gray-400 mr-1">Форма:</span>
          {Object.entries(SHAPE_TYPE_LABELS).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setActiveShapeType(value as ShapeType)}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                activeShapeType === value
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Separator */}
        <div className="w-px h-5 bg-gray-600" />

        {/* Zoom controls */}
        <div className="flex items-center gap-1">
          <button
            onClick={zoomOut}
            className="px-2 py-1 text-sm rounded bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            -
          </button>
          <span className="text-xs tabular-nums min-w-[48px] text-center">
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={zoomIn}
            className="px-2 py-1 text-sm rounded bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            +
          </button>
        </div>

        <div className="flex-1" />

        {/* Right panel toggle */}
        <button
          onClick={() => setRightOpen((v) => !v)}
          className="px-2 py-1 text-sm rounded hover:bg-gray-700 transition-colors"
          title={rightOpen ? 'Скрыть панель' : 'Показать панель'}
        >
          {rightOpen ? '\u25B6' : '\u25C0'}
        </button>
      </div>

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar */}
        {leftOpen && (
          <aside className="w-[280px] shrink-0 flex flex-col bg-gray-850 border-r border-gray-700 bg-gray-800/50">
            <div className="px-3 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider border-b border-gray-700">
              Дерево проектов
            </div>
            <div className="flex-1 overflow-y-auto">
              <ProjectTree />
            </div>
          </aside>
        )}

        {/* Center: PDF viewer */}
        <main className="flex-1 flex items-center justify-center overflow-auto bg-gray-900">
          {document ? (
            <div className="text-gray-500 text-sm">
              {/* PdfViewer will be mounted here */}
              PDF Viewer: {document.pdf_path}
            </div>
          ) : (
            <div className="text-gray-500 text-sm">
              Выберите документ в дереве проектов
            </div>
          )}
        </main>

        {/* Right panel */}
        {rightOpen && (
          <aside className="w-[320px] shrink-0 flex flex-col border-l border-gray-700 bg-gray-800/50">
            {/* Tabs */}
            <div className="flex border-b border-gray-700">
              <button
                onClick={() => setRightTab('blocks')}
                className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
                  rightTab === 'blocks'
                    ? 'text-white border-b-2 border-blue-500'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                Блоки
              </button>
              <button
                onClick={() => setRightTab('ocr')}
                className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
                  rightTab === 'ocr'
                    ? 'text-white border-b-2 border-blue-500'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                OCR
              </button>
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-y-auto p-3">
              {rightTab === 'blocks' ? (
                <div className="text-gray-500 text-sm">
                  {/* BlocksPanel will be mounted here */}
                  Панель блоков
                </div>
              ) : (
                <div className="text-gray-500 text-sm">
                  {/* OcrPreview will be mounted here */}
                  OCR предпросмотр
                </div>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
