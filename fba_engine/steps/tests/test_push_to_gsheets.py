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
    def test_403_status_treated_as_quota(self):
        # SimpleNamespace mimics httplib2.Response shape more faithfully than
        # MagicMock — googleapiclient.errors.HttpError surfaces resp.status
        # via that attribute (sometimes int, sometimes string).
        err = Exception("forbidden")
        err.resp = SimpleNamespace(status=403)
        assert _is_quota_error(err) is True

    def test_403_status_as_string_treated_as_quota(self):
        # httplib2 has historically surfaced status as a string. Reviewer
        # HIGH-1: int-coerce so we don't silently miss this in production.
        err = Exception("forbidden")
        err.resp = SimpleNamespace(status="403")
        assert _is_quota_error(err) is True

    def test_message_with_storage_quota(self):
        err = Exception("storageQuota exceeded for service account")
        assert _is_quota_error(err) is True

    def test_message_with_quota(self):
        err = Exception("Daily quota reached")
        assert _is_quota_error(err) is True

    def test_unrelated_error_returns_false(self):
        err = Exception("network timeout")
        assert _is_quota_error(err) is False


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
        # Simulate a happy-path response.
        drive.files.return_value.create.return_value.execute.return_value = {
            "id": "SHEET123",
            "webViewLink": "https://docs.google.com/spreadsheets/d/SHEET123/edit",
        }
        xlsx = tmp_path / "test.xlsx"
        xlsx.write_bytes(b"PKfake-xlsx")  # minimal placeholder
        sheet_id = _upload_with_conversion(
            drive, xlsx, name="Test", folder_id="FOLDER1"
        )
        assert sheet_id == "SHEET123"

    def test_uses_sheets_mimetype_for_file_metadata(self, tmp_path: Path):
        drive = MagicMock()
        drive.files.return_value.create.return_value.execute.return_value = {
            "id": "S1", "webViewLink": "url"
        }
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
        drive.files.return_value.create.return_value.execute.return_value = {
            "id": "FILE_ABC", "webViewLink": "url"
        }
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        file_id = _upload_raw_xlsx(drive, xlsx, name="N", folder_id="F")
        assert file_id == "FILE_ABC"

    def test_uses_xlsx_mimetype_no_conversion(self, tmp_path: Path):
        drive = MagicMock()
        drive.files.return_value.create.return_value.execute.return_value = {
            "id": "F1", "webViewLink": "url"
        }
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
      - "fatal"  -> Strategy 1 raises a non-quota error (used to verify the
                    chain re-raises without falling through). Use a message
                    that does NOT contain the substring 'quota' to avoid the
                    legacy substring matcher misclassifying it.
    """
    drive = MagicMock()
    sheets = MagicMock()

    create_chain = drive.files.return_value.create.return_value
    if strategy_1 == "ok":
        create_chain.execute.return_value = {
            "id": "CONVERTED_SHEET", "webViewLink": "url"
        }
    elif strategy_1 == "quota":
        err = Exception("storageQuota exceeded")
        create_chain.execute.side_effect = [
            err, {"id": "RAW_FILE", "webViewLink": "url"},
        ]
    elif strategy_1 == "fatal":
        create_chain.execute.side_effect = Exception("auth failure")
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
        # Reviewer M4: end-to-end test of the full 3-strategy chain.
        # Strategy 1 raises quota → Strategy 2 fails → Strategy 3 succeeds.
        drive = MagicMock()
        sheets = MagicMock()
        # Strategy 1 quota error, Strategy 2 also fails (e.g. raw upload disabled).
        drive.files.return_value.create.return_value.execute.side_effect = [
            Exception("storageQuota exceeded"),  # strategy 1
            Exception("raw upload failed"),       # strategy 2
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
        # Reviewer M5: full failure path raises PushFailedError, not silently
        # exits. Strategy 1 quota, 2 fails, 3 fails.
        drive = MagicMock()
        sheets = MagicMock()
        drive.files.return_value.create.return_value.execute.side_effect = [
            Exception("storageQuota exceeded"),
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
    def test_delete_previous_runs_before_upload(
        self, build_clients_mock, tmp_path: Path
    ):
        # Reviewer M6: sequencing — previous-sheet delete must precede upload,
        # so a stale id-file doesn't survive a successful new upload.
        drive, sheets = _build_drive_clients_mock(strategy_1="ok")
        build_clients_mock.return_value = (drive, sheets)
        xlsx = tmp_path / "x.xlsx"
        xlsx.write_bytes(b"x")
        id_file = tmp_path / "prev.txt"
        id_file.write_text("PREV_ID")

        # Track the order of drive.files() child-method calls.
        manager = MagicMock()
        manager.attach_mock(drive.files.return_value.delete, "delete")
        manager.attach_mock(drive.files.return_value.create, "create")

        push_to_gsheets(
            xlsx_path=xlsx, niche="kids-toys",
            key_path=_make_dummy_key(tmp_path),
            id_file_path=id_file,
        )

        # delete must appear before create in mock_calls order.
        names = [c[0] for c in manager.mock_calls if c[0] in {"delete", "create"}]
        assert names.index("delete") < names.index("create")

    @patch("fba_engine.steps.push_to_gsheets._build_clients")
    def test_id_file_only_written_after_successful_upload(
        self, build_clients_mock, tmp_path: Path
    ):
        # Reviewer M6: id-file write must happen AFTER upload returns. If
        # the upload raises, the id-file should NOT contain a stale or new id.
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
        # The previous delete attempt removes OLD_ID from Drive but the file
        # contents are not changed by push_to_gsheets when upload fails.
        # Either OLD_ID still present OR file was untouched — but it was NOT
        # overwritten with a new id.
        contents = id_file.read_text(encoding="utf-8").strip()
        assert contents == "OLD_ID"


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
