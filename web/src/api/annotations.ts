import type { Document } from "../models/document.ts";
import { fetchApi } from "./client.ts";

export interface AnnotationResponse {
  node_id: string;
  data: Document;
}

export interface SaveAnnotationResponse {
  ok: true;
}

/** Fetch the annotation document for a given node. */
export async function getAnnotation(
  nodeId: string,
): Promise<AnnotationResponse> {
  return fetchApi<AnnotationResponse>(`/api/annotations/${nodeId}`);
}

/** Persist an updated annotation document for a given node. */
export async function saveAnnotation(
  nodeId: string,
  data: Document,
): Promise<SaveAnnotationResponse> {
  return fetchApi<SaveAnnotationResponse>(`/api/annotations/${nodeId}`, {
    method: "PUT",
    body: JSON.stringify({ data }),
  });
}
