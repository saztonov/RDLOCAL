export type JobStatus =
  | "queued"
  | "processing"
  | "done"
  | "error"
  | "cancelled";

export interface Job {
  id: string;
  status: JobStatus;
  progress: number;
  document_name: string;
  task_name: string;
  document_id: string;
  created_at: string;
  updated_at?: string;
  error_message?: string;
  node_id?: string;
  status_message?: string;
}
