// Phase 2 Pre-filter for pet-care niche
// Reads phase1_raw.csv, applies pre-filter rules, outputs phase2_prefiltered.csv

const fs = require('fs');
const path = require('path');

const NICHE = 'pet-care';
// Resolve project data dir relative to this script (scripts/ -> project root/data)
const DATA_DIR = path.resolve(__dirname, '..', 'data', NICHE);
const INPUT = path.join(DATA_DIR, 'pet_care_phase1_raw.csv');
const OUTPUT = path.join(DATA_DIR, 'pet_care_phase2_prefiltered.csv');

// Simple CSV parser that handles quoted fields
function parseCSV(text) {
  const rows = [];
  let current = '';
  let inQuotes = false;
  let row = [];

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') {
      if (inQuotes && text[i+1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === ',' && !inQuotes) {
      row.push(current);
      current = '';
    } else if ((ch === '\n' || ch === '\r') && !inQuotes) {
      if (ch === '\r' && text[i+1] === '\n') i++;
      row.push(current);
      current = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else {
      current += ch;
    }
  }
  if (current || row.length > 0) {
    row.push(current);
    if (row.length > 1 || row[0] !== '') rows.push(row);
  }
  return rows;
}

// Parse numeric value, stripping % signs and whitespace
function parseNum(val) {
  if (!val || val === '' || val === '-') return null;
  const cleaned = val.replace(/[£%,\s+]/g, '').trim();
  if (cleaned === '' || cleaned === '-') return null;
  const n = parseFloat(cleaned);
  return isNaN(n) ? null : n;
}

// Parse "bought in past month" which may have "+" suffix like "300+"
function parseBought(val) {
  if (!val || val === '' || val === '-') return null;
  const cleaned = val.replace(/[,+\s]/g, '').trim();
  const n = parseInt(cleaned);
  return isNaN(n) ? null : n;
}

const raw = fs.readFileSync(INPUT, 'utf-8').replace(/^\uFEFF/, ''); // strip BOM
const rows = parseCSV(raw);
const headers = rows[0];
const data = rows.slice(1);

// Build column index map
const col = {};
headers.forEach((h, i) => col[h] = i);

// Key column indices
const iASIN = col['ASIN'];
const iTitle = col['Title'];
const iBought = col['Bought in past month'];
const iBuyBoxCurrent = col['Buy Box: Current'];
const iBuyBox90Drop = col['Buy Box: 90 days drop %'];
const iNewOfferCount = col['New Offer Count: Current'];
const iAmazonCurrent = col['Amazon: Current'];
const iBSRCurrent = col['Sales Rank: Current'];
const iBSRDrops90 = col['Sales Rank: Drops last 90 days'];
const iBuyBox90Avg = col['Buy Box: 90 days avg.'];
const iBrand = col['Brand'];
const iCategory = col['Categories: Root'];
const iBuyBoxSeller = col['Buy Box: Buy Box Seller'];
const iBuyBoxAmazonPct = col['Buy Box: % Amazon 90 days'];

const VELOCITY_MIN = 50; // Lowered for pet-care UK

const removed = { price_erosion: [], low_velocity: [], oversaturated: [] };
const kept = [];
const flags = {}; // ASIN -> [flags]

for (const row of data) {
  const asin = row[iASIN];
  if (!asin) continue;

  const bought = parseBought(row[iBought]);
  const buyboxDrop = parseNum(row[iBuyBox90Drop]);
  const offerCount = parseNum(row[iNewOfferCount]);
  const buyboxCurrent = parseNum(row[iBuyBoxCurrent]);
  const amazonCurrent = parseNum(row[iAmazonCurrent]);

  // REMOVE rules
  if (offerCount !== null && offerCount > 20) {
    removed.oversaturated.push(asin);
    continue;
  }
  if (buyboxDrop !== null && buyboxDrop < -20) {
    removed.price_erosion.push(asin);
    continue;
  }
  if (bought !== null && bought < VELOCITY_MIN) {
    removed.low_velocity.push(asin);
    continue;
  }

  // FLAG rules
  const rowFlags = [];

  if (buyboxDrop !== null && buyboxDrop >= -20 && buyboxDrop <= -10) {
    rowFlags.push('PRICE CHECK');
  }
  if (amazonCurrent !== null && amazonCurrent > 0) {
    rowFlags.push('AMAZON CHECK');
  }
  if (offerCount !== null && offerCount === 2) {
    rowFlags.push('LOW SELLER CHECK');
  }
  if (buyboxCurrent !== null && (buyboxCurrent < 20 || buyboxCurrent > 70)) {
    rowFlags.push('PRICE DRIFT');
  }

  flags[asin] = rowFlags;
  kept.push(row);
}

// Add a "Pre-filter Flags" column to the output
const outHeaders = [...headers, 'Pre-filter Flags'];
const outRows = [outHeaders];
for (const row of kept) {
  const asin = row[iASIN];
  const f = flags[asin] || [];
  outRows.push([...row, f.join('; ')]);
}

// Write output CSV
function toCSV(rows) {
  return rows.map(row =>
    row.map(cell => {
      const s = String(cell || '');
      if (s.includes(',') || s.includes('"') || s.includes('\n')) {
        return '"' + s.replace(/"/g, '""') + '"';
      }
      return s;
    }).join(',')
  ).join('\n');
}

fs.writeFileSync(OUTPUT, '\uFEFF' + toCSV(outRows), 'utf-8');

// Summary
const summary = {
  input: data.length,
  removed_price_erosion: removed.price_erosion.length,
  removed_low_velocity: removed.low_velocity.length,
  removed_oversaturated: removed.oversaturated.length,
  total_removed: removed.price_erosion.length + removed.low_velocity.length + removed.oversaturated.length,
  kept: kept.length,
  flagged_price_check: Object.values(flags).filter(f => f.includes('PRICE CHECK')).length,
  flagged_amazon_check: Object.values(flags).filter(f => f.includes('AMAZON CHECK')).length,
  flagged_low_seller: Object.values(flags).filter(f => f.includes('LOW SELLER CHECK')).length,
  flagged_price_drift: Object.values(flags).filter(f => f.includes('PRICE DRIFT')).length,
};

console.log(JSON.stringify(summary, null, 2));
console.log(`\nOutput saved to: ${OUTPUT}`);
