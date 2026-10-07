# backend/tests/test_upload_storage.py
# Task 2.7.a [SECURITY]: tests for app/ingest/upload_storage.py. Offline
# only -- real filesystem I/O against a pytest tmp_path, no services
# needed.
#
# The hostile cases below exist to prove the module's own central claim
# (its own header comment): the on-disk path is built from a server-
# generated uuid.UUID ALONE, never from anything tenant-suppliable --
# not because a crafted string happens to get rejected by a filter, but
# because the functions that build a path never accept a plain string
# for that purpose at all.
import uuid

import pytest

from app.ingest.upload_storage import UploadTooLargeError, delete, read, save


def test_save_then_read_is_byte_identical(tmp_path):
    data = b"\x00some real bytes\xff\x01" * 100
    storage_id = save(data, storage_dir=tmp_path, max_size_bytes=10_000)
    assert isinstance(storage_id, uuid.UUID)
    assert read(storage_id, storage_dir=tmp_path) == data


def test_delete_then_read_fails_cleanly(tmp_path):
    storage_id = save(b"content", storage_dir=tmp_path, max_size_bytes=10_000)
    delete(storage_id, storage_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        read(storage_id, storage_dir=tmp_path)


def test_double_delete_is_a_no_op_not_an_error(tmp_path):
    storage_id = save(b"content", storage_dir=tmp_path, max_size_bytes=10_000)
    delete(storage_id, storage_dir=tmp_path)
    delete(storage_id, storage_dir=tmp_path)  # must not raise


def test_delete_of_an_id_that_was_never_saved_is_also_a_no_op(tmp_path):
    delete(uuid.uuid4(), storage_dir=tmp_path)  # must not raise


def test_each_save_gets_its_own_distinct_id(tmp_path):
    first = save(b"one", storage_dir=tmp_path, max_size_bytes=10_000)
    second = save(b"two", storage_dir=tmp_path, max_size_bytes=10_000)
    assert first != second
    assert read(first, storage_dir=tmp_path) == b"one"
    assert read(second, storage_dir=tmp_path) == b"two"


def test_storage_directory_is_created_if_missing(tmp_path):
    storage_dir = tmp_path / "does" / "not" / "exist" / "yet"
    assert not storage_dir.exists()
    storage_id = save(b"content", storage_dir=storage_dir, max_size_bytes=10_000)
    assert read(storage_id, storage_dir=storage_dir) == b"content"


# --- Hostile: path traversal -------------------------------------------


def test_save_accepts_no_filename_or_path_argument_at_all(tmp_path):
    # save()'s own signature has no parameter a caller could even attempt
    # to put a tenant-supplied path-traversal string into -- confirmed
    # structurally, not just by convention, via inspect.signature.
    import inspect

    params = set(inspect.signature(save).parameters)
    assert params == {"data", "storage_dir", "max_size_bytes"}


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "../../../../etc/passwd",
        "/etc/passwd",
        "..",
        str(uuid.uuid4()),  # even a well-formed UUID STRING must be rejected -- not a UUID object
    ],
)
def test_read_and_delete_reject_a_string_id_even_one_shaped_like_a_path_traversal(
    tmp_path, hostile
):
    # The real, legitimate id a caller would have is always a uuid.UUID
    # object returned by save() -- never a string of any shape. This
    # proves read()/delete() refuse to even try building a path from a
    # string, so a hostile "filename" smuggled in by a confused or
    # compromised caller has zero effect on where anything is read from
    # or deleted, because it can never reach _path_for() as a usable
    # argument in the first place.
    with pytest.raises(TypeError):
        read(hostile, storage_dir=tmp_path)
    with pytest.raises(TypeError):
        delete(hostile, storage_dir=tmp_path)


def test_a_crafted_tenant_supplied_filename_has_zero_effect_on_where_bytes_land(tmp_path):
    # Simulates the real shape of the risk: a tenant supplies a hostile
    # "filename" purely as metadata alongside real file bytes. That string
    # is never passed to save() at all (it has no parameter for it -- see
    # the signature test above), so it is structurally impossible for it
    # to influence the on-disk path. This test proves the OUTCOME: the
    # saved file ends up exactly at storage_dir/<the real returned id>,
    # nowhere else, regardless of what the tenant claimed the filename was.
    tenant_supplied_filename = "../../../../etc/passwd"  # never used below, by design
    data = b"tenant file content"

    storage_id = save(data, storage_dir=tmp_path, max_size_bytes=10_000)

    on_disk = list(tmp_path.iterdir())
    assert len(on_disk) == 1
    assert on_disk[0].name == str(storage_id)
    assert tenant_supplied_filename not in on_disk[0].name
    assert on_disk[0].read_bytes() == data


# --- Size enforcement ---------------------------------------------------


def test_oversized_upload_is_rejected_before_any_disk_write(tmp_path):
    data = b"x" * 1000
    with pytest.raises(UploadTooLargeError):
        save(data, storage_dir=tmp_path, max_size_bytes=999)
    # Prove it, don't just assert the exception: zero files on disk
    # (pytest's own tmp_path always pre-exists, so the real proof is that
    # it stays EMPTY, not that it's absent).
    assert list(tmp_path.iterdir()) == []


def test_upload_exactly_at_the_limit_is_accepted(tmp_path):
    data = b"x" * 1000
    storage_id = save(data, storage_dir=tmp_path, max_size_bytes=1000)
    assert read(storage_id, storage_dir=tmp_path) == data
