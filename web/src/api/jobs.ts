import type { Job } from "../models/job.ts";
import { fetchApi } from "./client.ts";

export interface JobsListResponse {
  jobs: Job[];
  server_time: string;
}

export interface CreateNodeJobParams {
  node_id: string;
  task_name: string;
  [key: string]: unknown;
}

export interface OkResponse {
  ok: true;
}

/** List jobs, optionally filtered by document id. */
export async function listJobs(documentId?: string): Promise<JobsListResponse> {
  const query = documentId
    ? `?document_id=${encodeURIComponent(documentId)}`
    : "";
  return fetchApi<JobsListResponse>(`/jobs${query}`);
}

/** Create a new OCR job for a tree node. */
export async function createNodeJob(
  params: CreateNodeJobParams,
): Promise<Job> {
  return fetchApi<Job>("/jobs/node", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

/** Cancel a running or queued job. */
export async function cancelJob(jobId: string): Promise<OkResponse> {
  return fetchApi<OkResponse>(`/jobs/${jobId}/cancel`, { method: "POST" });
}

/** Fetch a single job by id. */
export async function getJob(jobId: string): Promise<Job> {
  return fetchApi<Job>(`/jobs/${jobId}`);
}
