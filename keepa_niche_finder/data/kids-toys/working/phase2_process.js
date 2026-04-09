#!/usr/bin/env node
/**
 * Phase 2 processing for kids-toys
 * Pre-filters Keepa data, extracts enrichment fields from Keepa export,
 * flags products needing SellerAmp confirmation.
 *
 * EXCLUDES educational subcategories (handled by educational-toys niche).
 * EXCLUDES apparel/costume and heavy outdoor items.
 */
const fs = require('fs');
const path = require('path');

const NICHE = 'kids-toys';
const BASE = path.resolve(__dirname, '..');
const WORKING = path.join(BASE, 'working');
const INPUT = fs.existsSync(path.join(BASE, 'kids_toys_phase1_filtered.csv'))
  ? path.join(BASE, 'kids_toys_phase1_filtered.csv')
  : path.join(WORKING, 'kids_toys_phase1_filtered.csv');

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

// Build column index lookup
const col = {};
headers.forEach((h, i) => { col[h] = i; });

// Subcategory EXCLUDE filter (kids-toys)
// Educational subcategories are handled by educational-toys niche -- remove them here
const EDU_EXCLUDE_KEYWORDS = [
  'learning', 'education', 'educational', 'steam', 'numeracy',
  'learning computer', 'learning area', 'educational game', 'stem'
];
const CATEGORIES_SUB = col['Categories: Sub'];
const SUBCAT_RANKS = col['Sales Rank: Subcategory Sales Ranks'];

// Title-based exclusions
const TITLE_EXCLUDE_APPAREL = ['costume', 'dress up', 'fancy dress', 'clothing'];
const TITLE_EXCLUDE_HEAVY = ['trampoline', 'climbing frame', 'swing set', 'playhouse', 'ride-on'];
const TITLE_COL = col['Title'];

const preFilterCount = lines.length - 1;
const filteredLines = [lines[0]]; // keep header
let removedEdu = 0;
let removedApparel = 0;
let removedHeavy = 0;

for (let i = 1; i < lines.length; i++) {
  const row = parseCSVLine(lines[i]);
  const sub = (row[CATEGORIES_SUB] || '').toLowerCase();
  const subRank = (row[SUBCAT_RANKS] || '').toLowerCase();
  const combined = sub + ' ' + subRank;
  const title = (row[TITLE_COL] || '').toLowerCase();

  // EXCLUDE educational subcategories
  if (EDU_EXCLUDE_KEYWORDS.some(k => combined.includes(k))) {
    removedEdu++;
    continue;
  }

  // EXCLUDE apparel/costume titles
  if (TITLE_EXCLUDE_APPAREL.some(k => title.includes(k))) {
    removedApparel++;
    continue;
  }

  // EXCLUDE heavy outdoor items
  if (TITLE_EXCLUDE_HEAVY.some(k => title.includes(k))) {
    removedHeavy++;
    continue;
  }

  filteredLines.push(lines[i]);
}
console.log(`Subcategory/title filter: ${preFilterCount} -> ${filteredLines.length - 1}`);
console.log(`  Removed educational: ${removedEdu}`);
console.log(`  Removed apparel/costume: ${removedApparel}`);
console.log(`  Removed heavy/outdoor: ${removedHeavy}`);

// Replace lines array with filtered version
lines.length = 0;
filteredLines.forEach(l => lines.push(l));

// Product code columns (for supplier price matching)
const EAN_COL = col['Product Codes: EAN'];
const UPC_COL = col['Product Codes: UPC'];
const GTIN_COL = col['Product Codes: GTIN'];

// Key column indices
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
const WEIGHT_FLAG = headers.length - 1; // Last column added in Phase 1
const BATT_REQ = col['Batteries Required'];
const BATT_INC = col['Batteries Included'];
const IS_HAZMAT = col['Is HazMat'];
const BB_IS_FBA = col['Buy Box: Is FBA'];
const VIDEOS = col['Videos: Video Count'];
const BINDING = col['Binding'];
const URL_SLUG = col['URL: URL slug'];

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
  removed_edu_subcat: removedEdu,
  removed_apparel: removedApparel,
  removed_heavy: removedHeavy,
  flagged_pricecheck: 0,
  flagged_amazon: 0,
  flagged_lowseller: 0,
  flagged_pricedrift: 0,
  flagged_mayberoi: 0,
  flagged_amazon_dominant: 0,
  gated_count: 0,
  sa_errors: 0,
  listing_strong: 0,
  listing_average: 0,
  listing_weak: 0,
  brand_1p: 0,
  reviews_over_500: 0,
  reviews_under_20: 0,
};

const enrichedRows = [];
const hazmatRows = [];
const gatedRows = [];
const removedRows = [];

// Output columns for enriched CSV (41 columns with EAN/UPC/GTIN at end)
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
  const weightFlag = str(row, WEIGHT_FLAG);
  const isHazmat = str(row, IS_HAZMAT).toLowerCase();
  const bbIsFBA = str(row, BB_IS_FBA);
  const hasAplus = str(row, HAS_APLUS).toLowerCase();
  const slug = str(row, URL_SLUG);
  const amazonUrl = 'https://www.amazon.co.uk/dp/' + asin;

  // Count images from semicolon-separated URLs
  const imageStr = str(row, IMAGES);
  const imageCount = imageStr ? imageStr.split(';').filter(u => u.trim()).length : 0;
  // Pre-filter removals
  let removed = false;
  let removeReason = '';

  // Hazmat confirmed
  if (isHazmat === 'yes' || isHazmat === 'y') {
    stats.removed_hazmat++;
    removeReason = 'HAZMAT';
    hazmatRows.push(row);
    removed = true;
  }

  // Oversaturated
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

  // Price erosion (drop > 20%)
  if (!removed && bb90Drop < -20) {
    stats.removed_erosion++;
    removeReason = 'PRICE EROSION';
    removed = true;
  }

  // Low velocity (below 100 for kids-toys)
  if (!removed && bought < 100) {
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
  // Amazon UK additional fees not in Keepa:
  //   Per-Item Fee: GBP0.75/unit (Professional sellers, most categories)
  //   Digital Services Fee: ~2% of referral fee (UK DST passthrough)
  const perItemFee = 0.75;
  const digitalServicesFee = refFee * 0.029;  // UK DST ~2.9% of referral fee
  const totalFee = fbaFee + refFee + perItemFee + digitalServicesFee;
  const estProfit = price - estCost - totalFee;
  const estROI = estCost > 0 ? (estProfit / estCost) * 100 : 0;
  const maxCost30ROI = (price - totalFee) / 1.20; // cost where ROI = 20%
  const breakevenPrice = estCost + totalFee;

  // Listing quality
  // Bullet count: estimate from Keepa - not directly available, assume 5 for A+ listings
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

  // Brand 1P detection
  const amazonSellerIds = ['A3P5ROKL5A1OLE', 'AZH2GF8Z5J95G'];
  let brand1P = 'N';
  if (bbSeller) {
    const sellerLower = bbSeller.toLowerCase();
    const brandLower = brand.toLowerCase();
    // Check if Amazon is the seller
    if (amazonSellerIds.some(id => bbSeller.includes(id)) || sellerLower.includes('amazon')) {
      // Amazon is seller, but that's not brand 1P unless brand IS Amazon
      if (brandLower.includes('amazon')) brand1P = 'Y';
    }
    // Check if brand name appears in seller name
    if (brandLower && brandLower.length > 2 && sellerLower.includes(brandLower)) {
      brand1P = 'Y';
    }
  }
  if (brand1P === 'Y') stats.brand_1p++;

  // Review stats
  if (reviewCount > 500) stats.reviews_over_500++;
  if (reviewCount < 20) stats.reviews_under_20++;

  // Post-enrichment flags
  const sasFlags = [];
  if (estROI < 20) { sasFlags.push('MAYBE-ROI'); stats.flagged_mayberoi++; }
  if (bbAmazonPct > 70) { sasFlags.push('AMAZON DOMINANT'); stats.flagged_amazon_dominant++; }

  // Gating - we can't determine from Keepa data alone, mark as unknown
  const gated = '-';
  // Build output row
  const outRow = [
    asin, title, brand, amazonUrl, category, weightFlag,
    price.toFixed(2), bb90Avg.toFixed(2), bb90Drop.toFixed(1) + '%',
    Math.round(bsr), Math.round(bsr90Avg), bsrDrops, bought,
    fbaCount, fba90Avg.toFixed(0), amazonCurrent ? 'Y' : 'N',
    fbaFee.toFixed(2), (refFee + perItemFee + digitalServicesFee).toFixed(2), totalFee.toFixed(2),
    estCost.toFixed(2), estProfit.toFixed(2), estROI.toFixed(1) + '%',
    maxCost30ROI.toFixed(2), breakevenPrice.toFixed(2),
    bbAmazonPct.toFixed(0) + '%', bbSeller.substring(0, 40), bbIsFBA,
    rating.toFixed(1), Math.round(reviewCount), imageCount, hasAplus === 'yes' ? 'Y' : 'N',
    bulletCount, listingQuality, brand1P,
    gated, sasFlags.join('; '), flags.join('; '), 'KEEPA',
    str(row, EAN_COL), str(row, UPC_COL), str(row, GTIN_COL)
  ];

  enrichedRows.push(outRow);

  // Track gated separately (all marked unknown for now since we can't determine from Keepa)
}

// Save outputs
// Enriched CSV
const enrichedCSV = [OUT_HEADERS.map(escapeCSV).join(',')];
enrichedRows.forEach(r => enrichedCSV.push(r.map(escapeCSV).join(',')));
fs.writeFileSync(path.join(WORKING, 'kids_toys_phase2_enriched.csv'), enrichedCSV.join('\n') + '\n');

// Hazmat CSV (header + rows)
if (hazmatRows.length > 0) {
  const hazmatCSV = [lines[0]];
  hazmatRows.forEach(r => hazmatCSV.push(r.join(',')));
  fs.writeFileSync(path.join(WORKING, 'kids_toys_phase2_hazmat.csv'), hazmatCSV.join('\n') + '\n');
} else {
  fs.writeFileSync(path.join(WORKING, 'kids_toys_phase2_hazmat.csv'), 'No HAZMAT products found\n');
}

// Gated CSV (empty for now - would need SellerAmp)
fs.writeFileSync(path.join(WORKING, 'kids_toys_phase2_gated.csv'),
  OUT_HEADERS.map(escapeCSV).join(',') + '\nNo gating data available without SellerAmp lookup\n');

// Stats
const afterPrefilter = enrichedRows.length;
const statsContent = `Niche: kids-toys
Date: ${new Date().toISOString().slice(0, 10)}

Phase 1 input count (after subcategory/title filter): ${stats.input}
Subcategory/title pre-filter:
  Removed educational subcategories: ${stats.removed_edu_subcat}
  Removed apparel/costume: ${stats.removed_apparel}
  Removed heavy/outdoor: ${stats.removed_heavy}
After pre-filter: ${afterPrefilter}
Removed at pre-filter:
  Single seller: ${stats.removed_single_seller}
  Price erosion: ${stats.removed_erosion}
  Low velocity (<100/mo): ${stats.removed_velocity}
  Oversaturated: ${stats.removed_oversaturated}
  Hazmat: ${stats.removed_hazmat}
SellerAmp processed: 0 (enriched from Keepa data)
SellerAmp errors: 0
After enrichment trim:
  Hazmat removed: ${stats.removed_hazmat}
  Gated (flagged): unknown (no SellerAmp)
  MAYBE-ROI flagged: ${stats.flagged_mayberoi}
  Amazon dominant flagged: ${stats.flagged_amazon_dominant}
Final enriched count: ${afterPrefilter}
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

fs.writeFileSync(path.join(WORKING, 'kids_toys_phase2_stats.txt'), statsContent);

console.log('Phase 2 complete:');
console.log(`  Input: ${stats.input}`);
console.log(`  Removed: ${stats.removed_single_seller + stats.removed_erosion + stats.removed_velocity + stats.removed_oversaturated + stats.removed_hazmat}`);
console.log(`    Single seller: ${stats.removed_single_seller}, Erosion: ${stats.removed_erosion}, Velocity: ${stats.removed_velocity}, Oversaturated: ${stats.removed_oversaturated}, Hazmat: ${stats.removed_hazmat}`);
console.log(`  Enriched output: ${afterPrefilter}`);
console.log(`  Listing Quality: STRONG=${stats.listing_strong} AVERAGE=${stats.listing_average} WEAK=${stats.listing_weak}`);
console.log(`  Brand 1P: ${stats.brand_1p}`);
console.log(`  MAYBE-ROI: ${stats.flagged_mayberoi}`);
console.log(`  Amazon Dominant: ${stats.flagged_amazon_dominant}`);
