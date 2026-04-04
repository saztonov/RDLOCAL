import { useEffect, useState, useMemo, useCallback, useRef } from 'react'
import { useTreeStore } from '../../stores/treeStore'
import { TreeNodeItem } from './TreeNodeItem'
import type { TreeNode } from '../../models/tree'

function filterNodes(nodes: TreeNode[], query: string): TreeNode[] {
  const lower = query.toLowerCase()
  return nodes.reduce<TreeNode[]>((acc, node) => {
    const childMatches = node.children ? filterNodes(node.children, query) : []
    const nameMatches = node.name.toLowerCase().includes(lower)

    if (nameMatches || childMatches.length > 0) {
      acc.push({
        ...node,
        children: nameMatches ? node.children : childMatches,
      })
    }
    return acc
  }, [])
}

export function ProjectTree() {
  const roots = useTreeStore((s) => s.roots)
  const loading = useTreeStore((s) => s.loading)
  const error = useTreeStore((s) => s.error)
  const loadRoots = useTreeStore((s) => s.loadRoots)

  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    loadRoots()
  }, [loadRoots])

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setSearchQuery(value)

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setDebouncedQuery(value)
    }, 300)
  }, [])

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const displayedNodes = useMemo(() => {
    if (!debouncedQuery.trim()) return roots
    return filterNodes(roots, debouncedQuery.trim())
  }, [roots, debouncedQuery])

  return (
    <div className="flex flex-col h-full">
      {/* Search */}
      <div className="p-2">
        <input
          type="text"
          value={searchQuery}
          onChange={handleSearchChange}
          placeholder="Поиск..."
          className="w-full px-2 py-1.5 text-sm bg-gray-700 border border-gray-600 rounded placeholder-gray-400 text-white focus:outline-none focus:border-blue-500 transition-colors"
        />
      </div>

      {/* Tree content */}
      <div className="flex-1 overflow-y-auto px-1 pb-2">
        {loading && roots.length === 0 && (
          <div className="text-gray-500 text-xs px-2 py-4 text-center">
            Загрузка...
          </div>
        )}

        {error && (
          <div className="text-red-400 text-xs px-2 py-2">
            {error}
          </div>
        )}

        {!loading && !error && displayedNodes.length === 0 && (
          <div className="text-gray-500 text-xs px-2 py-4 text-center">
            {debouncedQuery ? 'Ничего не найдено' : 'Нет проектов'}
          </div>
        )}

        {displayedNodes.map((node) => (
          <TreeNodeItem key={node.id} node={node} level={0} />
        ))}
      </div>
    </div>
  )
}
