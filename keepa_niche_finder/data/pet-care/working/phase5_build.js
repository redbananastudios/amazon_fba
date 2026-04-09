#!/usr/bin/env node
/**
 * Phase 5 build for pet-care
 * Transforms shortlist into final_results.csv with 64 columns.
 */
const fs = require('fs');
const path = require('path');

const BASE = path.resolve(__dirname, '..');
const WORKING = path.join(BASE, 'working');
const INPUT = fs.existsSync(path.join(WORKING, 'pet_care_phase4_ip_risk.csv'))
  ? path.join(WORKING, 'pet_care_phase4_ip_risk.csv')
  : fs.existsSync(path.join(WORKING, 'pet_care_phase3_shortlist.csv'))
    ? path.join(WORKING, 'pet_care_phase3_shortlist.csv')
    : path.join(BASE, 'pet_care_phase3_shortlist.csv');
const REJECT_CSV = path.join(WORKING, 'pet_care_phase5_rejected_private_label.csv');

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

function esc(val) {
  if (val === null || val === undefined) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function parseCSV(text) {
  const rows = [];
  let current = '';
  let inQuotes = false;
  const rawLines = text.split('\n');

  for (const line of rawLines) {
    if (!line.trim() && !inQuotes) continue;
    current += (current ? '\n' : '') + line;
    const quoteCount = (current.match(/"/g) || []).length;
    inQuotes = quoteCount % 2 !== 0;
    if (!inQuotes) {
      rows.push(parseCSVLine(current));
      current = '';
    }
  }
  return rows;
}

const raw = fs.readFileSync(INPUT, 'utf-8').replace(/^\uFEFF/, '');
const lines = parseCSV(raw);
const headers = lines[0];
const col = {}; headers.forEach((h, i) => { col[h] = i; });

function num(row, name) {
  const idx = col[name];
  if (idx === undefined) return 0;
  const v = (row[idx] || '').replace(/GBP/gi, '').replace(/[^0-9.-]/g, '').trim();
  return parseFloat(v) || 0;
}
function str(row, name) {
  const idx = col[name];
  if (idx === undefined) return '';
  return (row[idx] || '').trim();
}

const OUT_HEADERS = [
  'ASIN', 'Product Name', 'Brand', 'Amazon URL', 'Category', 'Weight Flag',
  'Verdict', 'Verdict Reason', 'Opportunity Lane', 'Commercial Priority', 'Lane Reason',
  'Composite Score', 'Demand Score', 'Stability Score', 'Competition Score', 'Margin Score',
  'Cash Flow Score', 'Profit Score', 'Balanced Score',
  'Monthly Gross Profit', 'Price Compression', 'Listing Quality',
  'Current Price', 'Buy Box 90d Avg', 'Price Stability',
  'Fulfilment Fee', 'Amazon Fees', 'Total Amazon Fees', 'Est Cost 65%', 'Est Profit', 'Est ROI %',
  'Max Cost 20% ROI', 'Breakeven Price',
  'BSR Current', 'BSR Drops 90d', 'Bought per Month',
  'Star Rating', 'Review Count', 'Brand 1P',
  'FBA Seller Count', 'Amazon on Listing', 'Amazon Buy Box Share', 'Private Label Risk',
  'Brand Seller Match', 'Fortress Listing', 'Brand Type', 'A+ Content Present', 'Brand Store Present',
  'Category Risk Level', 'IP Risk Score', 'IP Risk Band', 'IP Reason',
  'Gated', 'SAS Flags',
  'Route Code', 'Supplier Name', 'Supplier Website', 'Supplier Contact',
  'MOQ', 'Trade Price Found', 'Trade Price', 'Real ROI %',
  'Supplier Notes', 'Outreach Email File',
  'EAN', 'UPC', 'GTIN'
];

function priceStability(dropPct) {
  if (dropPct >= -2 && dropPct <= 2) return 'STABLE';
  if (dropPct > 2 && dropPct <= 10) return `SLIGHT DIP (${dropPct.toFixed(0)}%)`;
  if (dropPct > 10) return `DROPPING (${dropPct.toFixed(0)}%)`;
  if (dropPct < -2 && dropPct >= -10) return `RISING (${Math.abs(dropPct).toFixed(0)}%)`;
  return `SURGING (${Math.abs(dropPct).toFixed(0)}%)`;
}

function plRisk(fbaCount, brand1P) {
  if (brand1P === 'Y') return 'Likely';
  if (fbaCount <= 2) return 'Unlikely';
  return '-';
}

function confirmedPrivateLabelStatus(brand1P, brandSellerMatch, fortressListing, brandStorePresent, brandType) {
  const reasons = [];
  const sellerMatch = (brandSellerMatch || '').toUpperCase();
  const fortress = (fortressListing || '').toUpperCase() === 'YES';
  const storeLikely = (brandStorePresent || '').toUpperCase() === 'LIKELY';
  const established = (brandType || '').toUpperCase() === 'ESTABLISHED';
  const amazonBrand = (brand1P || '').toUpperCase() === 'Y';

  if (amazonBrand) reasons.push('Brand 1P');
  if (fortress) reasons.push('Fortress listing');
  if (sellerMatch === 'YES') reasons.push('Brand seller match');
  if (storeLikely) reasons.push('Brand store likely');
  if (established) reasons.push('Established brand');

  const strongControl = fortress && (sellerMatch === 'YES' || sellerMatch === 'PARTIAL');
  const brandOwned = sellerMatch === 'YES' && storeLikely;
  const multiSignal = reasons.length >= 3 && (sellerMatch === 'YES' || fortress || storeLikely);

  return {
    confirmed: amazonBrand || strongControl || brandOwned || multiSignal,
    reason: reasons.join(' | ')
  };
}

const verdictOrder = { 'YES': 1, 'MAYBE': 2, 'BRAND APPROACH': 3, 'BUY THE DIP': 4, 'MAYBE-ROI': 5, 'GATED': 6 };
const finalRows = [];
const supplierRows = [];
const rejectedRows = [];

for (let i = 1; i < lines.length; i++) {
  const row = lines[i];

  const asin = str(row, 'ASIN');
  const title = str(row, 'Title');
  const brand = str(row, 'Brand');
  const amazonUrl = str(row, 'Amazon URL');
  const category = str(row, 'Category');
  const weightFlag = str(row, 'Weight Flag');
  const verdict = str(row, 'Verdict');
  const verdictReason = str(row, 'Verdict Reason');
  const composite = num(row, 'Composite Score');
  const demand = num(row, 'Demand Score');
  const stability = num(row, 'Stability Score');
  const competition = num(row, 'Competition Score');
  const margin = num(row, 'Margin Score');
  const listingQuality = str(row, 'Listing Quality');
  const cashFlowScore = num(row, 'Cash Flow Score');
  const profitScore = num(row, 'Profit Score');
  const balancedScore = num(row, 'Balanced Score');
  const lane = str(row, 'Opportunity Lane');
  const commercialPriority = num(row, 'Commercial Priority');
  const monthlyGrossProfit = num(row, 'Monthly Gross Profit');
  const laneReason = str(row, 'Lane Reason');
  const priceCompression = str(row, 'Price Compression');
  const price = num(row, 'Current Price');
  const bb90Avg = num(row, 'Buy Box 90d Avg');
  const priceDrop = num(row, 'Price Drop % 90d');
  const fbaFee = num(row, 'Fulfilment Fee');
  const amazonFees = num(row, 'Amazon Fees');
  const totalFees = num(row, 'Total Amazon Fees');
  const estCost = num(row, 'Est Cost 65%');
  const estProfit = num(row, 'Est Profit');
  const estROI = num(row, 'Est ROI %');
  const maxCost = num(row, 'Max Cost 20% ROI');
  const breakeven = num(row, 'Breakeven Price');
  const bsr = num(row, 'BSR Current');
  const bsrDrops = num(row, 'BSR Drops 90d');
  const bought = num(row, 'Bought per Month');
  const starRating = num(row, 'Star Rating');
  const reviewCount = num(row, 'Review Count');
  const brand1P = str(row, 'Brand 1P');
  const fbaCount = num(row, 'FBA Seller Count');
  const amazonOnListing = str(row, 'Amazon on Listing');
  const bbAmazonPct = str(row, 'Buy Box Amazon %');
  const brandSellerMatch = str(row, 'Brand Seller Match');
  const fortressListing = str(row, 'Fortress Listing');
  const brandType = str(row, 'Brand Type');
  const aplusPresent = str(row, 'A+ Content Present');
  const brandStorePresent = str(row, 'Brand Store Present');
  const categoryRiskLevel = str(row, 'Category Risk Level');
  const ipRiskScore = str(row, 'IP Risk Score');
  const ipRiskBand = str(row, 'IP Risk Band');
  const ipReason = str(row, 'IP Reason');
  const gated = str(row, 'Gated');
  const sasFlags = str(row, 'SAS Flags');

  const priceStab = priceStability(priceDrop);
  const plr = plRisk(fbaCount, brand1P);
  const plStatus = confirmedPrivateLabelStatus(brand1P, brandSellerMatch, fortressListing, brandStorePresent, brandType);

  const outRow = [
    asin, title, brand, amazonUrl, category, weightFlag,
    verdict, verdictReason, lane, commercialPriority, laneReason,
    composite.toFixed(1), demand, stability, competition, margin,
    cashFlowScore, profitScore, balancedScore,
    'GBP' + monthlyGrossProfit.toFixed(0), priceCompression, listingQuality,
    'GBP' + price.toFixed(2), 'GBP' + bb90Avg.toFixed(2), priceStab,
    'GBP' + fbaFee.toFixed(2), 'GBP' + amazonFees.toFixed(2), 'GBP' + totalFees.toFixed(2), 'GBP' + estCost.toFixed(2), 'GBP' + estProfit.toFixed(2),
    estROI.toFixed(1) + '%',
    'GBP' + maxCost.toFixed(2), 'GBP' + breakeven.toFixed(2),
    Math.round(bsr), bsrDrops, bought,
    starRating.toFixed(1), Math.round(reviewCount), brand1P,
    fbaCount, amazonOnListing, bbAmazonPct, plr,
    brandSellerMatch, fortressListing, brandType, aplusPresent, brandStorePresent,
    categoryRiskLevel, ipRiskScore, ipRiskBand, ipReason,
    gated, sasFlags,
    'UNCLEAR', '', '', '', '', 'N', '', '', 'No supplier accounts configured', '',
    str(row, 'EAN'), str(row, 'UPC'), str(row, 'GTIN')
  ];
  if (plStatus.confirmed) {
    rejectedRows.push([...outRow, plStatus.reason || 'Confirmed private label']);
    continue;
  }
  finalRows.push(outRow);

  supplierRows.push([
    asin, title, brand, category, verdict, composite.toFixed(1),
    'GBP' + price.toFixed(2), 'GBP' + fbaFee.toFixed(2), estROI.toFixed(1) + '%',
    'N', '', 'N', '', '', 'UNKNOWN',
    'UNCLEAR', '', '', '', '', 'No supplier accounts configured', ''
  ]);
}

// Sort: Commercial Priority -> Monthly Gross Profit desc -> Bought desc -> Profit desc -> Composite desc -> Verdict
finalRows.sort((a, b) => {
  const cpA = parseFloat(a[9]) || 99;
  const cpB = parseFloat(b[9]) || 99;
  if (cpA !== cpB) return cpA - cpB;

  const mgpA = parseFloat((a[19] || '').replace(/[^\d.-]/g, '')) || 0;
  const mgpB = parseFloat((b[19] || '').replace(/[^\d.-]/g, '')) || 0;
  if (mgpA !== mgpB) return mgpB - mgpA;

  const boughtA = parseFloat(a[35]) || 0;
  const boughtB = parseFloat(b[35]) || 0;
  if (boughtA !== boughtB) return boughtB - boughtA;

  const profitA = parseFloat((a[29] || '').replace(/[^\d.-]/g, '')) || 0;
  const profitB = parseFloat((b[29] || '').replace(/[^\d.-]/g, '')) || 0;
  if (profitA !== profitB) return profitB - profitA;

  const compA = parseFloat(a[11]) || 0;
  const compB = parseFloat(b[11]) || 0;
  if (compA !== compB) return compB - compA;

  const vA = verdictOrder[a[6]] || 99;
  const vB = verdictOrder[b[6]] || 99;
  return vA - vB;
});

const finalCSV = [OUT_HEADERS.map(esc).join(',')];
finalRows.forEach(r => finalCSV.push(r.map(esc).join(',')));
fs.writeFileSync(path.join(WORKING, 'pet_care_final_results.csv'), finalCSV.join('\n') + '\n');
fs.writeFileSync(path.join(BASE, 'pet_care_final_results.csv'), finalCSV.join('\n') + '\n');

const supHeaders = [
  'ASIN', 'Product Name', 'Brand', 'Category', 'Verdict', 'Composite Score',
  'Current Price', 'FBA Fee', 'Est ROI %',
  'Existing Account Found', 'Existing Account Name',
  'Trade Price Found', 'Trade Price', 'Real ROI %', 'ROI Change',
  'Route Code', 'Supplier Name', 'Supplier Website', 'Supplier Contact',
  'MOQ', 'Notes', 'Outreach Email File'
];
const supCSV = [supHeaders.map(esc).join(',')];
supplierRows.forEach(r => supCSV.push(r.map(esc).join(',')));
fs.writeFileSync(path.join(WORKING, 'pet_care_phase5_suppliers.csv'), supCSV.join('\n') + '\n');

const rejectHeaders = [...OUT_HEADERS, 'Private Label Exclusion Reason'];
const rejectCsv = [rejectHeaders.map(esc).join(',')];
rejectedRows.forEach(r => rejectCsv.push(r.map(esc).join(',')));
fs.writeFileSync(REJECT_CSV, rejectCsv.join('\n') + '\n');

const laneBreakdown = {};
finalRows.forEach(r => { const l = r[8] || 'Unclassified'; laneBreakdown[l] = (laneBreakdown[l] || 0) + 1; });
const verdictBreakdown = {};
finalRows.forEach(r => { const v = r[6] || 'UNKNOWN'; verdictBreakdown[v] = (verdictBreakdown[v] || 0) + 1; });

// EAN coverage
let eanCount = 0;
finalRows.forEach(r => { if (r[62] && r[62].trim()) eanCount++; });

const statsContent = `Niche: pet-care
Date: ${new Date().toISOString().slice(0, 10)}
Phase 5 Build Output
Products in final CSV: ${finalRows.length}
Confirmed private label excluded: ${rejectedRows.length}
Columns: ${OUT_HEADERS.length}

Lane breakdown:
${Object.entries(laneBreakdown).map(([k, v]) => `  ${k}: ${v}`).join('\n')}

Verdict breakdown:
${Object.entries(verdictBreakdown).map(([k, v]) => `  ${k}: ${v}`).join('\n')}

EAN coverage: ${eanCount}/${finalRows.length} (${finalRows.length > 0 ? Math.round(eanCount/finalRows.length*100) : 0}%)

Supplier columns: empty (run skill-99-find-suppliers to populate)
Rejected private label CSV: ${REJECT_CSV}
`;
fs.writeFileSync(path.join(WORKING, 'pet_care_phase5_stats.txt'), statsContent);

const handoff = `# Phase 5 Handoff -- pet-care

Status: BUILD COMPLETE
Products in final output: ${finalRows.length}
Columns: ${OUT_HEADERS.length}

## What was built
- pet_care_final_results.csv (${OUT_HEADERS.length}-column output, sorted by Commercial Priority)
- pet_care_phase5_suppliers.csv (skeleton -- supplier columns empty)
- pet_care_phase5_rejected_private_label.csv (confirmed PL excluded from final file)
- pet_care_phase5_stats.txt

## Next steps
1. Build XLSX: node skills/skill-5-build-output/build_final_xlsx.js --niche pet-care
2. Upload to Google Sheets: node skills/skill-5-build-output/push_to_gsheets.js --niche pet-care
3. (Optional) Run skill-99-find-suppliers to populate supplier columns, then rebuild
`;
fs.writeFileSync(path.join(WORKING, 'pet_care_phase5_handoff.md'), handoff);

console.log('Phase 5 build complete:');
console.log(`  Final results CSV: ${finalRows.length} products, ${OUT_HEADERS.length} columns`);
console.log(`  Private label rejected: ${rejectedRows.length}`);
console.log(`  Supplier CSV: ${supplierRows.length} products (skeleton)`);
console.log(`  EAN coverage: ${eanCount}/${finalRows.length}`);
Object.entries(laneBreakdown).forEach(([k, v]) => console.log(`  Lane ${k}: ${v}`));
Object.entries(verdictBreakdown).forEach(([k, v]) => console.log(`  Verdict ${k}: ${v}`));
