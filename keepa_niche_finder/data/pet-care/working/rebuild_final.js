/**
 * Legacy helper for rebuilding a final file from shortlist data and
 * placeholder supplier data.
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname);
const SHORTLIST = path.join(DATA_DIR, 'pet_care_phase3_shortlist.csv');
const SUPPLIERS = path.join(DATA_DIR, 'pet_care_phase5_suppliers.csv');
const OUTPUT    = path.join(DATA_DIR, 'pet_care_final_results.csv');

// --- CSV parser that handles quoted fields with commas ---
function parseCSVLine(line) {
  const fields = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === ',' && !inQuotes) {
      fields.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  fields.push(current.trim());
  return fields;
}

function loadCSV(filepath) {
  const lines = fs.readFileSync(filepath, 'utf8').split('\n').filter(l => l.trim());
  const headers = parseCSVLine(lines[0]);
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const vals = parseCSVLine(lines[i]);
    const row = {};
    headers.forEach((h, idx) => { row[h] = vals[idx] || ''; });
    rows.push(row);
  }
  return { headers, rows };
}

function escapeCSV(val) {
  if (val == null) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// --- Load both files ---
const shortlist = loadCSV(SHORTLIST);
const suppliers = loadCSV(SUPPLIERS);

// --- Build lookup from shortlist by ASIN ---
const slMap = {};
shortlist.rows.forEach(r => { slMap[r.ASIN] = r; });

// --- Define final output columns (grouped logically) ---
const OUTPUT_HEADERS = [
  // Identity
  'ASIN',
  'Product Name',
  'Brand',
  'Amazon URL',
  'Category',

  // Verdict & Score
  'Verdict',
  'Verdict Reason',
  'Composite Score',
  'Demand Score',
  'Stability Score',
  'Competition Score',
  'Margin Score',

  // Pricing
  'Current Price (GBP)',
  'Buy Box 90-day avg (GBP)',
  'Price Stability',        // derived: drop % 90-day
  'FBA Fee (GBP)',
  'Est Cost 65% (GBP)',
  'Est Profit (GBP)',
  'Est ROI %',
  'Max Cost for 30% ROI (GBP)',
  'Breakeven Price (GBP)',

  // Demand
  'BSR Current',
  'BSR Drops last 90 days',
  'Bought in past month',

  // Competition
  'FBA Seller Count',
  'Amazon on Listing',
  'Amazon Buy Box Share',
  'Private Label Risk',

  // Gating
  'Gated',
  'SellerAmp Flags',

  // Supplier (from Phase 4)
  'Route Code',
  'Supplier Name',
  'Supplier Website',
  'Supplier Contact',
  'MOQ',
  'Trade Price Found',
  'Trade Price (GBP)',
  'Real ROI %',
  'Supplier Notes',
  'Outreach Email File',
];

// --- Merge and build output ---
const outputRows = [];

suppliers.rows.forEach(sup => {
  const asin = sup.ASIN;
  const sl = slMap[asin] || {};

  // Price stability label
  const dropPct = sl['Price drop % 90-day'] || '0 %';
  const dropNum = parseFloat(dropPct) || 0;
  let priceStability;
  if (Math.abs(dropNum) <= 2) priceStability = 'STABLE';
  else if (dropNum > 0 && dropNum <= 10) priceStability = 'SLIGHT DIP (' + dropPct.trim() + ')';
  else if (dropNum > 10) priceStability = 'DROPPING (' + dropPct.trim() + ')';
  else if (dropNum < -2 && dropNum >= -10) priceStability = 'RISING (' + dropPct.trim() + ')';
  else priceStability = 'SURGING (' + dropPct.trim() + ')';

  const row = {
    'ASIN': asin,
    'Product Name': sup['Product Name'] || sl['Title'] || '',
    'Brand': sup['Brand'] || sl['Brand'] || '',
    'Amazon URL': sl['Amazon URL'] || 'https://www.amazon.co.uk/dp/' + asin,
    'Category': sup['Category'] || sl['Category'] || '',

    'Verdict': sup['Verdict'] || '',
    'Verdict Reason': sl['Verdict Reason'] || '',
    'Composite Score': sup['Composite Score'] || sl['Composite Score'] || '',
    'Demand Score': sl['Demand Score'] || '',
    'Stability Score': sl['Stability Score'] || '',
    'Competition Score': sl['Competition Score'] || '',
    'Margin Score': sl['Margin Score'] || '',

    'Current Price (GBP)': sup['Current Price (GBP)'] || sl['Current Price (GBP)'] || '',
    'Buy Box 90-day avg (GBP)': sl['Buy Box 90-day avg (GBP)'] || '',
    'Price Stability': priceStability,
    'FBA Fee (GBP)': sup['FBA Fee (GBP)'] || sl['Total Fees (GBP)'] || '',
    'Est Cost 65% (GBP)': sl['Est Cost 65% (GBP)'] || '',
    'Est Profit (GBP)': sl['Est Profit (GBP)'] || '',
    'Est ROI %': sup['Est ROI %'] || sl['ROI %'] || '',
    'Max Cost for 30% ROI (GBP)': sl['Max Cost (GBP)'] || '',
    'Breakeven Price (GBP)': sl['Breakeven (GBP)'] || '',

    'BSR Current': sl['BSR Current'] || '',
    'BSR Drops last 90 days': sl['BSR Drops last 90 days'] || '',
    'Bought in past month': sl['Bought in past month'] || '',

    'FBA Seller Count': sl['New FBA Offer Count'] || '',
    'Amazon on Listing': sl['Amazon on listing'] || '',
    'Amazon Buy Box Share': sl['Amazon Buy Box Share'] || '',
    'Private Label Risk': sl['Private Label Risk'] || '',

    'Gated': sl['Gated (Y/N)'] || '',
    'SellerAmp Flags': sl['SellerAmp flags'] || '',

    'Route Code': sup['Route Code'] || '',
    'Supplier Name': sup['Supplier Name'] || '',
    'Supplier Website': sup['Supplier Website'] || '',
    'Supplier Contact': sup['Supplier Contact'] || '',
    'MOQ': sup['MOQ'] || '',
    'Trade Price Found': sup['Trade Price Found'] || '',
    'Trade Price (GBP)': sup['Trade Price (GBP)'] || '',
    'Real ROI %': sup['Real ROI %'] || '',
    'Supplier Notes': sup['Notes'] || '',
    'Outreach Email File': sup['Outreach Email File'] || '',
  };

  outputRows.push(row);
});

// --- Sort by Composite Score descending ---
outputRows.sort((a, b) => {
  const sa = parseFloat(a['Composite Score']) || 0;
  const sb = parseFloat(b['Composite Score']) || 0;
  return sb - sa;
});

// --- Write output ---
const csvLines = [OUTPUT_HEADERS.map(escapeCSV).join(',')];
outputRows.forEach(row => {
  csvLines.push(OUTPUT_HEADERS.map(h => escapeCSV(row[h])).join(','));
});

fs.writeFileSync(OUTPUT, csvLines.join('\n'), 'utf8');

console.log(`✅ Final results file built: ${OUTPUT}`);
console.log(`   Rows: ${outputRows.length}`);
console.log(`   Columns: ${OUTPUT_HEADERS.length}`);
console.log(`\n   Column groups:`);
console.log(`     Identity:    5 cols (ASIN, Name, Brand, URL, Category)`);
console.log(`     Verdict:     6 cols (Verdict, Reason, Scores)`);
console.log(`     Pricing:     8 cols (Price, Fees, ROI, Max Cost)`);
console.log(`     Demand:      3 cols (BSR, Drops, Monthly Sales)`);
console.log(`     Competition:  4 cols (Sellers, Amazon, Buy Box, PL Risk)`);
console.log(`     Gating:      2 cols (Gated, Flags)`);
console.log(`     Supplier:   10 cols (Route, Name, Contact, Trade Price)`);
