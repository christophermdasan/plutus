from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.enums import FilingStatus

from tests.conftest import requires_postgres

pytestmark = requires_postgres


def _create(filings, filing_id="f1", user_id=None, name="apple-10k.pdf", digest="hash1"):
    return filings.create(
        filing_id=filing_id,
        original_name=name,
        user_id=user_id,
        stored_path=f"/storage/{filing_id}.pdf",
        size_bytes=1234,
        content_hash=digest,
    )


def test_a_new_filing_starts_queued(filings):
    filing = _create(filings)
    assert filing.status is FilingStatus.QUEUED
    assert filing.original_name == "apple-10k.pdf"
    assert not filing.is_archived and not filing.is_deleted


def test_status_updates_record_page_count_and_errors(filings):
    _create(filings)

    filings.update_status("f1", FilingStatus.READY, num_pages=42)
    assert filings.get("f1").num_pages == 42

    filings.update_status("f1", FilingStatus.FAILED, error="corrupt PDF")
    failed = filings.get("f1")
    assert failed.status is FilingStatus.FAILED
    assert failed.error == "corrupt PDF"
    # a later status change must not silently discard the page count
    assert failed.num_pages == 42


def test_listing_is_scoped_to_a_workspace(filings, users):
    alice = users.create("a@example.com", "Alice", "hash")
    _create(filings, "guest-filing", user_id=None)
    _create(filings, "alice-filing", user_id=alice.id, digest="hash2")

    guest_ids = {f.id for f in filings.list_for_workspace(None)}
    alice_ids = {f.id for f in filings.list_for_workspace(alice.id)}

    assert guest_ids == {"guest-filing"}
    assert alice_ids == {"alice-filing"}


def test_soft_delete_hides_the_filing_but_keeps_the_row_and_file(filings):
    _create(filings)
    filings.soft_delete("f1")

    assert filings.get("f1") is None
    assert filings.list_for_workspace(None) == []

    retained = filings.get("f1", include_deleted=True)
    assert retained is not None
    assert retained.is_deleted
    # the stored PDF path survives, so nothing on disk needs removing
    assert retained.stored_path == "/storage/f1.pdf"


def test_a_soft_deleted_filing_can_be_restored(filings):
    _create(filings)
    filings.soft_delete("f1")
    filings.restore("f1")

    assert filings.get("f1") is not None


def test_archiving_moves_a_filing_between_the_two_lists(filings):
    _create(filings)

    filings.set_archived("f1", True)
    assert filings.list_for_workspace(None) == []
    assert [f.id for f in filings.list_for_workspace(None, archived=True)] == ["f1"]

    filings.set_archived("f1", False)
    assert [f.id for f in filings.list_for_workspace(None)] == ["f1"]
    assert filings.list_for_workspace(None, archived=True) == []


def test_an_archived_filing_is_still_retrievable_by_id(filings):
    # archiving is about decluttering the list, not revoking access
    _create(filings)
    filings.set_archived("f1", True)
    assert filings.get("f1") is not None


def test_metadata_and_suggested_questions_round_trip(filings):
    _create(filings)
    filings.update_metadata(
        "f1",
        company_name="Meridian Robotics",
        filing_type="10-K",
        fiscal_period="FY2023",
        suggested_questions=["What was total revenue?", "Any impairment charges?"],
    )

    filing = filings.get("f1")
    assert filing.company_name == "Meridian Robotics"
    assert filing.display_title == "Meridian Robotics · 10-K · FY2023"
    assert len(filing.suggested_questions) == 2


def test_display_title_falls_back_to_the_filename_without_metadata(filings):
    assert _create(filings).display_title == "apple-10k.pdf"


def test_duplicate_uploads_are_detectable_by_content_hash(filings):
    _create(filings, "f1", digest="abc123")

    assert filings.find_by_hash("abc123", None).id == "f1"
    assert filings.find_by_hash("different", None) is None
    # the same file in another workspace is not a duplicate
    assert filings.find_by_hash("abc123", 999) is None


def test_renaming_changes_only_the_display_name(filings):
    _create(filings)
    filings.rename("f1", "Meridian FY2023")

    filing = filings.get("f1")
    assert filing.display_title == "Meridian FY2023"
    assert filing.original_name != "Meridian FY2023"


def test_a_rename_outranks_the_title_derived_from_the_document(filings):
    """The regression that made renaming look broken.

    display_title preferred the enricher's company/type/period label, and only
    fell back to the filename - so renaming anything the enricher had already
    identified changed a field nothing displayed.
    """
    _create(filings)
    filings.update_metadata("f1", company_name="MERIDIAN ROBOTICS, INC.",
                            filing_type="10-K", fiscal_period="FY2023")
    assert filings.get("f1").display_title == "MERIDIAN ROBOTICS, INC. · 10-K · FY2023"

    filings.rename("f1", "Meridian FY2023")
    assert filings.get("f1").display_title == "Meridian FY2023"


def test_concurrent_writes_do_not_raise_or_lose_rows(filings):
    def worker(i: int) -> None:
        fid = f"f{i}"
        _create(filings, fid, digest=f"hash{i}")
        filings.update_status(fid, FilingStatus.PARSING)
        filings.update_status(fid, FilingStatus.READY, num_pages=i + 1)

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(worker, range(40)))

    listed = filings.list_for_workspace(None)
    assert len(listed) == 40
    assert all(f.status is FilingStatus.READY for f in listed)


def test_interrupted_ingestion_is_reconciled_at_startup(filings):
    """A crash mid-ingest must not leave a filing looking busy forever.

    Nothing resumes a background ingest, so a row still in an active status
    when the process starts was interrupted by definition. Left alone it
    shows "Embedding" indefinitely - indistinguishable, to the reader, from
    work still in progress.
    """
    _create(filings, "stuck", digest="h1")
    _create(filings, "done", digest="h2")
    filings.update_status("stuck", FilingStatus.EMBEDDING)
    filings.update_status("done", FilingStatus.READY, num_pages=3)

    assert filings.fail_interrupted() == 1

    stuck = filings.get("stuck")
    assert stuck.status is FilingStatus.FAILED
    assert "again" in (stuck.error or "")
    # Finished work is untouched.
    assert filings.get("done").status is FilingStatus.READY


def test_reconciliation_is_a_no_op_when_nothing_was_interrupted(filings):
    _create(filings)
    filings.update_status("f1", FilingStatus.READY, num_pages=1)
    assert filings.fail_interrupted() == 0
