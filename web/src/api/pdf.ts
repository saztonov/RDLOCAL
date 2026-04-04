import { fetchApi } from "./client.ts";

export interface PdfPageInfo {
  page_index: number;
  width: number;
  height: number;
}

export interface PdfInfo {
  node_id: string;
  page_count: number;
  pages: PdfPageInfo[];
}

/** Fetch PDF metadata (page count, dimensions). */
export async function getPdfInfo(nodeId: string): Promise<PdfInfo> {
  return fetchApi<PdfInfo>(`/api/pdf/${nodeId}/info`);
}

/** Build the URL for a rendered PDF page image. */
export function getPageImageUrl(nodeId: string, pageNum: number, dpi = 150): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? window.location.origin;
  return `${base}/api/pdf/${nodeId}/page/${pageNum}?dpi=${dpi}`;
}
