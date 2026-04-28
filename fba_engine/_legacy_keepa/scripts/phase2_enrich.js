// Phase 2 Enrichment: Merge Keepa pre-filtered data with SellerAmp results
// Applies trim rules, calculates ROI, produces output files

const fs = require('fs');
const path = require('path');

// Resolve project data dir relative to this script (scripts/ -> project root/data)
const DATA_DIR = path.resolve(__dirname, '..', 'data', 'pet-care');
const PREFILTERED = path.join(DATA_DIR, 'pet_care_phase2_prefiltered.csv');
const SAS_RESULTS = path.join(DATA_DIR, 'sas_results_pet_care.json');
const OUTPUT_ENRICHED = path.join(DATA_DIR, 'pet_care_phase2_enriched.csv');
const OUTPUT_GATED = path.join(DATA_DIR, 'pet_care_phase2_gated.csv');
const OUTPUT_HAZMAT = path.join(DATA_DIR, 'pet_care_phase2_hazmat.csv');
const OUTPUT_STATS = path.join(DATA_DIR, 'pet_care_phase2_stats.txt');
const OUTPUT_HANDOFF = path.join(DATA_DIR, 'pet_care_phase2_handoff.md');

// Simple CSV parser
function parseCSV(text) {
  const rows = [];
  let current = '', inQuotes = false, row = [];
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') {
      if (inQuotes && text[i+1] === '"') { current += '"'; i++; }
      else inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) { row.push(current); current = ''; }
    else if ((ch === '\n' || ch === '\r') && !inQuotes) {
      if (ch === '\r' && text[i+1] === '\n') i++;
      row.push(current); current = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else current += ch;
  }
  if (current || row.length > 0) { row.push(current); if (row.length > 1 || row[0] !== '') rows.push(row); }
  return rows;
}

function parseNum(val) {
  if (!val || val === '' || val === '-') return null;
  const cleaned = val.replace(/GBP/gi, '').replace(/[£%,\s+∞]/g, '').trim();
  if (cleaned === '' || cleaned === '-' || cleaned === 'Infinity') return null;
  const n = parseFloat(cleaned);
  return isNaN(n) ? null : n;
}

function parseBought(val) {
  if (!val || val === '' || val === '-') return null;
  const cleaned = val.replace(/[,+\s]/g, '').trim();
  const n = parseInt(cleaned);
  return isNaN(n) ? null : n;
}

function toCSVRow(arr) {
  return arr.map(cell => {
    const s = String(cell || '');
    if (s.includes(',') || s.includes('"') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }).join(',');
}

// Load data
const rawCSV = fs.readFileSync(PREFILTERED, 'utf-8').replace(/^\uFEFF/, '');
const rows = parseCSV(rawCSV);
const headers = rows[0];
const data = rows.slice(1);
const sasData = JSON.parse(fs.readFileSync(SAS_RESULTS, 'utf-8'));

// Column indices
const col = {};
headers.forEach((h, i) => col[h] = i);
const iASIN = col['ASIN'];
const iTitle = col['Title'];
const iBrand = col['Brand'];
const iCategory = col['Categories: Root'];
const iBuyBoxCurrent = col['Buy Box: Current'];
const iBuyBox90Avg = col['Buy Box: 90 days avg.'];
const iBuyBox90Drop = col['Buy Box: 90 days drop %'];
const iBSRCurrent = col['Sales Rank: Current'];
const iBSRDrops90 = col['Sales Rank: Drops last 90 days'];
const iBought = col['Bought in past month'];
const iNewOfferCount = col['New Offer Count: Current'];
const iAmazonCurrent = col['Amazon: Current'];
const iPreFlags = headers.length - 1; // Last column is Pre-filter Flags

// Output columns
const outHeaders = [
  'ASIN', 'Title', 'Brand', 'Amazon URL', 'Category',
  'Current Price (GBP)', 'Buy Box 90-day avg (GBP)', 'Price drop % 90-day',
  'BSR Current', 'BSR Drops last 90 days', 'Bought in past month',
  'New FBA Offer Count', 'Amazon on listing',
  'FBA Fee (GBP)', 'Est Profit (GBP)', 'ROI %', 'Max Cost (GBP)',
  'Breakeven (GBP)', 'Total Fees (GBP)',
  'Gated (Y/N)', 'Hazmat (Y/N)', 'Amazon Buy Box Share',
  'Private Label Risk', 'Sales velocity (SA)',
  'SA FBA Count', 'SA FBM Count', 'SA Total Offers',
  'Pre-filter flags', 'SellerAmp flags', 'Verdict', 'Data source'
];

const enriched = [];
const gated = [];
const hazmat = [];
const stats = {
  input: data.length,
  sa_processed: 0,
  sa_errors: 0,
  hazmat_removed: 0,
  gated_flagged: 0,
  maybe_roi: 0,
  amazon_dominant: 0,
  oversaturated_removed: 0,
  final_count: 0
};

for (const row of data) {
  const asin = row[iASIN];
  if (!asin) continue;

  const sas = sasData[asin];
  if (!sas) { stats.sa_errors++; continue; }
  if (sas.error) { stats.sa_errors++; continue; }
  stats.sa_processed++;

  const currentPrice = parseNum(row[iBuyBoxCurrent]);
  const buyBox90Avg = parseNum(row[iBuyBox90Avg]);
  const priceDrop90 = parseNum(row[iBuyBox90Drop]);
  const bsrCurrent = row[iBSRCurrent] || '';
  const bsrDrops90 = row[iBSRDrops90] || '';
  const bought = row[iBought] || '';
  const fbaOfferCount = parseNum(row[iNewOfferCount]);
  const amazonListed = parseNum(row[iAmazonCurrent]) > 0 ? 'Y' : 'N';
  const preFlags = row[iPreFlags] || '';

  // SellerAmp data
  const salePrice = parseNum(sas.sale_price) || currentPrice || 0;
  const totalFees = parseNum(sas.total_fees) || 0;
  const profit = parseNum(sas.profit) || 0;
  const maxCost = parseNum(sas.max_cost) || 0;
  const breakeven = parseNum(sas.breakeven) || 0;
  const saGated = sas.gated || '';
  const saHazmat = sas.hazmat || '';
  const amazonBB = sas.amazon_buybox || '';
  const privateLbl = sas.private_label || '';
  const estSales = sas.est_sales || '';
  const saFBA = sas.fba_count || '';
  const saFBM = sas.fbm_count || '';
  const saOffers = sas.total_offers || '';

  // Calculate ROI at 65% cost
  const estCost = salePrice * 0.65;
  const estProfit = salePrice - estCost - totalFees;
  const roi = estCost > 0 ? ((estProfit / estCost) * 100).toFixed(1) : '';

  // Apply trim rules
  const saFlags = [];
  let verdict = '';

  // REMOVE: Hazmat confirmed
  if (saHazmat === 'Y') {
    stats.hazmat_removed++;
    hazmat.push([asin, row[iTitle], row[col['Brand']], saHazmat]);
    continue;
  }

  // REMOVE: Oversaturated (SA confirms > 20 FBA sellers)
  const saFBANum = parseInt(saFBA) || 0;
  if (saFBANum < 2) {
    continue;
  }
  if (saFBANum > 20) {
    stats.oversaturated_removed++;
    continue;
  }

  // FLAG: Gated
  if (saGated === 'Y') {
    stats.gated_flagged++;
    saFlags.push('GATED');
    verdict = 'GATED';
  }

  // FLAG: ROI < 20%
  const roiNum = parseFloat(roi) || 0;
  if (roi && roiNum < 20) {
    stats.maybe_roi++;
    saFlags.push('MAYBE-ROI');
    if (!verdict) verdict = 'MAYBE-ROI';
  }

  // FLAG: Amazon dominant
  if (amazonBB && (amazonBB.toLowerCase().includes('probably') || amazonBB.toLowerCase().includes('yes'))) {
    stats.amazon_dominant++;
    saFlags.push('AMAZON DOMINANT');
  }

  // Set verdict if not already set
  if (!verdict) {
    if (roiNum >= 20) verdict = 'YES';
    else if (roiNum >= 20) verdict = 'MAYBE';
    else if (roi) verdict = 'MAYBE-ROI';
    else verdict = 'REVIEW';
  }

  // Check for brand approach (2-3 sellers)
  if (saFBANum >= 2 && saFBANum <= 3 && saGated !== 'Y') {
    if (!saFlags.includes('AMAZON DOMINANT')) {
      verdict = 'BRAND APPROACH';
    }
  }

  const outRow = [
    asin,
    row[iTitle] || '',
    row[col['Brand']] || '',
    'https://www.amazon.co.uk/dp/' + asin,
    row[iCategory] || '',
    salePrice ? salePrice.toFixed(2) : '',
    buyBox90Avg ? buyBox90Avg.toFixed(2) : '',
    priceDrop90 !== null ? priceDrop90 + '%' : '',
    bsrCurrent,
    bsrDrops90,
    bought,
    fbaOfferCount || '',
    amazonListed,
    totalFees ? totalFees.toFixed(2) : '',
    estProfit ? estProfit.toFixed(2) : '',
    roi ? roi + '%' : '',
    maxCost ? maxCost.toFixed(2) : '',
    breakeven ? breakeven.toFixed(2) : '',
    totalFees ? totalFees.toFixed(2) : '',
    saGated,
    saHazmat,
    amazonBB,
    privateLbl,
    estSales,
    saFBA,
    saFBM,
    saOffers,
    preFlags,
    saFlags.join('; '),
    verdict,
    'SA'
  ];

  enriched.push(outRow);

  if (saGated === 'Y') {
    gated.push(outRow);
  }
}

stats.final_count = enriched.length;

// Write enriched CSV
const enrichedCSV = [toCSVRow(outHeaders), ...enriched.map(toCSVRow)].join('\n');
fs.writeFileSync(OUTPUT_ENRICHED, '\uFEFF' + enrichedCSV, 'utf-8');

// Write gated CSV
const gatedCSV = [toCSVRow(outHeaders), ...gated.map(toCSVRow)].join('\n');
fs.writeFileSync(OUTPUT_GATED, '\uFEFF' + gatedCSV, 'utf-8');

// Write hazmat CSV
const hazmatHeaders = ['ASIN', 'Title', 'Brand', 'Hazmat'];
const hazmatCSV = [toCSVRow(hazmatHeaders), ...hazmat.map(toCSVRow)].join('\n');
fs.writeFileSync(OUTPUT_HAZMAT, '\uFEFF' + hazmatCSV, 'utf-8');

// Write stats
const statsText = `Phase 1 input count: 245
After pre-filter: ${stats.input}
Removed at pre-filter:
  Price erosion: 6
  Low velocity: 0
  Oversaturated: 5
SellerAmp processed: ${stats.sa_processed}
SellerAmp errors: ${stats.sa_errors}
After enrichment trim:
  Hazmat removed: ${stats.hazmat_removed}
  Oversaturated removed: ${stats.oversaturated_removed}
  Gated (flagged): ${stats.gated_flagged}
  MAYBE-ROI flagged: ${stats.maybe_roi}
  Amazon dominant flagged: ${stats.amazon_dominant}
Final enriched count: ${stats.final_count}
`;
fs.writeFileSync(OUTPUT_STATS, statsText, 'utf-8');

// Write handoff
const handoff = `# Phase 2 Handoff -- pet-care

Status: COMPLETE
Input: pet_care_phase1_raw.csv (245 products)
Pre-filtered: pet_care_phase2_prefiltered.csv (${stats.input} products)
Output: pet_care_phase2_enriched.csv (${stats.final_count} products)
Gated list: pet_care_phase2_gated.csv (${stats.gated_flagged} products)
Hazmat list: pet_care_phase2_hazmat.csv (${stats.hazmat_removed} products)

## Key flags in the enriched file
PRICE CHECK: may be dipping -- confirm chart in Skill 3
AMAZON CHECK: Amazon on listing -- Buy Box % confirms dominance
LOW SELLER CHECK: only 2 sellers -- check brand quality
MAYBE-ROI: below 20% estimated -- may improve with real trade price
AMAZON DOMINANT: Amazon Buy Box >70% -- scored down in Skill 3
GATED: restricted listing -- flag for ungating decision
BRAND APPROACH: 2-3 sellers, potential brand direct opportunity

## Verdict distribution
${enriched.reduce((acc, row) => {
  const v = row[outHeaders.indexOf('Verdict')];
  acc[v] = (acc[v] || 0) + 1;
  return acc;
}, {}).__proto__ ? Object.entries(enriched.reduce((acc, row) => {
  const v = row[outHeaders.indexOf('Verdict')];
  acc[v] = (acc[v] || 0) + 1;
  return acc;
}, {})).map(([k, v]) => '  ' + k + ': ' + v).join('\n') : 'N/A'}

## Next step
Run Skill 3 -- Scoring and Shortlist
Input: data/pet-care/pet_care_phase2_enriched.csv
`;
fs.writeFileSync(OUTPUT_HANDOFF, handoff, 'utf-8');

console.log(JSON.stringify(stats, null, 2));
console.log('\nVerdict distribution:');
const verdicts = {};
enriched.forEach(row => {
  const v = row[outHeaders.indexOf('Verdict')];
  verdicts[v] = (verdicts[v] || 0) + 1;
});
console.log(JSON.stringify(verdicts, null, 2));
console.log('\nFiles saved successfully.');

