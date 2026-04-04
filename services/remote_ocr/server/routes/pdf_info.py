"""PDF Info API — метаданные и рендеринг страниц."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..logging_config import get_logger
from ..storage import get_node_pdf_r2_key

logger = get_logger(__name__)

router = APIRouter(prefix="/api/pdf", tags=["pdf"])


def _download_pdf_to_temp(node_id: str) -> Path:
    """Скачать PDF из R2 во временный файл."""
    pdf_r2_key = get_node_pdf_r2_key(node_id)
    if not pdf_r2_key:
        raise HTTPException(status_code=404, detail=f"No PDF for node {node_id}")

    from ..routes.common import get_r2_sync_client

    s3_client, bucket_name = get_r2_sync_client()

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        s3_client.download_fileobj(bucket_name, pdf_r2_key, tmp)
        tmp.close()
        return Path(tmp.name)
    except Exception as e:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=f"PDF download failed: {e}")


@router.get("/{node_id}/info")
def get_pdf_info(node_id: str) -> dict:
    """Получить информацию о PDF: количество страниц и размеры."""
    pdf_path = _download_pdf_to_temp(node_id)
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        pages = []
        for i in range(len(doc)):
            page = doc[i]
            rect = page.rect
            pages.append({
                "page_index": i,
                "width": rect.width,
                "height": rect.height,
            })
        doc.close()
        return {
            "node_id": node_id,
            "page_count": len(pages),
            "pages": pages,
        }
    finally:
        pdf_path.unlink(missing_ok=True)


@router.get("/{node_id}/page/{page_num}")
def render_page(node_id: str, page_num: int, dpi: int = 150) -> Response:
    """Рендер страницы PDF как PNG (fallback для случаев когда pdf.js не подходит)."""
    if dpi > 300:
        dpi = 300

    pdf_path = _download_pdf_to_temp(node_id)
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        if page_num < 0 or page_num >= len(doc):
            doc.close()
            raise HTTPException(status_code=404, detail=f"Page {page_num} not found")

        page = doc[page_num]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()

        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
            },
        )
    finally:
        pdf_path.unlink(missing_ok=True)
