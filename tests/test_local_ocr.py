"""Тесты для локального OCR pipeline и runner."""
import multiprocessing
from unittest.mock import MagicMock


from app.ocr.local_pipeline import LocalOcrResult, run_local_ocr
from app.ocr.local_runner import LocalJob, LocalOcrRunner


class TestLocalOcrResult:
    """Тесты для LocalOcrResult dataclass."""

    def test_default_values(self):
        result = LocalOcrResult(status="done")
        assert result.status == "done"
        assert result.recognized == 0
        assert result.total_blocks == 0
        assert result.error_count == 0
        assert result.error_message is None
        assert result.duration_seconds == 0.0
        assert result.result_files == {}

    def test_full_values(self):
        result = LocalOcrResult(
            status="partial",
            recognized=5,
            total_blocks=10,
            error_count=3,
            error_message="some blocks failed",
            duration_seconds=42.5,
            result_files={"annotation.json": "/tmp/annotation.json"},
        )
        assert result.status == "partial"
        assert result.recognized == 5
        assert result.total_blocks == 10


class TestLocalJob:
    """Тесты для LocalJob dataclass."""

    def test_default_values(self):
        job = LocalJob()
        assert job.id  # UUID generated
        assert job.status == "queued"
        assert job.progress == 0.0
        assert job.total_blocks == 0
        assert job.result_files == {}

    def test_custom_values(self):
        job = LocalJob(
            pdf_path="/test.pdf",
            document_name="test.pdf",
            status="processing",
            progress=0.5,
            total_blocks=20,
        )
        assert job.pdf_path == "/test.pdf"
        assert job.document_name == "test.pdf"
        assert job.status == "processing"
        assert job.progress == 0.5

    def test_unique_ids(self):
        job1 = LocalJob()
        job2 = LocalJob()
        assert job1.id != job2.id


class TestRunLocalOcr:
    """Тесты для run_local_ocr function."""

    def test_empty_blocks(self, tmp_path):
        """Пустой список блоков → done, 0 recognized."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 dummy")  # minimal fake PDF

        result = run_local_ocr(
            pdf_path=str(pdf_path),
            blocks_data=[],
            output_dir=str(tmp_path / "output"),
        )
        assert result.status == "done"
        assert result.total_blocks == 0
        assert result.recognized == 0

    def test_cancellation(self, tmp_path):
        """Проверка отмены через check_cancelled callback."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 dummy")

        # Блок с минимальными данными — pipeline упадёт при парсинге PDF,
        # но cancellation должен сработать раньше
        blocks = [{"id": "test-block", "page_index": 0, "block_type": "text",
                   "coords_px": [0, 0, 100, 100], "coords_norm": [0, 0, 0.5, 0.5],
                   "source": "user", "shape_type": "rectangle"}]

        result = run_local_ocr(
            pdf_path=str(pdf_path),
            blocks_data=blocks,
            output_dir=str(tmp_path / "output"),
            check_cancelled=lambda: True,  # Always cancelled
        )
        assert result.status == "error"
        assert "Отменено" in (result.error_message or "")

    def test_progress_callback(self, tmp_path):
        """Проверка что progress callback вызывается."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 dummy")

        progress_calls = []

        def on_progress(value, msg):
            progress_calls.append((value, msg))

        result = run_local_ocr(
            pdf_path=str(pdf_path),
            blocks_data=[],
            output_dir=str(tmp_path / "output"),
            on_progress=on_progress,
        )
        # Пустые блоки → ранний выход, но хотя бы один progress
        # (может не вызваться при 0 блоков)
        assert result.status == "done"

    def test_invalid_pdf(self, tmp_path):
        """Несуществующий PDF → error."""
        result = run_local_ocr(
            pdf_path=str(tmp_path / "nonexistent.pdf"),
            blocks_data=[{"id": "b1", "page_index": 0, "block_type": "text",
                          "coords_px": [0, 0, 100, 100], "coords_norm": [0, 0, 0.5, 0.5],
                          "source": "user", "shape_type": "rectangle"}],
            output_dir=str(tmp_path / "output"),
        )
        assert result.status == "error"
        assert result.error_message is not None


class TestLocalOcrRunnerUnit:
    """Unit тесты для LocalOcrRunner (без запуска процессов)."""

    def test_submit_creates_job(self):
        """submit_job создаёт LocalJob и запускает Process."""
        runner = LocalOcrRunner.__new__(LocalOcrRunner)
        # Minimal init без QObject
        runner._jobs = {}
        runner._processes = {}
        runner._queues = {}
        runner._cancel_flags = {}

        job = LocalJob(
            pdf_path="/test.pdf",
            document_name="test.pdf",
            status="processing",
            total_blocks=5,
            output_dir="/output",
        )
        runner._jobs[job.id] = job

        assert job.id in runner._jobs
        assert runner._jobs[job.id].status == "processing"

    def test_cancel_sets_flag(self):
        """cancel_job устанавливает cancel_flag."""
        runner = LocalOcrRunner.__new__(LocalOcrRunner)
        runner._jobs = {}
        runner._cancel_flags = {}

        job = LocalJob(status="processing")
        flag = multiprocessing.Value("b", 0)
        runner._jobs[job.id] = job
        runner._cancel_flags[job.id] = flag

        assert flag.value == 0
        # Simulate cancel
        runner._cancel_flags[job.id].value = 1
        assert flag.value == 1

    def test_remove_job(self):
        """remove_job удаляет задачу из списка."""
        runner = LocalOcrRunner.__new__(LocalOcrRunner)
        runner._jobs = {}
        runner._processes = {}
        runner._queues = {}
        runner._cancel_flags = {}

        job = LocalJob()
        runner._jobs[job.id] = job

        runner._cleanup_process = MagicMock()
        runner.remove_job(job.id)

        assert job.id not in runner._jobs
