import { create } from "zustand";
import type { Job } from "../models/job";
import * as jobsApi from "../api/jobs";

interface JobEvent {
  job_id: string;
  type: string;
  progress?: number;
  message?: string;
  status?: string;
}

interface JobsStore {
  jobs: Job[];
  loading: boolean;

  loadJobs: (documentId?: string) => Promise<void>;
  createJob: (
    nodeId: string,
    documentId: string,
    documentName: string,
  ) => Promise<void>;
  cancelJob: (jobId: string) => Promise<void>;
  updateJobFromEvent: (event: JobEvent) => void;
}

export const useJobsStore = create<JobsStore>((set, get) => ({
  jobs: [],
  loading: false,

  loadJobs: async (documentId?: string) => {
    set({ loading: true });
    try {
      const response = await jobsApi.listJobs(documentId);
      set({ jobs: response.jobs, loading: false });
    } catch (err) {
      console.error("Failed to load jobs:", err);
      set({ loading: false });
    }
  },

  createJob: async (
    nodeId: string,
    documentId: string,
    documentName: string,
  ) => {
    try {
      const job = await jobsApi.createNodeJob({
        node_id: nodeId,
        document_id: documentId,
        document_name: documentName,
        client_id: "web",
        task_name: "",
        engine: "lmstudio",
      });
      set({ jobs: [...get().jobs, job] });
    } catch (err) {
      console.error("Failed to create job:", err);
    }
  },

  cancelJob: async (jobId: string) => {
    try {
      await jobsApi.cancelJob(jobId);
      set({
        jobs: get().jobs.map((j) =>
          j.id === jobId ? { ...j, status: "cancelled" as const } : j,
        ),
      });
    } catch (err) {
      console.error("Failed to cancel job:", err);
    }
  },

  updateJobFromEvent: (event: JobEvent) => {
    const { jobs } = get();
    const index = jobs.findIndex((j) => j.id === event.job_id);

    if (index === -1) return;

    const updated = [...jobs];
    const current = updated[index];
    updated[index] = {
      ...current,
      ...(event.status !== undefined && { status: event.status as Job["status"] }),
      ...(event.progress !== undefined && { progress: event.progress }),
      ...(event.message !== undefined && { status_message: event.message }),
    };

    set({ jobs: updated });
  },
}));
