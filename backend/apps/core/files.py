"""
Getting a filesystem path out of a stored file, wherever it is stored.

Django's ``FieldFile.path`` exists only on ``FileSystemStorage``. On any remote
backend — Supabase Storage in production, see ``config/settings/prod.py`` — it
raises ``NotImplementedError``, and every caller that reached for ``.path``
breaks the moment the deployment stops writing to local disk.

Most of the codebase does not care: it reads bytes. The exception is the
computer-vision stack. DeepFace and OpenCV's SFace both take *filenames*, not
buffers, and neither accepts a file object, so "just read the bytes" is not
available there. :func:`local_path` closes that gap: it hands back a real path
either way, copying to a temporary file only when the storage cannot supply one.

    with local_path(worker.photo) as reference:
        result = verify_face(live, reference)

The temporary file is removed on exit; a file that was already on disk is
yielded untouched and never deleted.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def local_path(field_file, *, suffix: str | None = None):
    """Yield a filesystem path for ``field_file``, copying it down if needed.

    ``field_file`` is a Django ``FieldFile`` (or anything with ``name`` and
    ``open``). ``suffix`` overrides the extension of the temporary copy; by
    default the stored name's own extension is kept, because the CV libraries
    decide how to decode a file from it.

    Raises ``ValueError`` for an empty field rather than yielding a path that
    does not exist — a caller asking for the path of a file nobody uploaded has
    a bug, and failing here says so plainly.
    """
    if not field_file:
        raise ValueError("No file is attached to this field.")

    # The fast path: local storage already has exactly what the caller wants,
    # and copying it would be pure waste on every gate scan. Resolved before the
    # yield, so that a NotImplementedError raised by the *caller's* body is not
    # mistaken for "this storage has no paths".
    try:
        existing = field_file.path
    except NotImplementedError:
        existing = None

    if existing is not None:
        yield existing
        return

    extension = suffix if suffix is not None else Path(field_file.name or "").suffix
    handle = tempfile.NamedTemporaryFile(suffix=extension, delete=False)
    temp_name = handle.name
    try:
        with field_file.open("rb") as source:
            # Chunked rather than read(): a remote read of a full-resolution
            # phone photo should not be held in memory twice on a 512 MB box.
            for chunk in iter(lambda: source.read(256 * 1024), b""):
                handle.write(chunk)
        handle.close()

        yield temp_name
    finally:
        if not handle.closed:
            handle.close()
        try:
            os.unlink(temp_name)
        except OSError:  # pragma: no cover — the file is ours and just closed
            logger.warning("Could not remove temporary file %s", temp_name)


__all__ = ["local_path"]
