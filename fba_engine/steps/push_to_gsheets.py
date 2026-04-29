"""Push-to-Google-Sheets step (Phase 5 — formerly Skill 5 part 3 in the legacy Keepa pipeline).

Uploads a styled XLSX (produced by step 4c.2) to Google Drive, with a
three-strategy fallback chain:

  1. **xlsx-conversion upload** — `files.create` with `mimeType =
     application/vnd.google-apps.spreadsheet` so Drive auto-converts
     xlsx to a native Google Sheet (preserves all openpyxl styling).
  2. **raw xlsx upload** — same `files.create` but with the xlsx mimetype,
     uploading the file as-is. Falls back here when Strategy 1 hits a
     storage-quota error (typical for service accounts not in a Workspace
     domain).
  3. **Sheets API create + populate** — creates a new spreadsheet via the
     Sheets API, moves it to the target folder, writes CSV rows + legend
     data, applies basic formatting (freeze panes, bold header row, auto-
     resize). No styling parity with the openpyxl xlsx — this is the
     last-resort path when Drive uploads are blocked entirely.

Logic ported 1:1 from `fba_engine/_legacy_keepa/skills/skill-5-build-output/
push_to_gsheets.js` (311 LOC).

**Required Python packages (already installed in this env):**
  - google-api-python-client
  - google-auth

**Required external setup:**
  - Service account JSON key (default path: `config/google-service-account.json`)
  - Target Drive folder shared with the service account email

Standalone CLI invocation:

    python -m fba_engine.steps.push_to_gsheets \\
        --niche kids-toys \\
        --base fba_engine/data/niches/kids-toys
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# ────────────────────────────────────────────────────────────────────────
# Constants — pinned to the legacy values for direct migration.
# ────────────────────────────────────────────────────────────────────────

DEFAULT_FOLDER_ID = "1uFYiz7rYFm5ZJHgkXJh86jKaigK9H6yd"
DEFAULT_KEY_PATH = "config/google-service-account.json"

XLSX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
SHEETS_MIMETYPE = "application/vnd.google-apps.spreadsheet"

DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
)

# Legend body for the Sheets-API fallback path. Must match the legacy JS
# byte-for-byte so the fallback sheet renders the same explanations.
LEGEND_DATA: list[list[str]] = [
    ["Verdict", "Meaning"],
    ["YES", "Composite 7+, all filters pass -- pursue this product"],
    ["MAYBE", "Composite 5-6, one concern -- review needed"],
    ["MAYBE-ROI", "ROI below 20% estimated -- may improve with real trade price"],
    ["BRAND APPROACH", "2-3 sellers, weak listing -- contact brand direct"],
    ["BUY THE DIP", "Price 30%+ below 90-day avg -- recovery pattern detected"],
    ["PRICE EROSION", "Consistent downward slope -- reject"],
    ["GATED", "Restricted listing -- flag for ungating decision"],
    ["NO", "Fails filter, reason stated"],
    ["", ""],
    ["Lane", "Meaning"],
    ["BALANCED", "Strong velocity + strong margin -- premium opportunity"],
    ["PROFIT", "Strong unit profit/ROI, may have lower velocity -- capital efficient"],
    ["CASH FLOW", "High velocity, acceptable margin -- turnover and cash generation"],
    ["", ""],
    ["Column", "Explanation"],
    ["Price Compression", "OK / SQUEEZED (10-20% below 90d avg) / COMPRESSED (20%+ below avg)"],
    ["Est Cost 65%", "Estimated cost at 65% of selling price (rough placeholder)"],
    ["Max Cost for 20% ROI", "Maximum you can pay for stock and still hit 20% ROI after FBA fees"],
    ["Price Stability", "STABLE / SLIGHT DIP / DROPPING / RISING / SURGING"],
    ["Listing Quality", "STRONG (6+ images, A+, 5+ bullets) / AVERAGE / WEAK"],
    ["Weight Flag", "OK / HEAVY (>5kg) / OVERSIZE (>45cm) / HEAVY+OVERSIZE"],
    ["Brand 1P", "Y = brand sells direct on Amazon (hard to compete)"],
]


class PushFailedError(RuntimeError):
    """All three upload strategies exhausted without success."""


# ────────────────────────────────────────────────────────────────────────
# Auth + client construction.
# ────────────────────────────────────────────────────────────────────────


def _build_clients(key_path: str | Path):
    """Return (drive, sheets) Google API client objects.

    Imported lazily so the module loads cleanly when googleapis isn't
    installed (the auth/build path is only reached when actually pushing).
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=list(DEFAULT_SCOPES)
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    sheets = build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    )
    return drive, sheets


# ────────────────────────────────────────────────────────────────────────
# Helpers.
# ────────────────────────────────────────────────────────────────────────


def _is_quota_error(err: Exception) -> bool:
    """True if `err` represents a Drive storage-quota failure.

    Matches the legacy JS rule: status 403 OR message contains
    'quota'/'storageQuota'. The status check is int-coerced because
    `googleapiclient.errors.HttpError.resp.status` may surface as either
    int or string depending on httplib2 version — relying on `== 403`
    alone risks silent miss in production.
    """
    resp = getattr(err, "resp", None)
    status = getattr(resp, "status", None) if resp is not None else None
    try:
        if status is not None and int(status) == 403:
            return True
    except (TypeError, ValueError):
        pass
    msg = str(err).lower()
    return "quota" in msg or "storagequota" in msg


def csv_rows_for_upload(df: pd.DataFrame) -> list[list[str]]:
    """Convert a DataFrame to the row-of-rows shape Sheets API expects.

    First row is the header. NaN cells become empty strings (NOT 'nan' the
    literal). All values are stringified — Sheets API accepts mixed types
    but explicit strings keep the upload deterministic.
    """
    rows: list[list[str]] = [list(df.columns)]
    for _, row in df.iterrows():
        out_row: list[str] = []
        for v in row:
            if v is None:
                out_row.append("")
                continue
            if isinstance(v, float) and math.isnan(v):
                out_row.append("")
                continue
            try:
                if pd.isna(v):
                    out_row.append("")
                    continue
            except (TypeError, ValueError):
                pass
            out_row.append(str(v))
        rows.append(out_row)
    return rows


def _delete_previous_sheet(drive, previous_id: str | None) -> bool:
    """Delete the prior sheet at `previous_id`. Returns True on success.

    Errors are logged to stderr and swallowed — a stale ID file shouldn't
    block a fresh upload.
    """
    if not previous_id:
        return False
    try:
        drive.files().delete(
            fileId=previous_id, supportsAllDrives=True
        ).execute()
        print(f"Deleted previous sheet: {previous_id}")
        return True
    except Exception as err:
        print(
            f"Could not delete previous sheet ({previous_id}): {err}",
            file=sys.stderr,
        )
        return False


# ────────────────────────────────────────────────────────────────────────
# Strategy 1 — xlsx-conversion upload.
# ────────────────────────────────────────────────────────────────────────


def _upload_with_conversion(
    drive, xlsx_path: Path, name: str, folder_id: str
) -> str:
    """Upload xlsx to Drive with auto-conversion to a Google Sheet.

    Returns the new spreadsheet's id. Raises whatever the API raises —
    callers detect quota errors via `_is_quota_error` and fall through
    to Strategy 2.
    """
    from googleapiclient.http import MediaFileUpload

    file_metadata = {"name": name, "mimeType": SHEETS_MIMETYPE}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(xlsx_path), mimetype=XLSX_MIMETYPE, resumable=False)
    res = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()
    return res["id"]


# ────────────────────────────────────────────────────────────────────────
# Strategy 2 — raw xlsx upload (no conversion).
# ────────────────────────────────────────────────────────────────────────


def _upload_raw_xlsx(
    drive, xlsx_path: Path, name: str, folder_id: str
) -> str:
    """Upload xlsx to Drive as-is, without conversion. Returns the file id."""
    from googleapiclient.http import MediaFileUpload

    file_metadata = {"name": name, "mimeType": XLSX_MIMETYPE}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(xlsx_path), mimetype=XLSX_MIMETYPE, resumable=False)
    res = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()
    return res["id"]


# ────────────────────────────────────────────────────────────────────────
# Strategy 3 — Sheets API create + populate.
# ────────────────────────────────────────────────────────────────────────


def _create_via_sheets_api(
    drive, sheets, csv_rows: list[list[str]], name: str, folder_id: str
) -> str:
    """Last-resort: create a new Sheet via Sheets API + populate.

    Returns the spreadsheetId. No openpyxl styling parity — this path
    exists for service accounts with zero Drive storage quota.
    """
    create_res = sheets.spreadsheets().create(
        body={
            "properties": {"title": name},
            "sheets": [
                {"properties": {"title": "Results"}},
                {"properties": {"title": "Legend"}},
            ],
        }
    ).execute()
    sheet_id = create_res["spreadsheetId"]
    results_sheet_id = create_res["sheets"][0]["properties"]["sheetId"]

    # Move to target folder.
    if folder_id:
        try:
            drive.files().update(
                fileId=sheet_id,
                addParents=folder_id,
                fields="id, parents",
                supportsAllDrives=True,
            ).execute()
        except Exception as err:
            print(f"Could not move to folder: {err}", file=sys.stderr)

    # Write Results data.
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Results!A1",
        valueInputOption="RAW",
        body={"values": csv_rows},
    ).execute()

    # Write Legend data.
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Legend!A1",
        valueInputOption="RAW",
        body={"values": LEGEND_DATA},
    ).execute()

    # Apply basic formatting via batchUpdate.
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "requests": [
                # Freeze first row + first 3 columns.
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": results_sheet_id,
                            "gridProperties": {
                                "frozenRowCount": 1,
                                "frozenColumnCount": 3,
                            },
                        },
                        "fields": (
                            "gridProperties.frozenRowCount,"
                            "gridProperties.frozenColumnCount"
                        ),
                    }
                },
                # Bold header row.
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": results_sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True}
                            }
                        },
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                },
                # Auto-resize columns. Was 55 in legacy JS; bumped to 67 to
                # cover the wider final_results schema introduced in step 4c.1
                # (EAN/UPC/GTIN appended). Sheets API tolerates indices past
                # the actual grid width, so this is safe even if the source
                # frame is narrower.
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": results_sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": 67,
                        }
                    }
                },
            ]
        },
    ).execute()

    return sheet_id


# ────────────────────────────────────────────────────────────────────────
# Top-level orchestration.
# ────────────────────────────────────────────────────────────────────────


def push_to_gsheets(
    xlsx_path: str | Path,
    niche: str,
    key_path: str | Path = DEFAULT_KEY_PATH,
    folder_id: str = DEFAULT_FOLDER_ID,
    id_file_path: str | Path | None = None,
    csv_rows: list[list[str]] | None = None,
) -> str:
    """Push the styled XLSX to Google Drive. Returns the Sheet/file id.

    Walks the three-strategy fallback chain. Writes the resulting id to
    `id_file_path` if provided. Raises FileNotFoundError if either the
    xlsx or the service-account key is missing. Raises PushFailedError
    if all three strategies fail.

    `csv_rows` is only used for Strategy 3 (the Sheets API fallback). If
    not provided, Strategy 3 is skipped — the legacy JS reads the CSV
    from disk; here we let the caller pre-render via `csv_rows_for_upload`.
    """
    xlsx_path = Path(xlsx_path)
    key_path = Path(key_path)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"XLSX not found: {xlsx_path}")
    if not key_path.exists():
        raise FileNotFoundError(
            f"Service account key not found: {key_path}\n"
            "Setup: download the JSON key from Google Cloud Console and "
            "save to config/google-service-account.json (or pass --key)."
        )

    drive, sheets = _build_clients(key_path)

    # Read previous id, then attempt cleanup.
    previous_id = None
    id_file = Path(id_file_path) if id_file_path else None
    if id_file and id_file.exists():
        previous_id = id_file.read_text(encoding="utf-8").strip() or None
    _delete_previous_sheet(drive, previous_id)

    title = (
        f"{niche.replace('-', ' ').title()} Final Results — "
        f"{date.today().isoformat()}"
    )

    # Strategy 1: xlsx-conversion upload.
    try:
        sheet_id = _upload_with_conversion(drive, xlsx_path, title, folder_id)
        if id_file:
            id_file.write_text(sheet_id, encoding="utf-8")
        print(f"Uploaded (xlsx conversion): {title}")
        print(f"Sheet ID: {sheet_id}")
        return sheet_id
    except Exception as err:
        if not _is_quota_error(err):
            raise
        print(
            f"Xlsx conversion upload hit quota error; falling back to raw xlsx: {err}",
            file=sys.stderr,
        )

    # Strategy 2: raw xlsx upload.
    try:
        file_id = _upload_raw_xlsx(drive, xlsx_path, title, folder_id)
        if id_file:
            id_file.write_text(file_id, encoding="utf-8")
        print(f"Uploaded (raw xlsx, not converted): {title}")
        print(f"File ID: {file_id}")
        print("Note: file is xlsx in Drive. Open in Google Sheets to view.")
        return file_id
    except Exception as err:
        print(f"Raw upload also failed: {err}", file=sys.stderr)

    # Strategy 3: Sheets API create + populate (only if csv_rows supplied).
    if csv_rows is not None:
        try:
            sheet_id = _create_via_sheets_api(
                drive, sheets, csv_rows, title, folder_id
            )
            if id_file:
                id_file.write_text(sheet_id, encoding="utf-8")
            print(f"Uploaded (Sheets API fallback): {title}")
            print(f"Sheet ID: {sheet_id}")
            print("Note: styling is basic. Free up Drive quota for full styling.")
            return sheet_id
        except Exception as err:
            print(f"Sheets API fallback also failed: {err}", file=sys.stderr)

    # All strategies exhausted.
    print("", file=sys.stderr)
    print("=== UPLOAD FAILED ===", file=sys.stderr)
    print(
        "All three upload strategies exhausted. The service account may have "
        "zero Drive storage quota (common for Cloud-only service accounts).",
        file=sys.stderr,
    )
    print(
        f"Manual fallback: drag {xlsx_path} into "
        f"https://drive.google.com/drive/folders/{folder_id}",
        file=sys.stderr,
    )
    raise PushFailedError("All Google Drive / Sheets upload strategies failed.")


# ────────────────────────────────────────────────────────────────────────
# Step contract.
# ────────────────────────────────────────────────────────────────────────


def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Step-runner-compatible wrapper. Passthrough on the DataFrame.

    If `config["xlsx_path"]` is set, attempt an upload — otherwise no-op.
    Required `config` keys when uploading:
      - xlsx_path: path to the styled XLSX produced by step 4c.2
      - niche: niche slug (used in the sheet title)
      - key_path: optional path to the service-account JSON key

    The DataFrame is returned unchanged (this step is a side-effect step).
    """
    if not config.get("xlsx_path"):
        return df

    push_to_gsheets(
        xlsx_path=config["xlsx_path"],
        niche=config.get("niche", "results"),
        key_path=config.get("key_path", DEFAULT_KEY_PATH),
        folder_id=config.get("folder_id", DEFAULT_FOLDER_ID),
        id_file_path=config.get("id_file_path"),
        csv_rows=csv_rows_for_upload(df) if config.get("enable_sheets_api_fallback") else None,
    )
    return df


# ────────────────────────────────────────────────────────────────────────
# CLI — mirrors legacy push_to_gsheets.js paths.
# ────────────────────────────────────────────────────────────────────────


def run(niche: str, base: Path, key_path: Path, folder_id: str) -> None:
    base = Path(base)
    niche_snake = niche.replace("-", "_")

    xlsx_path = base / f"{niche_snake}_final_results.xlsx"
    id_file_path = base / f"{niche_snake}_gsheet_id.txt"
    csv_path = base / f"{niche_snake}_final_results.csv"
    if not csv_path.exists():
        csv_path = base / "working" / f"{niche_snake}_final_results.csv"

    csv_rows = None
    if csv_path.exists():
        df = pd.read_csv(
            csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        csv_rows = csv_rows_for_upload(df)

    sheet_id = push_to_gsheets(
        xlsx_path=xlsx_path,
        niche=niche,
        key_path=key_path,
        folder_id=folder_id,
        id_file_path=id_file_path,
        csv_rows=csv_rows,
    )
    print(f"URL: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    print(f"ID saved to: {id_file_path}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 5 Google Sheets push — uploads the styled XLSX produced "
            "by step 4c.2 to Drive with a 3-strategy fallback chain."
        )
    )
    parser.add_argument("--niche", required=True, help="Niche slug")
    parser.add_argument(
        "--base", required=True, type=Path,
        help="Base directory containing {niche}_final_results.xlsx",
    )
    parser.add_argument(
        "--key", type=Path, default=Path(DEFAULT_KEY_PATH),
        help=f"Service account JSON key path (default: {DEFAULT_KEY_PATH})",
    )
    parser.add_argument(
        "--folder", default=DEFAULT_FOLDER_ID,
        help=f"Drive folder ID (default: {DEFAULT_FOLDER_ID})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    run(niche=args.niche, base=args.base, key_path=args.key, folder_id=args.folder)
