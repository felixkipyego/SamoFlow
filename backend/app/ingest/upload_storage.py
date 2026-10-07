# backend/app/ingest/upload_storage.py
# Task 2.7.a [SECURITY]: raw-bytes storage primitive for the upload
# adapter (docs/SPEC.md §5.6: "originals stored privately so re-embedding
# needs no re-upload"). Local disk under a Settings-configurable
# directory, shared between the `api` and `worker` containers via a new
# Docker volume (deploy/docker-compose.yml) -- see the Step 2.7 breakdown
# decision (b) for why disk, not a Postgres column or a new object-
# storage service.
#
# Path-traversal resistance BY CONSTRUCTION, not by validation -- matching
# this project's own established design philosophy (ip_safety.py's own
# allow-list-not-deny-list precedent): save() takes no filename or path
# argument at all, from a tenant or otherwise. It generates its own
# identifier (uuid.uuid4()) and that is the ONLY input to the on-disk
# path. read()/delete() require an actual uuid.UUID instance (enforced at
# runtime, not just type-hinted -- a type hint alone is not checked at
# runtime) -- a string, even one that merely LOOKS like a UUID, is
# rejected outright. This closes the entire path-traversal class
# structurally: str(uuid.uuid4()) can never contain "/", "..", or any
# other path-meaningful character, so there is no string to sanitize and
# no denylist to maintain. A secondary "is the resolved path still inside
# storage_dir" containment check was considered and declined -- it would
# be unreachable dead code given the above, not real defense in depth
# (rule 11: no checks the task does not need).
#
# The real, original filename (needed later for `documents.file_name`) is
# NEVER tracked here -- this module only ever sees and returns a
# server-generated uuid.UUID. The caller (2.7.c's orchestration primitive)
# is responsible for keeping that mapping, e.g. by writing the original
# filename into the `documents` row keyed by the SAME uuid it stores as
# this module's own storage_id. The two are deliberately independent: this
# module cannot leak a tenant-supplied string into a filesystem path
# because it is never given one.
#
# Size enforcement happens INSIDE save(), before the write, not as a
# separate pre-check a caller could forget to call -- docs/SPEC.md §5.6's
# own "size... limits from the plan" line, applied here as a single
# required `max_size_bytes` keyword argument (Step 2.7 breakdown decision
# (d): a global Settings default for now, not yet per-plan -- see the
# matching Open marker). Rejected input is never written to disk at all.
import uuid
from pathlib import Path


class UploadTooLargeError(Exception):
    """Raised by save() when `data` exceeds `max_size_bytes`. Matches this
    project's own plain-Exception-subclass precedent (e.g. safe_fetch.py's
    UnsafeFetchError) -- not a ValueError, since this is a policy/limit
    rejection, not a malformed-input error."""


def _path_for(storage_dir: Path, storage_id: uuid.UUID) -> Path:
    # The ONLY place an on-disk path is built. `storage_id` must already
    # be a real uuid.UUID instance -- enforced here at runtime (not merely
    # hinted) so no caller, however it got hold of a string, can smuggle a
    # path-traversal sequence through a parameter that merely LOOKS like
    # the right type.
    if not isinstance(storage_id, uuid.UUID):
        raise TypeError(f"storage_id must be a uuid.UUID, got {type(storage_id).__name__}")
    return storage_dir / str(storage_id)


def save(data: bytes, *, storage_dir: Path, max_size_bytes: int) -> uuid.UUID:
    """Writes `data` to a new, server-identified file under `storage_dir`,
    returning the generated id. Takes no filename/path input of any kind
    -- see this module's own header comment for why. Rejects oversized
    input BEFORE writing anything (`UploadTooLargeError`), never write-
    then-check."""
    if len(data) > max_size_bytes:
        raise UploadTooLargeError(
            f"upload is {len(data)} bytes, exceeding the {max_size_bytes}-byte limit"
        )
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_id = uuid.uuid4()
    _path_for(storage_dir, storage_id).write_bytes(data)
    return storage_id


def read(storage_id: uuid.UUID, *, storage_dir: Path) -> bytes:
    """Reads back the bytes saved under `storage_id`. Raises
    FileNotFoundError (the standard, well-known exception for this --
    rule 11) if no such file exists, including after delete()."""
    return _path_for(storage_dir, storage_id).read_bytes()


def delete(storage_id: uuid.UUID, *, storage_dir: Path) -> None:
    """Deletes the file saved under `storage_id`. Idempotent: deleting an
    already-deleted (or never-existing) id is a no-op, not an error --
    `Path.unlink(missing_ok=True)` is the standard stdlib mechanism for
    this (rule 11), not a hand-rolled existence check first."""
    _path_for(storage_dir, storage_id).unlink(missing_ok=True)
