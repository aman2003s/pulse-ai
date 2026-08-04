import asyncio
from pathlib import Path
from typing import Dict, Any

from core.tools.registry import registry, Tool

# A real embedded text layer runs into the thousands of characters even for a
# short document; a handful of stray characters per page (e.g. page numbers a
# scanner already burned into the image) isn't a genuine text layer. Anything
# averaging below this per page is treated as image-only and OCR'd instead of
# returned as a near-empty "summary".
_MIN_CHARS_PER_PAGE = 20
_PDF_CONTENT_CAP = 6000
# OCR is a real per-page cost (render + recognize); a runaway safety cap for a
# very long scanned document, not a limit meant to bind on a normal one.
_MAX_OCR_PAGES = 15


def _extract_text_layer(path: str):
    """Real embedded text extraction — what NVDA/JAWS read directly from a
    PDF's own tagged/plain text layer, no OCR involved. pypdf (BSD-licensed,
    pure Python) is the permissively-licensed equivalent of that mechanism."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            pages_text.append("")
    return pages_text


async def _ocr_pages(path: str, max_pages: int):
    """Fallback for image-only/scanned PDFs with no real text layer. Renders
    each page and reads it with Windows' OWN native PDF renderer
    (Windows.Data.Pdf) and OCR engine (Windows.Media.Ocr) — the same offline,
    on-device mechanism Windows' own built-in tools (Snipping Tool's text
    actions, PowerToys Text Extractor, Narrator) use — instead of bundling a
    third-party OCR dependency."""
    from winsdk.windows.data.pdf import PdfDocument
    from winsdk.windows.storage import StorageFile
    from winsdk.windows.storage.streams import InMemoryRandomAccessStream
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return [], 0, ("No OCR language pack is installed for this Windows account — "
                        "add one under Settings > Time & Language > Language & region.")

    file = await StorageFile.get_file_from_path_async(path)
    pdf_doc = await PdfDocument.load_from_file_async(file)
    if pdf_doc.is_password_protected:
        return [], 0, "This PDF is password-protected — open it manually once to unlock it, then try again."

    real_page_count = pdf_doc.page_count
    page_count = min(real_page_count, max_pages)
    pages_text = []
    for i in range(page_count):
        page = pdf_doc.get_page(i)
        stream = InMemoryRandomAccessStream()
        await page.render_to_stream_async(stream)
        page.close()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        pages_text.append(result.text or "")
    return pages_text, real_page_count, None


class ReadPdfTool(Tool):
    name: str = "read_pdf"
    description: str = (
        "Reads a PDF file's content as text, for summarizing/answering questions about it. "
        "Extracts the real embedded text layer first (fast, exact); if the PDF has no text "
        "layer at all (a scanned/image-only document), falls back to Windows' own built-in "
        "OCR to read it. Pass the absolute file path (use search_file first if you only have a name)."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the PDF file"}
        },
        "required": ["path"]
    }
    output_schema: Dict[str, Any] = {"type": "object"}
    permission_level: str = "safe"
    # OCR-per-page is the slow path; text-layer extraction is normally well under a second.
    timeout_s: float = 45.0

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = (params.get("path") or "").strip()
        if not path:
            return {"error": "No PDF path given."}
        p = Path(path)
        if not p.exists():
            return {"error": f"No file found at {path}. Try search_file first if you only have a name."}
        if p.suffix.lower() != ".pdf":
            return {"error": f"{path} isn't a .pdf file."}

        try:
            pages_text = _extract_text_layer(str(p))
        except Exception as e:
            return {"error": f"Couldn't open this PDF: {e}"}

        total_chars = sum(len(t.strip()) for t in pages_text)
        if pages_text and total_chars >= _MIN_CHARS_PER_PAGE * len(pages_text):
            full_text = "\n\n".join(t for t in pages_text if t.strip())
            truncated = len(full_text) > _PDF_CONTENT_CAP
            return {
                "success": True,
                "pages_total": len(pages_text),
                "pages_read": len(pages_text),
                "method": "text_layer",
                "text": full_text[:_PDF_CONTENT_CAP],
                "truncated": truncated
            }

        try:
            ocr_pages, real_page_count, err = asyncio.run(_ocr_pages(str(p), _MAX_OCR_PAGES))
        except Exception as e:
            return {"error": f"Couldn't OCR this PDF: {e}"}
        if err:
            return {"error": err}
        if not ocr_pages or not any(t.strip() for t in ocr_pages):
            return {"error": "This looks like an image-only PDF and Windows' OCR couldn't find any readable text on it."}

        full_text = "\n\n".join(t for t in ocr_pages if t.strip())
        pages_capped = real_page_count > len(ocr_pages)
        result = {
            "success": True,
            "pages_total": real_page_count,
            "pages_read": len(ocr_pages),
            "method": "ocr",
            "text": full_text[:_PDF_CONTENT_CAP],
            "truncated": len(full_text) > _PDF_CONTENT_CAP or pages_capped
        }
        if pages_capped:
            result["note"] = f"Only OCR'd the first {len(ocr_pages)} of {real_page_count} pages."
        return result


registry.register(ReadPdfTool())
