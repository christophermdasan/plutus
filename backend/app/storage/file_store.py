"""Filing storage on the local filesystem.

Files are laid out per workspace so one user's uploads are physically
separated from another's, keeping the extension they arrived with:

    storage/filings/{user_id|guest}/{filing_id}.pdf
    storage/filings/{user_id|guest}/{filing_id}.htm

The extension is preserved rather than normalised because it is what tells
the parser and the viewer how to read the file back.

Deletion is deliberately absent from this class. Removing a filing is a soft
delete - the row is flagged and the PDF is kept - so that a mistaken delete
is always recoverable and any citation already shown to a user keeps
resolving.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import settings


class FileStore:
    def __init__(self, root: Path | None = None):
        self._root = root or settings.filings_dir

    @staticmethod
    def content_hash(data: bytes) -> str:
        """Stable digest, used to spot a re-upload of the same document."""
        return hashlib.sha256(data).hexdigest()

    def _workspace_dir(self, user_id: int | None) -> Path:
        return self._root / (str(user_id) if user_id is not None else "guest")

    def save(
        self, filing_id: str, user_id: int | None, data: bytes, suffix: str = ".pdf"
    ) -> Path:
        directory = self._workspace_dir(user_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{filing_id}{suffix.lower()}"
        path.write_bytes(data)
        return path

    def path_for(self, filing_id: str, user_id: int | None) -> Path | None:
        """The stored file, whatever extension it was saved under."""
        matches = sorted(self._workspace_dir(user_id).glob(f"{filing_id}.*"))
        return matches[0] if matches else None

    def exists(self, filing_id: str, user_id: int | None) -> bool:
        return self.path_for(filing_id, user_id) is not None
