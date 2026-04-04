export type NodeType = "project" | "folder" | "document";

export interface TreeNode {
  id: string;
  parent_id: string | null;
  node_type: NodeType;
  name: string;
  code: string | null;
  status: string;
  attributes: Record<string, unknown>;
  sort_order: number;
  version: number;
  created_at: string;
  updated_at: string;
  children?: TreeNode[];
}
