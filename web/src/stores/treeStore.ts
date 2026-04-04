import { create } from "zustand";
import type { TreeNode } from "../models/tree";
import * as treeApi from "../api/tree";

interface TreeStore {
  roots: TreeNode[];
  expandedIds: Set<string>;
  selectedNodeId: string | null;
  loading: boolean;
  error: string | null;

  loadRoots: () => Promise<void>;
  loadChildren: (parentId: string) => Promise<void>;
  toggleExpand: (nodeId: string) => void;
  selectNode: (nodeId: string) => void;
}

export const useTreeStore = create<TreeStore>((set, get) => ({
  roots: [],
  expandedIds: new Set(),
  selectedNodeId: null,
  loading: false,
  error: null,

  loadRoots: async () => {
    set({ loading: true, error: null });
    try {
      const roots = await treeApi.getRootNodes();
      set({ roots, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to load roots",
        loading: false,
      });
    }
  },

  loadChildren: async (parentId: string) => {
    set({ loading: true, error: null });
    try {
      const children = await treeApi.getChildren(parentId);
      const { roots } = get();

      const attachChildren = (nodes: TreeNode[]): TreeNode[] =>
        nodes.map((node) => {
          if (node.id === parentId) {
            return { ...node, children };
          }
          if (node.children && node.children.length > 0) {
            return { ...node, children: attachChildren(node.children) };
          }
          return node;
        });

      set({ roots: attachChildren(roots), loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to load children",
        loading: false,
      });
    }
  },

  toggleExpand: (nodeId: string) => {
    const { expandedIds } = get();
    const next = new Set(expandedIds);
    if (next.has(nodeId)) {
      next.delete(nodeId);
    } else {
      next.add(nodeId);
    }
    set({ expandedIds: next });
  },

  selectNode: (nodeId: string) => {
    set({ selectedNodeId: nodeId });
  },
}));
