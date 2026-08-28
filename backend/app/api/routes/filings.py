from __future__ import annotations

import uuid

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.api.deps import Files, Filings, Pipeline, WorkspaceId
from app.api.schemas import FilingOut, RenameFilingRequest
from app.exceptions import NotFoundError, ValidationError
from app.ingestion.html_parser import _decode, sanitize_page_html, split_html_pages
from app.storage import FileStore

router = APIRouter(prefix="/filings", tags=["filings"])

MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# EDGAR serves filings as HTML and most people archive them as PDF, so both
# are accepted as they are. Neither is converted into the other: re-rendering
# would re-paginate the document and move every citation off the page the
# source actually used.
ACCEPTED_SUFFIXES = (".pdf", ".htm", ".html", ".xhtml")

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".htm": "text/html",
    ".html": "text/html",
    ".xhtml": "text/html",
}
_HTML_SUFFIXES = {".htm", ".html", ".xhtml"}

# A page fragment is not a document, and rendered bare it inherits nothing:
# no margins, no page colour, no sane default font. Wrapping it keeps the
# viewer looking like the paper the filing was set on, matching how the same
# drawer renders a PDF. Deliberately light - the filing's own styles decide
# how the filing looks.
_PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html {{ background: #f1f1f1; }}
  body {{
    margin: 0 auto; padding: 28px 32px; max-width: 60rem; background: #fff;
    color: #111; font-family: Georgia, "Times New Roman", serif; font-size: 13px;
    line-height: 1.5; overflow-wrap: break-word;
  }}
  table {{ border-collapse: collapse; max-width: 100%; }}
  td, th {{ padding: 2px 6px; vertical-align: top; }}
  img {{ max-width: 100%; height: auto; }}
</style></head><body>{body}</body></html>"""


def _run_ingest(pipeline: Pipeline, filing_id: str, path) -> None:
    try:
        pipeline.ingest(filing_id, path)
    except Exception:
        # Status is already recorded as failed by the pipeline; swallowing
        # here just stops an unhandled exception in a background task.
        pass


@router.post("", response_model=FilingOut, status_code=201)
async def add_filing(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    filings: Filings,
    pipeline: Pipeline,
    store: Files,
    workspace: WorkspaceId,
):
    if not file.filename or not file.filename.lower().endswith(ACCEPTED_SUFFIXES):
        raise ValidationError("Upload a PDF or HTML filing (.pdf, .htm, .html).")

    data = await file.read()
    if not data:
        raise ValidationError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError("That file is larger than the 100MB limit.")

    digest = FileStore.content_hash(data)
    if existing := filings.find_by_hash(digest, workspace):
        # Re-uploading the same document returns what's already indexed
        # rather than duplicating the work and the sidebar entry.
        return FilingOut.of(existing)

    filing_id = uuid.uuid4().hex[:12]
    path = store.save(filing_id, workspace, data, Path(file.filename).suffix)
    filing = filings.create(
        filing_id=filing_id,
        original_name=file.filename,
        user_id=workspace,
        stored_path=str(path),
        size_bytes=len(data),
        content_hash=digest,
    )

    # Ingestion runs in the background so the upload returns immediately and
    # the UI can show live progress.
    background_tasks.add_task(_run_ingest, pipeline, filing_id, path)
    return FilingOut.of(filing)


def _owned(filings: Filings, filing_id: str, workspace: int | None, *, include_deleted: bool = False):
    """Fetch a filing, but only within the caller's own workspace.

    Every route below addresses a filing by id, and an id alone is not
    authorisation: without this check any visitor could read, rename or delete
    another account's filings - and download the documents themselves - simply
    by naming their id. Storage is already partitioned per workspace on disk;
    this makes the API agree with it.

    A filing in someone else's workspace is reported as missing rather than
    forbidden, so the API does not confirm that a given id exists.
    """
    filing = filings.get(filing_id, include_deleted=include_deleted)
    if filing is None or filing.user_id != workspace:
        raise NotFoundError("Filing not found.")
    return filing


@router.get("", response_model=list[FilingOut])
def list_filings(filings: Filings, workspace: WorkspaceId, archived: bool = False):
    return [FilingOut.of(f) for f in filings.list_for_workspace(workspace, archived=archived)]


@router.get("/{filing_id}", response_model=FilingOut)
def get_filing(filing_id: str, filings: Filings, workspace: WorkspaceId):
    return FilingOut.of(_owned(filings, filing_id, workspace))


def _stored_file(filing) -> Path:
    """The file behind an already-authorised filing.

    `stored_path` is recorded absolute, which makes it a hostage to where the
    checkout happens to live: rename or move the directory and every filing in
    the database points at somewhere that no longer exists. The layout is
    deterministic - storage/filings/{workspace}/{id}{ext} - so the recorded
    path is treated as a hint and the location is re-derived when it does not
    resolve.
    """
    if filing.stored_path:
        path = Path(filing.stored_path)
        if path.exists():
            return path

    relocated = FileStore().path_for(filing.id, filing.user_id)
    if relocated is not None and relocated.exists():
        return relocated

    raise NotFoundError("The stored file for this filing is missing.")


@router.get("/{filing_id}/pdf")
def get_filing_file(filing_id: str, filings: Filings, workspace: WorkspaceId):
    """The original document. Path kept as /pdf so existing links resolve."""
    filing = _owned(filings, filing_id, workspace)
    path = _stored_file(filing)
    # inline so the browser's viewer renders it rather than downloading
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Content-Disposition": f'inline; filename="{filing.original_name}"'},
    )


@router.get("/{filing_id}/page/{page}", response_class=HTMLResponse)
def get_filing_page_html(
    filing_id: str, page: int, filings: Filings, workspace: WorkspaceId
):
    """One page of an HTML filing, sanitised, for the source panel.

    Served a page at a time rather than whole: filings run to several
    megabytes, and a citation names one page, so this is both what the reader
    asked for and the cheapest thing to send.
    """
    path = _stored_file(_owned(filings, filing_id, workspace))
    if path.suffix.lower() not in _HTML_SUFFIXES:
        raise ValidationError("This filing is not an HTML document.")

    pages = split_html_pages(_decode(path))
    if not 1 <= page <= len(pages):
        raise NotFoundError(f"This filing has {len(pages)} pages.")

    return HTMLResponse(
        _PAGE_TEMPLATE.format(body=sanitize_page_html(pages[page - 1])),
        headers={
            # Belt and braces with the viewer's sandboxed iframe: even if
            # something executable survived sanitising, it can neither run
            # nor call out.
            "Content-Security-Policy": (
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
            )
        },
    )


@router.patch("/{filing_id}", response_model=FilingOut)
def rename_filing(
    filing_id: str, payload: RenameFilingRequest, filings: Filings, workspace: WorkspaceId
):
    _owned(filings, filing_id, workspace)
    filings.rename(filing_id, payload.name)
    return FilingOut.of(filings.get(filing_id))


@router.post("/{filing_id}/archive", response_model=FilingOut)
def archive_filing(filing_id: str, filings: Filings, workspace: WorkspaceId):
    _owned(filings, filing_id, workspace)
    filings.set_archived(filing_id, True)
    return FilingOut.of(filings.get(filing_id))


@router.post("/{filing_id}/unarchive", response_model=FilingOut)
def unarchive_filing(filing_id: str, filings: Filings, workspace: WorkspaceId):
    _owned(filings, filing_id, workspace)
    filings.set_archived(filing_id, False)
    return FilingOut.of(filings.get(filing_id))


@router.delete("/{filing_id}", status_code=204)
def delete_filing(filing_id: str, filings: Filings, workspace: WorkspaceId):
    """Soft delete: the row is flagged and the PDF is kept on disk.

    Nothing is destroyed, so a mistaken delete stays recoverable and any
    citation already shown to a user still resolves.
    """
    _owned(filings, filing_id, workspace)
    filings.soft_delete(filing_id)


@router.post("/{filing_id}/restore", response_model=FilingOut)
def restore_filing(filing_id: str, filings: Filings, workspace: WorkspaceId):
    _owned(filings, filing_id, workspace, include_deleted=True)
    filings.restore(filing_id)
    return FilingOut.of(filings.get(filing_id))
