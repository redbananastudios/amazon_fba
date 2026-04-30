"""Tests for fba_engine.steps.push_to_gsheets.

The googleapis client is mocked in every test — these tests verify the
strategy-chain control flow and parameter shape, not the actual network
calls. End-to-end verification requires a real service-account key + folder
permissions and is deferred to manual smoke testing.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

from fba_engine.steps.push_to_gsheets import (
    DEFAULT_FOLDER_ID,
    DEFAULT_SCOPES,
    LEGEND_DATA,
    SHEETS_MIMETYPE,
    XLSX_MIMETYPE,
    PushFailedError,
    _create_via_sheets_api,
    _delete_previous_sheet,
    _is_quota_error,
    _upload_raw_xlsx,
    _upload_with_conversion,
    csv_rows_for_upload,
    push_to_gsheets,
    run_step,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_folder_id_set(self):
        assert DEFAULT_FOLDER_ID  # non-empty
        # Pin the legacy folder ID so a typo'd config doesn't silently send to
        # a different folder.
        assert DEFAULT_FOLDER_ID == "1uFYiz7rYFm5ZJHgkXJh86jKaigK9H6yd"

    def test_scopes_include_drive_and_sheets(self):
        assert any("drive" in s for s in DEFAULT_SCOPES)
        assert any("spreadsheets" in s for s in DEFAULT_SCOPES)

    def test_mime_types(self):
        assert XLSX_MIMETYPE.endswith("spreadsheetml.sheet")
        assert SHEETS_MIMETYPE == "application/vnd.google-apps.spreadsheet"

    def test_legend_data_has_known_verdicts(self):
        col_a = [row[0] for row in LEGEND_DATA]
        for v in ["YES", "MAYBE", "BRAND APPROACH", "BUY THE DIP", "GATED", "NO"]:
            assert v in col_a


# ---------------------------------------------------------------------------
# csv_rows_for_upload — converts a DataFrame to row-of-rows for sheets upload.
# ---------------------------------------------------------------------------


class TestCsvRowsForUpload:
    def test_first_row_is_headers(self):
        df = pd.DataFrame([{"ASIN": "B001", "Brand": "Acme"}])
        rows = csv_rows_for_upload(df)
        assert rows[0] == ["ASIN", "Brand"]

    def test_subsequent_rows_are_data_as_strings(self):
        df = pd.DataFrame([{"ASIN": "B001", "Price": 9.99}])
        rows = csv_rows_for_upload(df)
        assert rows[1] == ["B001", "9.99"]

    def test_nan_cells_become_empty_string(self):
        df = pd.DataFrame([{"ASIN": "B001", "Brand": float("nan")}])
        rows = csv_rows_for_upload(df)
        assert rows[1][1] == ""

    def test_empty_df_returns_only_header(self):
        df = pd.DataFrame(columns=["ASIN"])
        rows = csv_rows_for_upload(df)
        assert rows == [["ASIN"]]


# ---------------------------------------------------------------------------
# _is_quota_error — detects Drive storage-quota failures.
# ---------------------------------------------------------------------------


class TestIsQuotaError:
    """The matcher is intentionally tight: `_is_quota_error` must match
    only canonical Google quota phrases (storageQuotaExceeded, quotaExceeded)
    OR HTTP 403 with a 'storage' hint. Bare 'quota' substrings, rate-limit
    errors, and 403-without-storage are NOT storage-quota — they should
    fall through to other handlers (retry / re-raise)."""

    def test_storage_quota_phrase_matches(self):
        err = Exception("storageQuotaExceeded for service account")
        assert _is_quota_error(err) is True

    def test_quota_exceeded_phrase_matches(self):
        err = Exception("quotaExceeded: limit reached")
        assert _is_quota_error(err) is True

    def test_403_with_storage_hint_matches(self):
        err = Exception("403 Forbidden — storage limit reached")
        err.resp = SimpleNamespace(status=403)
        assert _is_quota_error(err) is True

    def test_403_status_as_string_with_storage_hint_matches(self):
        # httplib2 sometimes surfaces status as string — int-coerce.
        err = Exception("storage limit")
        err.resp = SimpleNamespace(status="403")
        assert _is_quota_error(err) is True

    def test_bare_403_without_storage_hint_does_not_match(self):
        # Permission denied is 403 but is NOT storage-quota; should
        # propagate, not fall through silently.
        err = Exception("Forbidden: caller lacks permission")
        err.resp = SimpleNamespace(status=403)
        assert _is_quota_error(err) is False

    def test_rate_limit_phrase_does_not_match(self):
        # Rate limits are transient — handled by retry, NOT by strategy
        # fallthrough.
        err = Exception("userRateLimitExceeded")
        assert _is_quota_error(err) is False

    def test_unrelated_substring_quota_does_not_match(self):
        # "API quota check timed out" used to false-positive on the bare
        # 'quota' substring matcher.
        err = Exception("API quota check timed out")
        assert _is_quota_error(err) is False

    def test_unrelated_error_returns_false(self):
        err = Exception("network timeout")
        assert _is_quota_error(err) is False


class TestIsTransient:
    """Regression: transient errors must trigger the retry helper."""

    def test_429_is_transient(self):
        from fba_engine.steps.push_to_gsheets import _is_transient
        err = Exception("Too Many Requests")
        err.resp = SimpleNamespace(status=429)
        assert _is_transient(err) is True

    def test_503_is_transient(self):
        from fba_engine.steps.push_to_gsheets import _is_transient
        err = Exception("Service Unavailable")
        err.resp = SimpleNamespace(status=503)
        assert _is_transient(err) is True

    def test_rate_limit_phrase_is_transient(self):
        from fba_engine.steps.push_to_gsheets import _is_transient
        err = Exception("userRateLimitExceeded")
        assert _is_transient(err) is True

    def test_404_is_not_transient(self):
        from fba_engine.steps.push_to_gsheets import _is_transient
        err = Exception("not found")
        err.resp = SimpleNamespace(status=404)
        assert _is_transient(err) is False

    def test_storage_quota_is_not_transient(self):
        from fba_engine.steps.push_to_gsheets import _is_transient
        err = Exception("storageQuotaExceeded")
        assert _is_transient(err) is False


class TestRetryWithBackoff:
    """Regression: transient errors should be retried with exponential
    backoff before giving up; non-transient errors must not be retried."""

    def test_succeeds_first_try(self):
        from fba_engine.steps.push_to_gsheets import _retry_with_backoff
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            return "ok"
        sleeps = []
        assert _retry_with_backoff(fn, sleep=sleeps.append) == "ok"
        assert calls["n"] == 1
        assert sleeps == []

    def test_retries_on_transient_then_succeeds(self):
        from fba_engine.steps.push_to_gsheets import _retry_with_backoff
        err = Exception("Service Unavailable")
        err.resp = SimpleNamespace(status=503)
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise err
            return "ok"
        sleeps = []
        assert _retry_with_backoff(
            fn, base_delay=1.0, sleep=sleeps.append
        ) == "ok"
        assert calls["n"] == 3
        assert sleeps == [1.0, 2.0]  # backoff 2^0, 2^1

    def test_does_not_retry_non_transient(self):
        from fba_engine.steps.push_to_gsheets import _retry_with_backoff
        err = Exception("Forbidden")
        err.resp = SimpleNamespace(status=403)
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            raise err
        sleeps = []
        with pytest.raises(Exception, match="Forbidden"):
            _retry_with_backoff(fn, sleep=sleeps.append)
        assert calls["n"] == 1
        assert sleeps == []

    def test_gives_up_after_max_attempts(self):
        from fba_engine.steps.push_to_gsheets import _retry_with_backoff
        err = Exception("Service Unavailable")
        err.resp = SimpleNamespace(status=503)
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            raise err
        sleeps = []
        with pytest.raises(Exception, match="Service Unavailable"):
            _retry_with_backoff(
                fn, max_attempts=3, base_delay=1.0, sleep=sleeps.append
            )
        assert calls["n"] == 3
        assert sleeps == [1.0, 2.0]  # final attempt raises, no sleep after


# ---------------------------------------------------------------------------
# _delete_previous_sheet — removes the prior sheet if id-file exists.
# ---------------------------------------------------------------------------


class TestDeletePreviousSheet:
    def test_no_previous_id_is_noop(self):
        drive = MagicMock()
        result = _delete_previous_sheet(drive, previous_id=None)
        drive.files.assert_not_called()
        assert result is False

    def test_previous_id_calls_drive_delete(self):
        drive = MagicMock()
        delete_op = drive.files.return_value.delete
        result = _delete_previous_sheet(drive, previous_id="abc123")
        delete_op.assert_called_once()
        kwargs = delete_op.call_args.kwargs
        assert kwargs["fileId"] == "abc123"
        assert kwargs.get("supportsAllDrives") is True
        assert result is True

    def test_delete_failure_is_logged_not_raised(self):
        drive = MagicMock()
        drive.files.return_value.delete.return_value.execute.side_effect = (
            Exception("not found")
        )
        # Must not raise.
        result = _delete_previous_sheet(drive, previous_id="bad_id")
        assert result is False  # delete attempted but failed.


# ---------------------------------------------------------------------------
# _upload_with_conversion — Strategy 1.
# ---------------------------------------------------------------------------


class TestUploadWithConversion:
    def test_returns_sheet_id_on_success(self, tmp_path: Path):
        drive = MagicMock()
        # Resumable uploads pump via next_chunk() -> (status, response).
        # response is None for in-progress chunks, dict on completion.
        drive.files.return_value.create.return_value.next_chunk.return_value = (
            None,
            {
                "id": "SHEET123",
                "webViewLink": "https://docs.google.com/spreadsheets/d/SHEET123/edit",
            },
        )
        xlsx = tmp_path / "test.xlsx"
        xlsx.write_bytes(b"PKfake-xlsx")  # minimal placeholder
        sheet_id = _upload_with_conversion(
            drive, xlsx, name="Test", folder_id="FOLDER1"
        )
        assert sheet_id == "SHEET123"

    def test_uses_sheets_mimetype_for_file_metadata(self, tmp_path: Path):
        drive = MagicMock()
        drive.files.return_value.create.return_value.next_chunk.return_value = (
            None, {"id": "S1", "webViewLink": "url"}
        )
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        _upload_with_conversion(drive, xlsx, name="N", folder_id="F")
        kwargs = drive.files.return_value.create.call_args.kwargs
        assert kwargs["body"]["mimeType"] == SHEETS_MIMETYPE
        assert kwargs["body"]["parents"] == ["F"]


# ---------------------------------------------------------------------------
# _upload_raw_xlsx — Strategy 2.
# ---------------------------------------------------------------------------


class TestUploadRawXlsx:
    def test_returns_file_id_on_success(self, tmp_path: Path):
        drive = MagicMock()
        drive.files.return_value.create.return_value.next_chunk.return_value = (
            None, {"id": "FILE_ABC", "webViewLink": "url"}
        )
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        file_id = _upload_raw_xlsx(drive, xlsx, name="N", folder_id="F")
        assert file_id == "FILE_ABC"

    def test_uses_xlsx_mimetype_no_conversion(self, tmp_path: Path):
        drive = MagicMock()
        drive.files.return_value.create.return_value.next_chunk.return_value = (
            None, {"id": "F1", "webViewLink": "url"}
        )
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        _upload_raw_xlsx(drive, xlsx, name="N", folder_id="F")
        kwargs = drive.files.return_value.create.call_args.kwargs
        assert kwargs["body"]["mimeType"] == XLSX_MIMETYPE


# ---------------------------------------------------------------------------
# _create_via_sheets_api — Strategy 3.
# ---------------------------------------------------------------------------


class TestCreateViaSheetsApi:
    def _make_clients(self):
        drive = MagicMock()
        sheets = MagicMock()
        # Spreadsheet creation response.
        sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "SHEET_API_ID",
            "sheets": [
                {"properties": {"sheetId": 100, "title": "Results"}},
                {"properties": {"sheetId": 200, "title": "Legend"}},
            ],
        }
        return drive, sheets

    def test_returns_spreadsheet_id(self):
        drive, sheets = self._make_clients()
        sheet_id = _create_via_sheets_api(
            drive, sheets, csv_rows=[["ASIN"], ["B001"]],
            name="Test", folder_id="F",
        )
        assert sheet_id == "SHEET_API_ID"

    def test_writes_csv_data_to_results_sheet(self):
        drive, sheets = self._make_clients()
        _create_via_sheets_api(
            drive, sheets, csv_rows=[["A", "B"], ["1", "2"]],
            name="N", folder_id="F",
        )
        # Find the values.update calls.
        update_call = sheets.spreadsheets.return_value.values.return_value.update
        # Should be called at least twice (Results + Legend).
        assert update_call.call_count >= 2
        ranges = [c.kwargs.get("range") for c in update_call.call_args_list]
        assert any("Results" in r for r in ranges if r)
        assert any("Legend" in r for r in ranges if r)

    def test_applies_freeze_and_bold_formatting(self):
        drive, sheets = self._make_clients()
        _create_via_sheets_api(
            drive, sheets, csv_rows=[["A"], ["1"]], name="N", folder_id="F",
        )
        batch_update = sheets.spreadsheets.return_value.batchUpdate
        batch_update.assert_called_once()
        body = batch_update.call_args.kwargs["body"]
        request_kinds = [list(r.keys())[0] for r in body["requests"]]
        # Must include freeze panes + bold header + auto-resize.
        assert "updateSheetProperties" in request_kinds
        assert "repeatCell" in request_kinds

    def test_moves_to_target_folder(self):
        drive, sheets = self._make_clients()
        _create_via_sheets_api(
            drive, sheets, csv_rows=[["A"], ["1"]], name="N", folder_id="FOLDER",
        )
        # drive.files().update(fileId=..., addParents='FOLDER', ...)
        update_op = drive.files.return_value.update
        update_op.assert_called_once()
        kwargs = update_op.call_args.kwargs
        assert kwargs["fileId"] == "SHEET_API_ID"
        assert kwargs["addParents"] == "FOLDER"


# ---------------------------------------------------------------------------
# push_to_gsheets — top-level orchestration.
# ---------------------------------------------------------------------------


def _make_dummy_key(tmp_path: Path) -> Path:
    """Drop a placeholder service-account JSON so push_to_gsheets's
    file-existence check passes — _build_clients itself is always mocked."""
    key = tmp_path / "key.json"
    key.write_text('{"type": "service_account"}')
    return key


def _build_drive_clients_mock(
    *, strategy_1: str = "ok",
):
    """Helper to construct mocked drive + sheets clients.

    `strategy_1` controls how Strategy 1 (xlsx-conversion) responds:
      - "ok"     -> returns CONVERTED_SHEET (Strategy 1 wins; 2 + 3 not called).
      - "quota"  -> Strategy 1 raises a storage-quota error, Strategy 2 succeeds.
      - "fatal"  -> Strategy 1 raises a non-quota, non-transient error (used
                    to verify the chain re-raises without falling through).

    Uploads now use resumable chunked uploads, so the create chain is
    pumped via `next_chunk()` returning `(status, response)` tuples.
    """
    drive = MagicMock()
    sheets = MagicMock()

    create_chain = drive.files.return_value.create.return_value
    if strategy_1 == "ok":
        create_chain.next_chunk.return_value = (
            None, {"id": "CONVERTED_SHEET", "webViewLink": "url"}
        )
    elif strategy_1 == "quota":
        # Strategy 1 chunk loop raises quota; Strategy 2 chunk loop succeeds.
        # Each new request creates a fresh `.create.return_value` mock instance
        # — but MagicMock's attribute caching means we need a side_effect that
        # tracks call count.
        err = Exception("storageQuotaExceeded")
        call_count = {"n": 0}

        def chunk_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise err
            return (None, {"id": "RAW_FILE", "webViewLink": "url"})

        create_chain.next_chunk.side_effect = chunk_side_effect
    elif strategy_1 == "fatal":
        # Non-transient AuthError-style failure — must not be retried, must
        # not fall through to Strategy 2.
        create_chain.next_chunk.side_effect = Exception("auth failure")
    else:
        raise ValueError(f"unknown strategy_1 mode: {strategy_1!r}")

    # Sheets API create.
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "SHEETS_API_ID",
        "sheets": [{"properties": {"sheetId": 1, "title": "Results"}}],
    }

    return drive, sheets


class TestPushToGsheets:
    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_strategy_1_success_returns_sheet_id(
        self, build_clients_mock, tmp_path: Path
    ):
        drive, sheets = _build_drive_clients_mock(strategy_1="ok")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        result = push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys", key_path=_make_dummy_key(tmp_path),
        )
        assert result == "CONVERTED_SHEET"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_quota_error_falls_through_to_strategy_2(
        self, build_clients_mock, tmp_path: Path
    ):
        # Strategy 1 raises quota; Strategy 2 succeeds.
        drive, sheets = _build_drive_clients_mock(strategy_1="quota")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        result = push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys", key_path=_make_dummy_key(tmp_path),
        )
        # Result is the file ID from Strategy 2.
        assert result == "RAW_FILE"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_non_quota_error_does_not_fall_through(
        self, build_clients_mock, tmp_path: Path
    ):
        # Strategy 1 raises non-quota error → re-raise without trying 2/3.
        drive, sheets = _build_drive_clients_mock(strategy_1="fatal")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        with pytest.raises(Exception, match="auth failure"):
            push_to_gsheets(
                xlsx_path=xlsx, niche="kids-toys",
                key_path=_make_dummy_key(tmp_path),
            )

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_writes_id_file_on_success(
        self, build_clients_mock, tmp_path: Path
    ):
        drive, sheets = _build_drive_clients_mock(strategy_1="ok")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        id_file = tmp_path / "kids_toys_gsheet_id.txt"
        push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path), id_file_path=id_file,
        )
        assert id_file.exists()
        assert id_file.read_text(encoding="utf-8").strip() == "CONVERTED_SHEET"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_missing_xlsx_raises(self, build_clients_mock, tmp_path: Path):
        # _build_clients shouldn't even be called.
        with pytest.raises(FileNotFoundError):
            push_to_gsheets(
                xlsx_path=tmp_path / "nonexistent.xlsx",
                niche="kids-toys",
                key_path=_make_dummy_key(tmp_path),
            )
        build_clients_mock.assert_not_called()

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_missing_key_raises(self, build_clients_mock, tmp_path: Path):
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        with pytest.raises(FileNotFoundError):
            push_to_gsheets(
                xlsx_path=xlsx, niche="kids-toys",
                key_path=tmp_path / "nonexistent.json",
            )
        build_clients_mock.assert_not_called()

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_deletes_previous_sheet_when_id_file_exists(
        self, build_clients_mock, tmp_path: Path
    ):
        drive, sheets = _build_drive_clients_mock(strategy_1="ok")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        id_file = tmp_path / "prev_id.txt"
        id_file.write_text("PREVIOUS_ID")
        push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path), id_file_path=id_file,
        )
        # Previous-sheet delete should have been attempted.
        delete_op = drive.files.return_value.delete
        delete_op.assert_called_once()
        assert delete_op.call_args.kwargs["fileId"] == "PREVIOUS_ID"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_full_chain_quota_then_raw_fail_then_sheets_api_succeeds(
        self, build_clients_mock, tmp_path: Path
    ):
        # End-to-end: Strategy 1 raises quota → Strategy 2 fails → Strategy 3
        # succeeds. After the fix, csv_rows can be omitted — the orchestrator
        # reads them from the xlsx itself so Strategy 3 always has data.
        drive = MagicMock()
        sheets = MagicMock()
        # Resumable upload chunks: first call raises storageQuotaExceeded,
        # second call (Strategy 2 raw) raises a non-quota, non-transient error.
        next_chunk = drive.files.return_value.create.return_value.next_chunk
        next_chunk.side_effect = [
            Exception("storageQuotaExceeded"),  # Strategy 1
            Exception("raw upload failed"),     # Strategy 2 (non-transient)
        ]
        # Strategy 3 (Sheets API) succeeds.
        sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "FALLBACK_ID",
            "sheets": [{"properties": {"sheetId": 1, "title": "Results"}}],
        }
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        result = push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path),
            csv_rows=[["ASIN", "Brand"], ["B001", "Acme"]],
        )
        assert result == "FALLBACK_ID"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_all_strategies_fail_raises_push_failed_error(
        self, build_clients_mock, tmp_path: Path
    ):
        # Strategy 1 quota → 2 fails → 3 fails → PushFailedError.
        drive = MagicMock()
        sheets = MagicMock()
        next_chunk = drive.files.return_value.create.return_value.next_chunk
        next_chunk.side_effect = [
            Exception("storageQuotaExceeded"),
            Exception("raw upload failed"),
        ]
        sheets.spreadsheets.return_value.create.return_value.execute.side_effect = (
            Exception("sheets api also down")
        )
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        with pytest.raises(PushFailedError):
            push_to_gsheets(
                xlsx_path=xlsx, niche="kids-toys",
                key_path=_make_dummy_key(tmp_path),
                csv_rows=[["ASIN"], ["B001"]],
            )

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_delete_previous_runs_after_successful_upload(
        self, build_clients_mock, tmp_path: Path
    ):
        # Behaviour change vs legacy: previous-sheet delete is deferred until
        # AFTER the new upload succeeds. This prevents the half-deleted state
        # where prev is gone but new upload also failed.
        drive, sheets = _build_drive_clients_mock(strategy_1="ok")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        id_file = tmp_path / "prev.txt"
        id_file.write_text("PREV_ID")

        manager = MagicMock()
        manager.attach_mock(drive.files.return_value.delete, "delete")
        manager.attach_mock(drive.files.return_value.create, "create")

        push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path),
            id_file_path=id_file,
        )

        # create must appear before delete in mock_calls order.
        names = [c[0] for c in manager.mock_calls if c[0] in {"delete", "create"}]
        assert names.index("create") < names.index("delete")

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_previous_id_preserved_when_upload_fails(
        self, build_clients_mock, tmp_path: Path
    ):
        # If the upload chain fails entirely, the previous sheet must NOT
        # be deleted — better to keep the stale sheet visible than orphan
        # everything.
        drive, sheets = _build_drive_clients_mock(strategy_1="fatal")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        id_file = tmp_path / "id.txt"
        id_file.write_text("OLD_ID")
        with pytest.raises(Exception, match="auth failure"):
            push_to_gsheets(
                xlsx_path=xlsx, niche="kids-toys",
                key_path=_make_dummy_key(tmp_path),
                id_file_path=id_file,
            )
        # id-file unchanged.
        assert id_file.read_text(encoding="utf-8").strip() == "OLD_ID"
        # delete must not have been called (no prev-sheet teardown on failure).
        drive.files.return_value.delete.assert_not_called()

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_strategy_3_runs_without_csv_rows_supplied(
        self, build_clients_mock, tmp_path: Path
    ):
        # Regression for HIGH-9: caller does NOT supply csv_rows. The
        # orchestrator must read them from the xlsx itself so Strategy 3
        # can run — the legacy default silently skipped Strategy 3 here.
        import openpyxl
        drive = MagicMock()
        sheets = MagicMock()
        next_chunk = drive.files.return_value.create.return_value.next_chunk
        next_chunk.side_effect = [
            Exception("storageQuotaExceeded"),  # Strategy 1
            Exception("raw upload failed"),     # Strategy 2
        ]
        sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "FB_ID",
            "sheets": [{"properties": {"sheetId": 1, "title": "Results"}}],
        }
        build_clients_mock.return_value = (drive, sheets)

        # Real openpyxl xlsx with a Results sheet.
        xlsx = tmp_path / "x.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Results"
        ws.append(["ASIN", "Brand"])
        ws.append(["B001", "Acme"])
        wb.save(xlsx)

        result = push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path),
            csv_rows=None,  # explicit — must NOT skip Strategy 3
        )
        assert result == "FB_ID"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_strategy_3_orphan_sheet_cleanup_on_population_failure(
        self, build_clients_mock, tmp_path: Path
    ):
        # Regression for HIGH-13: if Strategy 3's create succeeds but a
        # subsequent populate fails, the half-empty sheet must be deleted
        # so it doesn't leak in Drive.
        drive = MagicMock()
        sheets = MagicMock()
        next_chunk = drive.files.return_value.create.return_value.next_chunk
        next_chunk.side_effect = [
            Exception("storageQuotaExceeded"),
            Exception("raw upload failed"),
        ]
        sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "ORPHAN_ID",
            "sheets": [{"properties": {"sheetId": 1, "title": "Results"}}],
        }
        # values.update fails after spreadsheet was created.
        sheets.spreadsheets.return_value.values.return_value.update.return_value.execute.side_effect = (
            Exception("populate failed")
        )
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        with pytest.raises(PushFailedError):
            push_to_gsheets(
                xlsx_path=xlsx, niche="kids-toys",
                key_path=_make_dummy_key(tmp_path),
                csv_rows=[["ASIN"], ["B001"]],
            )
        # Orphan delete attempted on the half-created sheet.
        delete_op = drive.files.return_value.delete
        delete_op.assert_called_once()
        assert delete_op.call_args.kwargs["fileId"] == "ORPHAN_ID"

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_strategy_3_clamps_freeze_for_narrow_frames(
        self, build_clients_mock, tmp_path: Path
    ):
        # Regression for HIGH-9 / MEDIUM-9: if csv_rows has fewer than 3
        # columns, frozenColumnCount must clamp — Sheets API rejects a
        # frozen count larger than the grid width.
        drive = MagicMock()
        sheets = MagicMock()
        next_chunk = drive.files.return_value.create.return_value.next_chunk
        next_chunk.side_effect = [
            Exception("storageQuotaExceeded"),
            Exception("raw upload failed"),
        ]
        sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
            "spreadsheetId": "OK_ID",
            "sheets": [{"properties": {"sheetId": 1, "title": "Results"}}],
        }
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path),
            csv_rows=[["only_one"]],  # 1 column, narrower than frozen=3
        )
        # Inspect the batchUpdate request for clamped frozenColumnCount.
        batch_kwargs = (
            sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs
        )
        requests = batch_kwargs["body"]["requests"]
        freeze_req = next(r for r in requests if "updateSheetProperties" in r)
        frozen = freeze_req["updateSheetProperties"]["properties"][
            "gridProperties"
        ]["frozenColumnCount"]
        assert frozen == 1  # clamped from 3 down to ncols


# ---------------------------------------------------------------------------
# run_step contract.
# ---------------------------------------------------------------------------


class TestRunStep:
    def test_run_step_returns_input_unchanged_when_no_xlsx_path(self):
        df = pd.DataFrame([{"ASIN": "B001"}])
        out = run_step(df, {})
        pd.testing.assert_frame_equal(out, df)

    @patch("fba_engine.steps.push_to_gsheets.push_to_gsheets")
    def test_run_step_calls_push_when_xlsx_path_set(
        self, push_mock, tmp_path: Path
    ):
        push_mock.return_value = "RESULT_ID"
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        df = pd.DataFrame([{"ASIN": "B001"}])
        out = run_step(
            df, {
                "xlsx_path": str(xlsx),
                "niche": "kids-toys",
                "key_path": str(tmp_path / "key.json"),
            },
        )
        push_mock.assert_called_once()
        # Returns df unchanged.
        pd.testing.assert_frame_equal(out, df)
