import { useState, useCallback, useEffect } from 'react'
import { useDocumentStore } from '../../stores/documentStore'
import { useViewerStore } from '../../stores/viewerStore'
import { BlockType, ShapeType } from '../../models/enums'
import { ProjectTree } from '../tree/ProjectTree'
import { PdfViewer } from '../viewer/PdfViewer'
import BlocksTree from '../blocks/BlocksTree'
import OcrPreview from '../ocr/OcrPreview'
import JobsPanel from '../ocr/JobsPanel'

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
  const [rightTab, setRightTab] = useState<'blocks' | 'ocr' | 'jobs'>('blocks')

  const currentPage = useDocumentStore((s) => s.currentPage)
  const document = useDocumentStore((s) => s.document)
  const pdfInfo = useDocumentStore((s) => s.pdfInfo)
  const dirty = useDocumentStore((s) => s.dirty)
  const setCurrentPage = useDocumentStore((s) => s.setCurrentPage)
  const saveAnnotation = useDocumentStore((s) => s.saveAnnotation)

  const zoom = useViewerStore((s) => s.zoom)
  const zoomIn = useViewerStore((s) => s.zoomIn)
  const zoomOut = useViewerStore((s) => s.zoomOut)
  const activeBlockType = useViewerStore((s) => s.activeBlockType)
  const activeShapeType = useViewerStore((s) => s.activeShapeType)
  const setActiveBlockType = useViewerStore((s) => s.setActiveBlockType)
  const setActiveShapeType = useViewerStore((s) => s.setActiveShapeType)

  const totalPages = pdfInfo?.page_count ?? 0

  const handlePrevPage = useCallback(() => {
    if (currentPage > 0) setCurrentPage(currentPage - 1)
  }, [currentPage, setCurrentPage])

  const handleNextPage = useCallback(() => {
    if (currentPage < totalPages - 1) setCurrentPage(currentPage + 1)
  }, [currentPage, totalPages, setCurrentPage])

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey) {
        if (e.key === 'z' && !e.shiftKey) {
          e.preventDefault()
          useDocumentStore.getState().undo()
        } else if ((e.key === 'z' && e.shiftKey) || e.key === 'y') {
          e.preventDefault()
          useDocumentStore.getState().redo()
        } else if (e.key === 's') {
          e.preventDefault()
          useDocumentStore.getState().saveAnnotation()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

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

        {/* Separator */}
        <div className="w-px h-5 bg-gray-600" />

        {/* Undo / Redo */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => useDocumentStore.getState().undo()}
            className="px-2 py-1 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Отменить (Ctrl+Z)"
          >
            &#8630;
          </button>
          <button
            onClick={() => useDocumentStore.getState().redo()}
            className="px-2 py-1 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Повторить (Ctrl+Shift+Z)"
          >
            &#8631;
          </button>
        </div>

        <div className="flex-1" />

        {/* Save indicator */}
        {document && (
          <div className="flex items-center gap-2">
            {dirty ? (
              <button
                onClick={() => saveAnnotation()}
                className="flex items-center gap-1.5 px-2 py-1 text-xs rounded hover:bg-gray-700 transition-colors"
                title="Сохранить (Ctrl+S)"
              >
                <span className="inline-block h-2 w-2 rounded-full bg-yellow-400 animate-pulse" />
                <span className="text-yellow-400">Не сохранено</span>
              </button>
            ) : (
              <span className="flex items-center gap-1.5 px-2 py-1 text-xs text-gray-500">
                <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                Сохранено
              </span>
            )}
          </div>
        )}

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
        <main className="flex-1 flex overflow-hidden bg-gray-900">
          <PdfViewer />
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
              <button
                onClick={() => setRightTab('jobs')}
                className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
                  rightTab === 'jobs'
                    ? 'text-white border-b-2 border-blue-500'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                Задачи
              </button>
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-y-auto">
              {rightTab === 'blocks' ? (
                <BlocksTree />
              ) : rightTab === 'ocr' ? (
                <OcrPreview />
              ) : (
                <JobsPanel />
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
