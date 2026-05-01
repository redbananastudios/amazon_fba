#!/usr/bin/env node
/**
 * push_to_gsheets.js
 * Uploads an XLSX file to Google Drive as a Google Sheet.
 * Google auto-converts xlsx → Sheets, preserving styling.
 *
 * Usage:
 *   # Generic (preferred — used by the strategy runner):
 *   node push_to_gsheets.js \
 *     --xlsx /path/to/file.xlsx \
 *     --title "Sheet Title" \
 *     [--id-file /path/to/store-the-id.txt] \
 *     [--folder <drive-folder-id>] \
 *     [--key /path/to/service-account.json]
 *
 *   # Legacy (kept for the niche-driven keepa_niche pipeline):
 *   node push_to_gsheets.js --niche educational-toys
 *
 * Requirements:
 *   - service account key (default: <repo>/fba_engine/_legacy_keepa/config/google-service-account.json)
 *   - GOOGLE_DRIVE_FOLDER_ID env var or --folder argument
 *   - googleapis npm package (npm install in fba_engine/_legacy_keepa/)
 *
 * Stdout contract: emits one line `URL: <https://docs.google.com/...>`
 * on success — the runner parses this to surface the sheet URL.
 */

const { google } = require('googleapis');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
function flag(name) {
  const i = args.indexOf('--' + name);
  return i !== -1 ? args[i + 1] : null;
}

// Resolve project root relative to this script (skills/skill-5-build-output/ → _legacy_keepa root)
const BASE = path.resolve(__dirname, '..', '..');

// Two invocation modes:
//   1. Generic: --xlsx + --title + optional --id-file. Used by the
//      keepa_finder strategy runner — no niche concept involved.
//   2. Legacy: --niche, derives all paths from the niche directory.
//      Kept for the keepa_niche / supplier_pricelist pipelines.
const niche = flag('niche');
const xlsxArg = flag('xlsx');
const titleArg = flag('title');
const idFileArg = flag('id-file');

let xlsxPath, idFile, title;
if (xlsxArg) {
  // Generic mode
  xlsxPath = path.resolve(xlsxArg);
  if (!titleArg) {
    console.error('--xlsx requires --title (the title to use for the Google Sheet).');
    process.exit(1);
  }
  title = titleArg;
  // id-file is optional in generic mode — when omitted, no previous-sheet
  // cleanup happens (each run creates a fresh sheet).
  idFile = idFileArg ? path.resolve(idFileArg) : null;
} else if (niche) {
  // Legacy mode
  const DATA = path.join(BASE, 'data', niche);
  const nicheSnake = niche.replace(/-/g, '_');
  xlsxPath = path.join(DATA, `${nicheSnake}_final_results.xlsx`);
  idFile = path.join(DATA, `${nicheSnake}_gsheet_id.txt`);
  const nicheLabel = niche.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const date = new Date().toISOString().slice(0, 10);
  title = titleArg || `${nicheLabel} Final Results — ${date}`;
} else {
  console.error('Usage: node push_to_gsheets.js --xlsx <path> --title <title> [--id-file <path>]');
  console.error('   or: node push_to_gsheets.js --niche <niche>');
  process.exit(1);
}

const keyPath = flag('key') || path.join(BASE, 'config', 'google-service-account.json');
const folderId = flag('folder') || process.env.GOOGLE_DRIVE_FOLDER_ID || '1uFYiz7rYFm5ZJHgkXJh86jKaigK9H6yd';

if (!fs.existsSync(xlsxPath)) {
  console.error(`XLSX not found: ${xlsxPath}`);
  process.exit(1);
}

if (!fs.existsSync(keyPath)) {
  console.error(`Service account key not found: ${keyPath}`);
  console.error('Setup: Create a Google Cloud service account, download JSON key,');
  console.error('save to config/google-service-account.json');
  process.exit(1);
}

async function main() {
  // Auth -- need full drive scope for service accounts with zero quota
  const auth = new google.auth.GoogleAuth({
    keyFile: keyPath,
    scopes: [
      'https://www.googleapis.com/auth/drive',
      'https://www.googleapis.com/auth/spreadsheets',
    ],
  });
  const drive = google.drive({ version: 'v3', auth });
  const sheets = google.sheets({ version: 'v4', auth });

  // `title` is now computed at the top-level args block — both modes
  // (generic --title / legacy --niche-derived) populate it before main()
  // runs. The previous redeclaration here was a leftover from when the
  // script was niche-only.

  // Delete previous sheet if ID file exists. In generic mode (--xlsx
  // without --id-file) idFile is null, so we skip the cleanup — each
  // run creates a fresh sheet. The runner that writes the id_file is
  // responsible for passing the same path on rerun.
  const previousId = (idFile && fs.existsSync(idFile))
    ? fs.readFileSync(idFile, 'utf-8').trim() : null;
  if (previousId) {
    try {
      await drive.files.delete({ fileId: previousId, supportsAllDrives: true });
      console.log(`Deleted previous sheet: ${previousId}`);
    } catch (err) {
      console.warn(`Could not delete previous sheet (${previousId}): ${err.message}`);
    }
  }

  // Strategy: Try xlsx upload first. If quota error, fall back to Sheets API + CSV data.
  try {
    // Attempt 1: Direct xlsx upload with conversion
    const fileMetadata = {
      name: title,
      mimeType: 'application/vnd.google-apps.spreadsheet',
    };
    if (folderId) fileMetadata.parents = [folderId];

    const media = {
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      body: fs.createReadStream(xlsxPath),
    };

    const res = await drive.files.create({
      requestBody: fileMetadata,
      media: media,
      fields: 'id, webViewLink',
      supportsAllDrives: true,
    });

    const sheetId = res.data.id;
    const sheetUrl = res.data.webViewLink;
    if (idFile) fs.writeFileSync(idFile, sheetId);
    console.log(`Uploaded (xlsx conversion): ${title}`);
    console.log(`Sheet ID: ${sheetId}`);
    console.log(`URL: ${sheetUrl}`);
    if (idFile) console.log(`ID saved to: ${idFile}`);
    return;
  } catch (err) {
    const errMsg = err.message || JSON.stringify(err.errors || '');
    if (err.status === 403 || errMsg.includes('quota') || errMsg.includes('storageQuota')) {
      console.log('Xlsx upload failed (storage quota). Falling back to Sheets API...');
    } else {
      throw err;
    }
  }

  // Attempt 2: Upload as a raw xlsx file (not converted to Google Sheet)
  // This uses less quota since it's stored as-is
  console.log('Trying raw xlsx upload (no conversion)...');
  try {
    const rawMetadata = { name: title };
    if (folderId) rawMetadata.parents = [folderId];

    const rawMedia = {
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      body: fs.createReadStream(xlsxPath),
    };

    const rawRes = await drive.files.create({
      requestBody: rawMetadata,
      media: rawMedia,
      fields: 'id, webViewLink',
      supportsAllDrives: true,
    });

    const rawId = rawRes.data.id;
    const rawUrl = rawRes.data.webViewLink;
    if (idFile) fs.writeFileSync(idFile, rawId);
    console.log(`Uploaded (raw xlsx, not converted): ${title}`);
    console.log(`File ID: ${rawId}`);
    console.log(`URL: ${rawUrl}`);
    if (idFile) console.log(`ID saved to: ${idFile}`);
    console.log('Note: File is xlsx format in Drive. Open in Google Sheets to view/edit.');
    return;
  } catch (rawErr) {
    const rawMsg = rawErr.message || '';
    console.log(`Raw upload also failed: ${rawMsg}`);
    console.log('');
  }

  // Attempt 3: Create via Sheets API
  console.log('Trying Sheets API create...');
  try {
  // Read the CSV data (the xlsx was built from this)
  const csvPath = xlsxPath.replace('.xlsx', '.csv');
  let csvFile = csvPath;
  // CSV may be in working/ subfolder
  if (!fs.existsSync(csvFile)) {
    const workingCsv = path.join(path.dirname(csvFile), 'working', path.basename(csvFile));
    if (fs.existsSync(workingCsv)) csvFile = workingCsv;
    else { console.error('CSV not found at', csvPath, 'or', workingCsv); process.exit(1); }
  }

  const csvRaw = fs.readFileSync(csvFile, 'utf-8');
  const csvLines = csvRaw.split('\n').filter(l => l.trim());
  const csvRows = csvLines.map(line => parseCSVLine(line));

  // Create spreadsheet
  const createRes = await sheets.spreadsheets.create({
    requestBody: {
      properties: { title },
      sheets: [
        { properties: { title: 'Results' } },
        { properties: { title: 'Legend' } },
      ],
    },
  });

  const sheetId = createRes.data.spreadsheetId;
  console.log(`Created sheet: ${sheetId}`);

  // Move to the target folder
  try {
    await drive.files.update({
      fileId: sheetId,
      addParents: folderId,
      fields: 'id, parents',
      supportsAllDrives: true,
    });
    console.log(`Moved to folder: ${folderId}`);
  } catch (moveErr) {
    console.warn(`Could not move to folder: ${moveErr.message}`);
  }

  // Write data to Results sheet
  await sheets.spreadsheets.values.update({
    spreadsheetId: sheetId,
    range: 'Results!A1',
    valueInputOption: 'RAW',
    requestBody: { values: csvRows },
  });
  console.log(`Wrote ${csvRows.length} rows to Results sheet`);

  // Write legend data
  const legendData = [
    ['Verdict', 'Meaning'],
    ['YES', 'Composite 7+, all filters pass -- pursue this product'],
    ['MAYBE', 'Composite 5-6, one concern -- review needed'],
    ['MAYBE-ROI', 'ROI below 20% estimated -- may improve with real trade price'],
    ['BRAND APPROACH', '2-3 sellers, weak listing -- contact brand direct'],
    ['BUY THE DIP', 'Price 30%+ below 90-day avg -- recovery pattern detected'],
    ['PRICE EROSION', 'Consistent downward slope -- reject'],
    ['GATED', 'Restricted listing -- flag for ungating decision'],
    ['NO', 'Fails filter, reason stated'],
    ['', ''],
    ['Lane', 'Meaning'],
    ['BALANCED', 'Strong velocity + strong margin -- premium opportunity'],
    ['PROFIT', 'Strong unit profit/ROI, may have lower velocity -- capital efficient'],
    ['CASH FLOW', 'High velocity, acceptable margin -- turnover and cash generation'],
    ['', ''],
    ['Column', 'Explanation'],
    ['Price Compression', 'OK / SQUEEZED (10-20% below 90d avg) / COMPRESSED (20%+ below avg)'],
    ['Est Cost 65%', 'Estimated cost at 65% of selling price (rough placeholder)'],
    ['Max Cost for 20% ROI', 'Maximum you can pay for stock and still hit 20% ROI after FBA fees'],
    ['Price Stability', 'STABLE / SLIGHT DIP / DROPPING / RISING / SURGING'],
    ['Listing Quality', 'STRONG (6+ images, A+, 5+ bullets) / AVERAGE / WEAK'],
    ['Weight Flag', 'OK / HEAVY (>5kg) / OVERSIZE (>45cm) / HEAVY+OVERSIZE'],
    ['Brand 1P', 'Y = brand sells direct on Amazon (hard to compete)'],
  ];

  await sheets.spreadsheets.values.update({
    spreadsheetId: sheetId,
    range: 'Legend!A1',
    valueInputOption: 'RAW',
    requestBody: { values: legendData },
  });

  // Apply basic formatting: freeze header rows, bold headers
  const resultsSheetId = createRes.data.sheets[0].properties.sheetId;
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: sheetId,
    requestBody: {
      requests: [
        // Freeze first row + first 3 columns
        {
          updateSheetProperties: {
            properties: {
              sheetId: resultsSheetId,
              gridProperties: { frozenRowCount: 1, frozenColumnCount: 3 },
            },
            fields: 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount',
          },
        },
        // Bold header row
        {
          repeatCell: {
            range: { sheetId: resultsSheetId, startRowIndex: 0, endRowIndex: 1 },
            cell: { userEnteredFormat: { textFormat: { bold: true } } },
            fields: 'userEnteredFormat.textFormat.bold',
          },
        },
        // Auto-resize columns
        {
          autoResizeDimensions: {
            dimensions: { sheetId: resultsSheetId, dimension: 'COLUMNS', startIndex: 0, endIndex: 55 },
          },
        },
      ],
    },
  });

  const sheetUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/edit`;
  if (idFile) fs.writeFileSync(idFile, sheetId);

  console.log(`Uploaded (Sheets API fallback): ${title}`);
  console.log(`Sheet ID: ${sheetId}`);
  console.log(`URL: ${sheetUrl}`);
  if (idFile) console.log(`ID saved to: ${idFile}`);
  console.log('Note: Styling is basic (Sheets API fallback). For full styling, free up Drive quota and re-run.');
  return;
  } catch (sheetsErr) {
    console.log(`Sheets API also failed: ${sheetsErr.message}`);
  }

  // All attempts failed
  console.log('');
  console.log('=== UPLOAD FAILED ===');
  console.log('The service account has zero Drive storage quota.');
  console.log('This happens with Cloud-only service accounts not in a Google Workspace domain.');
  console.log('');
  console.log('Solutions:');
  console.log('  1. Upload manually: Open Google Drive, drag the xlsx file into the folder');
  console.log('     File: ' + xlsxPath);
  console.log('     Folder: https://drive.google.com/drive/folders/' + folderId);
  console.log('  2. Add the service account to a Google Workspace domain with storage');
  console.log('  3. Use domain-wide delegation from a Workspace account');
  console.log('');
  console.log('The xlsx file is the primary deliverable and works without Google Sheets.');
  process.exit(1);
}

function parseCSVLine(line) {
  const fields = []; let field = ''; let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') { if (inQ && line[i+1] === '"') { field += '"'; i++; } else inQ = !inQ; }
    else if (ch === ',' && !inQ) { fields.push(field); field = ''; }
    else { field += ch; }
  }
  fields.push(field);
  return fields;
}

main().catch(err => { console.error(err); process.exit(1); });
