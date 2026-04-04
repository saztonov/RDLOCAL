import type { Block } from "./block.ts";

export interface Page {
  page_number: number;
  width: number;
  height: number;
  blocks: Block[];
}

export interface Document {
  pdf_path: string;
  format_version: number;
  pages: Page[];
}
