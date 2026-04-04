import { useEffect, useRef } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { useJobsStore } from "../../stores/jobsStore";
import { createJobsWebSocket } from "../../api/websocket";
const STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  processing: "Обработка",
  done: "Готово",
  error: "Ошибка",
  cancelled: "Отменено",
};

const STATUS_STYLES: Record<string, string> = {
  queued: "bg-yellow-900/50 text-yellow-400",
  processing: "bg-blue-900/50 text-blue-400",
  done: "bg-green-900/50 text-green-400",
  error: "bg-red-900/50 text-red-400",
  cancelled: "bg-gray-700 text-gray-400",
};

function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? "bg-gray-700 text-gray-400";
  const label = STATUS_LABELS[status] ?? status;

  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-[11px] font-medium ${style}`}
    >
      {label}
    </span>
  );
}

function ProgressBar({ progress }: { progress: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(progress * 100)));

  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-700">
      <div
        className="h-full rounded-full bg-blue-500 transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export default function JobsPanel() {
  const nodeId = useDocumentStore((s) => s.nodeId);
  const document = useDocumentStore((s) => s.document);

  const jobs = useJobsStore((s) => s.jobs);
  const loading = useJobsStore((s) => s.loading);
  const loadJobs = useJobsStore((s) => s.loadJobs);
  const createJob = useJobsStore((s) => s.createJob);
  const cancelJob = useJobsStore((s) => s.cancelJob);
  const updateJobFromEvent = useJobsStore((s) => s.updateJobFromEvent);

  const wsRef = useRef<{ close: () => void } | null>(null);

  // Load jobs on mount and when nodeId changes
  useEffect(() => {
    loadJobs(nodeId ?? undefined);
  }, [nodeId, loadJobs]);

  // WebSocket connection
  useEffect(() => {
    const handle = createJobsWebSocket((event) => {
      updateJobFromEvent({
        job_id: event.job_id ?? "",
        type: event.type,
        status: event.status,
        progress: event.progress,
        message: event.error_message,
      });
    });
    wsRef.current = handle;

    return () => {
      handle.close();
      wsRef.current = null;
    };
  }, [updateJobFromEvent]);

  const handleCreateJob = () => {
    if (!nodeId || !document) return;
    createJob(nodeId, nodeId, document.pdf_path);
  };

  const canCancel = (status: string) =>
    status === "queued" || status === "processing";

  return (
    <div className="flex h-full flex-col bg-gray-900 text-gray-300">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-700 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          OCR Задачи
        </span>
        <button
          className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!nodeId || !document}
          onClick={handleCreateJob}
        >
          Запустить OCR
        </button>
      </div>

      {/* Jobs table */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8 text-sm text-gray-500">
            Загрузка...
          </div>
        ) : jobs.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-sm text-gray-500">
            Нет задач
          </div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-gray-800 text-gray-400">
              <tr>
                <th className="px-3 py-2 font-medium">Документ</th>
                <th className="px-3 py-2 font-medium">Статус</th>
                <th className="px-3 py-2 font-medium">Прогресс</th>
                <th className="px-3 py-2 font-medium">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-gray-800/50">
                  <td className="max-w-[150px] truncate px-3 py-2 text-gray-300">
                    {job.document_name}
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-3 py-2">
                    {job.status === "processing" ? (
                      <ProgressBar progress={job.progress} />
                    ) : job.status === "done" ? (
                      <span className="text-green-400">100%</span>
                    ) : job.status === "error" ? (
                      <span
                        className="max-w-[120px] truncate text-red-400"
                        title={job.error_message}
                      >
                        {job.error_message ?? "Ошибка"}
                      </span>
                    ) : (
                      <span className="text-gray-500">--</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {canCancel(job.status) ? (
                      <button
                        className="rounded px-2 py-0.5 text-[11px] text-red-400 hover:bg-red-900/30 hover:text-red-300"
                        onClick={() => cancelJob(job.id)}
                      >
                        Отменить
                      </button>
                    ) : (
                      <span className="text-gray-600">--</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
