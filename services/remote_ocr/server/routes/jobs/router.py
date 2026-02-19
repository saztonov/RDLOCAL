"""Router для задач OCR"""
from fastapi import APIRouter

from services.remote_ocr.server.routes.jobs.create_handler import create_job_handler
from services.remote_ocr.server.routes.jobs.delete_handler import delete_job_handler
from services.remote_ocr.server.routes.jobs.reorder_handler import reorder_job_handler
from services.remote_ocr.server.routes.jobs.read_handlers import (
    download_result_handler,
    get_job_details_handler,
    get_job_handler,
    get_jobs_changes_handler,
    list_jobs_handler,
)
from services.remote_ocr.server.routes.jobs.update_handlers import (
    cancel_job_handler,
    pause_job_handler,
    restart_job_handler,
    resume_job_handler,
    start_job_handler,
    update_job_handler,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# POST endpoints
router.post("")(create_job_handler)
router.post("/{job_id}/restart")(restart_job_handler)
router.post("/{job_id}/start")(start_job_handler)
router.post("/{job_id}/pause")(pause_job_handler)
router.post("/{job_id}/resume")(resume_job_handler)
router.post("/{job_id}/cancel")(cancel_job_handler)
router.post("/{job_id}/reorder")(reorder_job_handler)

# GET endpoints
router.get("")(list_jobs_handler)
router.get("/changes")(get_jobs_changes_handler)
router.get("/{job_id}")(get_job_handler)
router.get("/{job_id}/details")(get_job_details_handler)
router.get("/{job_id}/result")(download_result_handler)

# PATCH endpoints
router.patch("/{job_id}")(update_job_handler)

# DELETE endpoints
router.delete("/{job_id}")(delete_job_handler)
