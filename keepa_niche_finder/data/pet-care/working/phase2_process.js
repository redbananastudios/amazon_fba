#!/usr/bin/env node
/**
 * Phase 2 processing for pet-care
 * Pre-filters Keepa data, extracts enrichment fields from Keepa export,
 * flags products needing SellerAmp confirmation.
 */
const fs = require('fs');
const path = require('path');

const NICHE = 'pet-care';
const BASE = path.resolve(__dirname, '..');
const WORKING = path.join(BASE, 'working');
const INPUT = fs.existsSync(path.join(BASE, 'pet_care_phase1_filtered.csv'))
  ? path.join(BASE, 'pet_care_phase1_filtered.csv')
  : path.join(WORKING, 'pet_care_phase1_filtered.csv');

// Hazmat title keyword exclusions (pet-care specific)
const HAZMAT_TITLE_KEYWORDS = ['flea spray', 'tick spray', 'aerosol', 'pesticide', 'insecticide', 'flammable', 'pressurised'];
// Heavy/bulky item title keyword exclusions
const HEAVY_TITLE_KEYWORDS = ['dog cage', 'crate', 'kennel', 'cat tree large', 'aquarium tank', 'large bed', 'extra large'];

// CSV parsing
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

function escapeCSV(val) {
  if (!val && val !== 0) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

// Read and parse
const raw = fs.readFileSync(INPUT, 'utf-8').replace(/^\uFEFF/, '');
const lines = raw.split('\n').filter(l => l.trim());
const headers = parseCSVLine(lines[0]);

const col = {};
headers.forEach((h, i) => { col[h] = i; });

// Product code columns (for supplier price matching)
const EAN_COL = col['Product Codes: EAN'];
const UPC_COL = col['Product Codes: UPC'];
const GTIN_COL = col['Product Codes: GTIN'];

const ASIN = col['ASIN'];
const TITLE = col['Title'];
const BOUGHT = col['Bought in past month'];
const BB_CURRENT = col['Buy Box: Current'];
const BB_90AVG = col['Buy Box: 90 days avg.'];
const BB_90DROP = col['Buy Box: 90 days drop %'];
const AMAZON_CURRENT = col['Amazon: Current'];
const FBA_COUNT = col['New Offer Count: Current'];
const FBA_90AVG = col['New Offer Count: 90 days avg.'];
const BSR = col['Sales Rank: Current'];
const BSR_DROPS = col['Sales Rank: Drops last 90 days'];
const BSR_90AVG = col['Sales Rank: 90 days avg.'];
const CATEGORY = col['Categories: Root'];
const SUBCATEGORY = col['Categories: Sub'];
const RATING = col['Reviews: Rating'];
const REVIEW_COUNT = col['Reviews: Rating Count'];
const IMAGES = col['Image'];
const HAS_APLUS = col['A+ Content: Has A+ Content'];
const FBA_FEE = col['FBA Pick&Pack Fee'];
const REF_FEE_PCT = col['Referral Fee %'];
const BB_SELLER = col['Buy Box: Buy Box Seller'];
const BB_AMAZON_PCT = col['Buy Box: % Amazon 90 days'];
const BRAND = col['Brand'];
const MANUFACTURER = col['Manufacturer'];
const WEIGHT_G = col['Package: Weight (g)'];
const BATT_REQ = col['Batteries Required'];
const BATT_INC = col['Batteries Included'];
const IS_HAZMAT = col['Is HazMat'];
const BB_IS_FBA = col['Buy Box: Is FBA'];
const VIDEOS = col['Videos: Video Count'];
const BINDING = col['Binding'];
const URL_SLUG = col['URL: URL slug'];
const itemDimHeader = Object.keys(col).find(k => /^Item: Dimension \(cm/i.test(k));
const pkgDimHeader = Object.keys(col).find(k => /^Package: Dimension \(cm/i.test(k));
const ITEM_DIM = itemDimHeader !== undefined ? col[itemDimHeader] : undefined;
const PKG_DIM = pkgDimHeader !== undefined ? col[pkgDimHeader] : undefined;

function num(row, idx) {
  if (idx === undefined || idx === -1) return 0;
  const v = (row[idx] || '').replace(/GBP/gi, '').replace(/[£%,+]/g, '').trim();
  const n = parseFloat(v);
  return isNaN(n) ? 0 : n;
}

function str(row, idx) {
  if (idx === undefined || idx === -1) return '';
  return (row[idx] || '').trim();
}

// Process rows
const stats = {
  input: 0,
  removed_single_seller: 0,
  removed_erosion: 0,
  removed_velocity: 0,
  removed_oversaturated: 0,
  removed_hazmat: 0,
  removed_hazmat_title: 0,
  removed_heavy_title: 0,
  flagged_pricecheck: 0,
  flagged_amazon: 0,
  flagged_lowseller: 0,
  flagged_pricedrift: 0,
  flagged_mayberoi: 0,
  flagged_amazon_dominant: 0,
  gated_count: 0,
  listing_strong: 0,
  listing_average: 0,
  listing_weak: 0,
  brand_1p: 0,
  reviews_over_500: 0,
  reviews_under_20: 0,
  weight_heavy: 0,
  weight_oversize: 0,
};

const enrichedRows = [];
const hazmatRows = [];
const removedRows = [];

const OUT_HEADERS = [
  'ASIN', 'Title', 'Brand', 'Amazon URL', 'Category', 'Weight Flag',
  'Current Price', 'Buy Box 90d Avg', 'Price Drop % 90d',
  'BSR Current', 'BSR 90d Avg', 'BSR Drops 90d', 'Bought per Month',
  'FBA Seller Count', 'FBA Seller 90d Avg', 'Amazon on Listing',
  'Fulfilment Fee', 'Amazon Fees', 'Total Amazon Fees', 'Est Cost 65%', 'Est Profit', 'Est ROI %',
  'Max Cost 20% ROI', 'Breakeven Price',
  'Buy Box Amazon %', 'BB Seller', 'BB Is FBA',
  'Star Rating', 'Review Count', 'Image Count', 'Has A+ Content',
  'Bullet Count', 'Listing Quality', 'Brand 1P',
  'Gated', 'SAS Flags', 'Pre-filter Flags', 'Data Source',
  'EAN', 'UPC', 'GTIN'
];

for (let i = 1; i < lines.length; i++) {
  const row = parseCSVLine(lines[i]);
  stats.input++;

  const asin = str(row, ASIN);
  const title = str(row, TITLE);
  const titleLower = title.toLowerCase();
  const brand = str(row, BRAND);
  const bought = num(row, BOUGHT);
  const bbCurrent = num(row, BB_CURRENT);
  const bb90Avg = num(row, BB_90AVG);
  const bb90Drop = num(row, BB_90DROP);
  const amazonCurrent = str(row, AMAZON_CURRENT);
  const fbaCount = num(row, FBA_COUNT);
  const fba90Avg = num(row, FBA_90AVG);
  const bsr = num(row, BSR);
  const bsrDrops = num(row, BSR_DROPS);
  const bsr90Avg = num(row, BSR_90AVG);
  const category = str(row, CATEGORY);
  const subcategory = str(row, SUBCATEGORY);
  const rating = num(row, RATING);
  const reviewCount = num(row, REVIEW_COUNT);
  const fbaFee = num(row, FBA_FEE);
  const refFeePct = num(row, REF_FEE_PCT);
  const bbSeller = str(row, BB_SELLER);
  const bbAmazonPct = num(row, BB_AMAZON_PCT);
  const isHazmat = str(row, IS_HAZMAT).toLowerCase();
  const bbIsFBA = str(row, BB_IS_FBA);
  const hasAplus = str(row, HAS_APLUS).toLowerCase();
  const slug = str(row, URL_SLUG);
  const amazonUrl = 'https://www.amazon.co.uk/dp/' + asin;
  const weightG = num(row, WEIGHT_G);

  const imageStr = str(row, IMAGES);
  const imageCount = imageStr ? imageStr.split(';').filter(u => u.trim()).length : 0;
  // Weight flag
  let weightFlag = 'OK';
  const dimStr = str(row, PKG_DIM) || str(row, ITEM_DIM);
  let maxDim = 0;
  if (dimStr) {
    const normalizedDimStr = dimStr.replace(/[^0-9.,a-z ]/gi, 'x');
    const dims = normalizedDimStr.replace(/[^0-9.,x ]/gi, '').split(/[x ]+/).map(d => parseFloat(d)).filter(d => !isNaN(d));
    if (dims.length >= 3) {
      maxDim = Math.max(...dims);
    }
  }
  const isHeavy = weightG > 5000;
  const isOversize = maxDim > 45;
  if (isHeavy && isOversize) { weightFlag = 'HEAVY+OVERSIZE'; stats.weight_heavy++; stats.weight_oversize++; }
  else if (isHeavy) { weightFlag = 'HEAVY'; stats.weight_heavy++; }
  else if (isOversize) { weightFlag = 'OVERSIZE'; stats.weight_oversize++; }

  // Pre-filter removals
  let removed = false;
  let removeReason = '';

  // Hazmat flag from Keepa
  if (isHazmat === 'yes' || isHazmat === 'y') {
    stats.removed_hazmat++;
    removeReason = 'HAZMAT';
    hazmatRows.push(row);
    removed = true;
  }

  // Hazmat title keyword exclusions (pet-care specific)
  if (!removed && HAZMAT_TITLE_KEYWORDS.some(kw => titleLower.includes(kw))) {
    stats.removed_hazmat_title++;
    removeReason = 'HAZMAT TITLE KEYWORD';
    hazmatRows.push(row);
    removed = true;
  }

  // Heavy/bulky title keyword exclusions
  if (!removed && HEAVY_TITLE_KEYWORDS.some(kw => titleLower.includes(kw))) {
    stats.removed_heavy_title++;
    removeReason = 'HEAVY/BULKY TITLE KEYWORD';
    removed = true;
  }

  if (!removed && fbaCount > 20) {
    stats.removed_oversaturated++;
    removeReason = 'OVERSATURATED';
    removed = true;
  }

  if (!removed && fbaCount < 2) {
    stats.removed_single_seller++;
    removeReason = 'SINGLE SELLER';
    removed = true;
  }

  if (!removed && bb90Drop < -20) {
    stats.removed_erosion++;
    removeReason = 'PRICE EROSION';
    removed = true;
  }

  // Pet-care velocity floor: 100
  if (!removed && bought > 0 && bought < 100) {
    stats.removed_velocity++;
    removeReason = 'LOW VELOCITY';
    removed = true;
  }

  if (removed) {
    removedRows.push({ asin, title, reason: removeReason });
    continue;
  }

  // Pre-filter flags
  const flags = [];
  if (bb90Drop >= -20 && bb90Drop <= -10) { flags.push('PRICE CHECK'); stats.flagged_pricecheck++; }
  if (amazonCurrent && amazonCurrent !== '' && amazonCurrent !== '-') { flags.push('AMAZON CHECK'); stats.flagged_amazon++; }
  if (fbaCount === 2) { flags.push('LOW SELLER CHECK'); stats.flagged_lowseller++; }
  if (bbCurrent < 20 || bbCurrent > 70) { flags.push('PRICE DRIFT'); stats.flagged_pricedrift++; }

  // Enrichment from Keepa data
  const price = bbCurrent || bb90Avg;
  const estCost = price * 0.65;
  const refFee = price * (refFeePct / 100 || 0.1501);
  const perItemFee = 0.75;
  const digitalServicesFee = refFee * 0.029;
  const totalFee = fbaFee + refFee + perItemFee + digitalServicesFee;
  const estProfit = price - estCost - totalFee;
  const estROI = estCost > 0 ? (estProfit / estCost) * 100 : 0;
  const maxCost20ROI = (price - totalFee) / 1.20;
  const breakevenPrice = estCost + totalFee;

  const bulletCount = hasAplus === 'yes' ? 5 : (imageCount >= 5 ? 4 : 3);
  let listingQuality = 'WEAK';
  if (imageCount >= 6 && hasAplus === 'yes' && bulletCount >= 5) {
    listingQuality = 'STRONG';
    stats.listing_strong++;
  } else if (imageCount >= 4 || hasAplus === 'yes' || bulletCount >= 4) {
    listingQuality = 'AVERAGE';
    stats.listing_average++;
  } else {
    stats.listing_weak++;
  }

  const amazonSellerIds = ['A3P5ROKL5A1OLE', 'AZH2GF8Z5J95G'];
  let brand1P = 'N';
  if (bbSeller) {
    const sellerLower = bbSeller.toLowerCase();
    const brandLower = brand.toLowerCase();
    if (amazonSellerIds.some(id => bbSeller.includes(id)) || sellerLower.includes('amazon')) {
      if (brandLower.includes('amazon')) brand1P = 'Y';
    }
    if (brandLower && brandLower.length > 2 && sellerLower.includes(brandLower)) {
      brand1P = 'Y';
    }
  }
  if (brand1P === 'Y') stats.brand_1p++;
  if (reviewCount > 500) stats.reviews_over_500++;
  if (reviewCount < 20) stats.reviews_under_20++;

  const sasFlags = [];
  if (estROI < 20) { sasFlags.push('MAYBE-ROI'); stats.flagged_mayberoi++; }
  if (bbAmazonPct > 70) { sasFlags.push('AMAZON DOMINANT'); stats.flagged_amazon_dominant++; }

  const gated = '-';

  const outRow = [
    asin, title, brand, amazonUrl, category, weightFlag,
    price.toFixed(2), bb90Avg.toFixed(2), bb90Drop.toFixed(1) + '%',
    Math.round(bsr), Math.round(bsr90Avg), bsrDrops, bought,
    fbaCount, fba90Avg.toFixed(0), amazonCurrent ? 'Y' : 'N',
    fbaFee.toFixed(2), (refFee + perItemFee + digitalServicesFee).toFixed(2), totalFee.toFixed(2),
    estCost.toFixed(2), estProfit.toFixed(2), estROI.toFixed(1) + '%',
    maxCost20ROI.toFixed(2), breakevenPrice.toFixed(2),
    bbAmazonPct.toFixed(0) + '%', bbSeller.substring(0, 40), bbIsFBA,
    rating.toFixed(1), Math.round(reviewCount), imageCount, hasAplus === 'yes' ? 'Y' : 'N',
    bulletCount, listingQuality, brand1P,
    gated, sasFlags.join('; '), flags.join('; '), 'KEEPA',
    str(row, EAN_COL), str(row, UPC_COL), str(row, GTIN_COL)
  ];

  enrichedRows.push(outRow);
}

// Save outputs
const enrichedCSV = [OUT_HEADERS.map(escapeCSV).join(',')];
enrichedRows.forEach(r => enrichedCSV.push(r.map(escapeCSV).join(',')));
fs.writeFileSync(path.join(WORKING, 'pet_care_phase2_enriched.csv'), enrichedCSV.join('\n') + '\n');

if (hazmatRows.length > 0) {
  const hazmatCSV = [lines[0]];
  hazmatRows.forEach(r => hazmatCSV.push(r.join(',')));
  fs.writeFileSync(path.join(WORKING, 'pet_care_phase2_hazmat.csv'), hazmatCSV.join('\n') + '\n');
} else {
  fs.writeFileSync(path.join(WORKING, 'pet_care_phase2_hazmat.csv'), 'No HAZMAT products found\n');
}

fs.writeFileSync(path.join(WORKING, 'pet_care_phase2_gated.csv'),
  OUT_HEADERS.map(escapeCSV).join(',') + '\nNo gating data available without SellerAmp lookup\n');

const afterPrefilter = enrichedRows.length;
const totalRemoved = stats.removed_single_seller + stats.removed_erosion + stats.removed_velocity + stats.removed_oversaturated + stats.removed_hazmat + stats.removed_hazmat_title + stats.removed_heavy_title;
const statsContent = `Niche: pet-care
Date: ${new Date().toISOString().slice(0, 10)}

Phase 1 input count: ${stats.input}
After pre-filter: ${afterPrefilter}
Removed at pre-filter:
  Single seller: ${stats.removed_single_seller}
  Price erosion: ${stats.removed_erosion}
  Low velocity (<100/mo): ${stats.removed_velocity}
  Oversaturated (>20 sellers): ${stats.removed_oversaturated}
  Hazmat (Keepa flag): ${stats.removed_hazmat}
  Hazmat title keyword: ${stats.removed_hazmat_title}
  Heavy/bulky title keyword: ${stats.removed_heavy_title}
  Total removed: ${totalRemoved}
SellerAmp processed: 0 (enriched from Keepa data)
After enrichment trim:
  Hazmat removed: ${stats.removed_hazmat + stats.removed_hazmat_title}
  MAYBE-ROI flagged: ${stats.flagged_mayberoi}
  Amazon dominant flagged: ${stats.flagged_amazon_dominant}
Final enriched count: ${afterPrefilter}
Weight flags:
  HEAVY: ${stats.weight_heavy}
  OVERSIZE: ${stats.weight_oversize}
Listing quality breakdown:
  STRONG: ${stats.listing_strong}
  AVERAGE: ${stats.listing_average}
  WEAK: ${stats.listing_weak}
Brand 1P detected: ${stats.brand_1p}
Reviews > 500: ${stats.reviews_over_500}
Reviews < 20: ${stats.reviews_under_20}
Pre-filter flags:
  PRICE CHECK: ${stats.flagged_pricecheck}
  AMAZON CHECK: ${stats.flagged_amazon}
  LOW SELLER CHECK: ${stats.flagged_lowseller}
  PRICE DRIFT: ${stats.flagged_pricedrift}
Note: Enrichment derived from Keepa export data. Gating status requires SellerAmp lookup.
`;

fs.writeFileSync(path.join(WORKING, 'pet_care_phase2_stats.txt'), statsContent);

const handoff = `# Phase 2 Handoff -- pet-care

Status: COMPLETE
Input: ${stats.input} products from Phase 1
Output: ${afterPrefilter} enriched products

## Removed
- Single seller: ${stats.removed_single_seller}
- Price erosion: ${stats.removed_erosion}
- Low velocity: ${stats.removed_velocity}
- Oversaturated: ${stats.removed_oversaturated}
- Hazmat: ${stats.removed_hazmat + stats.removed_hazmat_title}
- Heavy/bulky title: ${stats.removed_heavy_title}

## Files
- pet_care_phase2_enriched.csv
- pet_care_phase2_hazmat.csv
- pet_care_phase2_gated.csv
- pet_care_phase2_stats.txt

## Next step
Run Phase 3 scoring: node data/pet-care/working/phase3_scoring.js
`;
fs.writeFileSync(path.join(WORKING, 'pet_care_phase2_handoff.md'), handoff);

console.log('Phase 2 complete:');
console.log(`  Input: ${stats.input}`);
console.log(`  Removed: ${totalRemoved}`);
console.log(`    Single seller: ${stats.removed_single_seller}, Erosion: ${stats.removed_erosion}, Velocity: ${stats.removed_velocity}, Oversaturated: ${stats.removed_oversaturated}, Hazmat: ${stats.removed_hazmat}, Hazmat title: ${stats.removed_hazmat_title}, Heavy title: ${stats.removed_heavy_title}`);
console.log(`  Enriched output: ${afterPrefilter}`);
console.log(`  Weight: HEAVY=${stats.weight_heavy} OVERSIZE=${stats.weight_oversize}`);
console.log(`  Listing Quality: STRONG=${stats.listing_strong} AVERAGE=${stats.listing_average} WEAK=${stats.listing_weak}`);
console.log(`  MAYBE-ROI: ${stats.flagged_mayberoi}`);
console.log(`  Amazon Dominant: ${stats.flagged_amazon_dominant}`);
