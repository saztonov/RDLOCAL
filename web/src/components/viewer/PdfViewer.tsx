import { useMemo } from 'react'
import { useDocumentStore } from '../../stores/documentStore'
import { getPageImageUrl } from '../../api/pdf'

export function PdfViewer() {
  const nodeId = useDocumentStore((s) => s.nodeId)
  const pdfInfo = useDocumentStore((s) => s.pdfInfo)
  const currentPage = useDocumentStore((s) => s.currentPage)
  const loading = useDocumentStore((s) => s.loading)
  const error = useDocumentStore((s) => s.error)

  const imageUrl = useMemo(() => {
    if (!nodeId || !pdfInfo) return null
    return getPageImageUrl(nodeId, currentPage)
  }, [nodeId, pdfInfo, currentPage])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
          <span className="text-sm">Загрузка PDF...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-red-400 text-sm bg-red-900/20 px-4 py-3 rounded-lg max-w-md text-center">
          {error}
        </div>
      </div>
    )
  }

  if (!imageUrl) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Выберите документ в дереве проектов
      </div>
    )
  }

  return (
    <div className="flex items-start justify-center h-full overflow-auto p-4">
      <img
        src={imageUrl}
        alt={`Страница ${currentPage + 1}`}
        className="max-w-full shadow-2xl rounded"
        draggable={false}
      />
    </div>
  )
}
