import type { TreeNode } from "../models/tree.ts";
import { fetchApi } from "./client.ts";

/** Fetch all root-level tree nodes. */
export async function getRootNodes(): Promise<TreeNode[]> {
  return fetchApi<TreeNode[]>("/api/tree/nodes/root");
}

/** Fetch direct children of a given parent node. */
export async function getChildren(parentId: string): Promise<TreeNode[]> {
  return fetchApi<TreeNode[]>(`/api/tree/nodes/${parentId}/children`);
}

/** Fetch a single tree node by id. */
export async function getNode(nodeId: string): Promise<TreeNode> {
  return fetchApi<TreeNode>(`/api/tree/nodes/${nodeId}`);
}

export interface NodeFile {
  id: string;
  node_id: string;
  file_type: string;
  file_url: string;
  [key: string]: unknown;
}

/** Fetch files attached to a node, optionally filtered by file type. */
export async function getNodeFiles(
  nodeId: string,
  fileType?: string,
): Promise<NodeFile[]> {
  const query = fileType ? `?file_type=${encodeURIComponent(fileType)}` : "";
  return fetchApi<NodeFile[]>(`/api/tree/nodes/${nodeId}/files${query}`);
}
