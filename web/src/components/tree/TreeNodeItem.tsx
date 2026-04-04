import { useCallback } from 'react'
import { useTreeStore } from '../../stores/treeStore'
import { useDocumentStore } from '../../stores/documentStore'
import type { TreeNode } from '../../models/tree'

interface TreeNodeItemProps {
  node: TreeNode
  level: number
}

const NODE_ICONS: Record<string, string> = {
  project: '\uD83D\uDCC1',
  folder: '\uD83D\uDCC1',
  document: '\uD83D\uDCC4',
}

export function TreeNodeItem({ node, level }: TreeNodeItemProps) {
  const expandedIds = useTreeStore((s) => s.expandedIds)
  const selectedNodeId = useTreeStore((s) => s.selectedNodeId)
  const toggleExpand = useTreeStore((s) => s.toggleExpand)
  const selectNode = useTreeStore((s) => s.selectNode)
  const loadChildren = useTreeStore((s) => s.loadChildren)
  const loadDocument = useDocumentStore((s) => s.loadDocument)

  const isExpanded = expandedIds.has(node.id)
  const isSelected = selectedNodeId === node.id
  const isExpandable = node.node_type !== 'document'
  const hasLoadedChildren = node.children !== undefined

  const handleExpandClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      if (!isExpandable) return

      if (!hasLoadedChildren) {
        loadChildren(node.id)
      }
      toggleExpand(node.id)
    },
    [isExpandable, hasLoadedChildren, loadChildren, toggleExpand, node.id],
  )

  const handleNodeClick = useCallback(() => {
    selectNode(node.id)
    if (node.node_type === 'document') {
      loadDocument(node.id)
    } else {
      if (!hasLoadedChildren) {
        loadChildren(node.id)
      }
      toggleExpand(node.id)
    }
  }, [selectNode, node.id, node.node_type, loadDocument, hasLoadedChildren, loadChildren, toggleExpand])

  const icon = NODE_ICONS[node.node_type] ?? '\uD83D\uDCC4'

  return (
    <div>
      <div
        onClick={handleNodeClick}
        className={`flex items-center gap-1 px-2 py-1 cursor-pointer rounded text-sm select-none transition-colors ${
          isSelected
            ? 'bg-blue-600/30 text-white'
            : 'text-gray-300 hover:bg-gray-700/50'
        }`}
        style={{ paddingLeft: `${level * 20 + 8}px` }}
      >
        {/* Expand/collapse indicator */}
        {isExpandable ? (
          <button
            onClick={handleExpandClick}
            className="w-4 h-4 flex items-center justify-center text-[10px] text-gray-400 hover:text-white shrink-0 transition-colors"
          >
            {isExpanded ? '\u25BC' : '\u25B6'}
          </button>
        ) : (
          <span className="w-4 h-4 shrink-0" />
        )}

        {/* Icon */}
        <span className="shrink-0 text-sm">{icon}</span>

        {/* Name */}
        <span className="truncate">{node.name}</span>
      </div>

      {/* Children */}
      {isExpandable && isExpanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeNodeItem key={child.id} node={child} level={level + 1} />
          ))}
        </div>
      )}
    </div>
  )
}
