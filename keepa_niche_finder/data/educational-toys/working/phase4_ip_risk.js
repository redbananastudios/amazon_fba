#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const NICHE = 'educational-toys';
const BASE = path.resolve(__dirname, '..');
const WORKING = path.join(BASE, 'working');
const NICHE_SNAKE = NICHE.replace(/-/g, '_');
const INPUT = path.join(WORKING, `${NICHE_SNAKE}_phase3_shortlist.csv`);
const OUTPUT = path.join(WORKING, `${NICHE_SNAKE}_phase4_ip_risk.csv`);
const STATS = path.join(WORKING, `${NICHE_SNAKE}_phase4_stats.txt`);
const HANDOFF = path.join(WORKING, `${NICHE_SNAKE}_phase4_handoff.md`);

const IP_HEADERS = [
  'Brand Seller Match',
  'Fortress Listing',
  'Brand Type',
  'A+ Content Present',
  'Brand Store Present',
  'Category Risk Level',
  'IP Risk Score',
  'IP Risk Band',
  'IP Reason'
];

const KNOWN_ESTABLISHED_BRANDS = new Set([
  'lego', 'pokemon', 'disney', 'barbie', 'fisher price', 'fisherprice',
  'vtech', 'wilson', 'yonex', 'titleist', 'adidas', 'head', 'babolat',
  'dunlop', 'carlton', 'stiga', 'play doh', 'playdoh', 'nerf',
  'hot wheels', 'hotwheels'
]);

function parseCSVLine(line) {
  const fields = [];
  let field = '';
  let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQ && line[i + 1] === '"') {
        field += '"';
        i++;
      } else {
        inQ = !inQ;
      }
    } else if (ch === ',' && !inQ) {
      fields.push(field);
      field = '';
    } else {
      field += ch;
    }
  }
  fields.push(field);
  return fields;
}

function esc(val) {
  if (val === null || val === undefined) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
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

function normalizeName(value) {
  return String(value || '')
    .toLowerCase()
    .split('/')[0]
    .replace(/\([^)]*\)/g, ' ')
    .replace(/\b(ltd|limited|inc|uk)\b/g, ' ')
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function levenshtein(a, b) {
  const s = a || '';
  const t = b || '';
  if (s === t) return 0;
  if (!s.length) return t.length;
  if (!t.length) return s.length;

  const dp = Array.from({ length: s.length + 1 }, () => new Array(t.length + 1).fill(0));
  for (let i = 0; i <= s.length; i++) dp[i][0] = i;
  for (let j = 0; j <= t.length; j++) dp[0][j] = j;

  for (let i = 1; i <= s.length; i++) {
    for (let j = 1; j <= t.length; j++) {
      const cost = s[i - 1] === t[j - 1] ? 0 : 1;
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + cost
      );
    }
  }
  return dp[s.length][t.length];
}

function similarity(a, b) {
  const left = normalizeName(a);
  const right = normalizeName(b);
  if (!left || !right) return 0;
  const distance = levenshtein(left, right);
  return 1 - (distance / Math.max(left.length, right.length, 1));
}

function categoryRiskLevel(niche) {
  const map = {
    'educational-toys': 'HIGH',
    'kids-toys': 'HIGH',
    'afro-hair': 'MEDIUM',
    'pet-care': 'MEDIUM',
    'sports-goods': 'MEDIUM',
    'stationery': 'LOW'
  };
  return map[niche] || 'MEDIUM';
}

function brandType(rawBrand, reviewCount, rating) {
  const clean = normalizeName(rawBrand);
  const compact = String(rawBrand || '').replace(/[^A-Za-z0-9]/g, '');
  if (KNOWN_ESTABLISHED_BRANDS.has(clean) || (reviewCount > 500 && rating > 3.5)) return 'ESTABLISHED';
  if (/^[A-Z]{2,}$/.test(compact) || /\d{2,}/.test(compact) || compact.length <= 3) return 'SYNTHETIC';
  return 'GENERIC';
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

if (!fs.existsSync(INPUT)) {
  console.error(`Input not found: ${INPUT}`);
  process.exit(1);
}

const raw = fs.readFileSync(INPUT, 'utf-8').replace(/^\uFEFF/, '');
const rows = parseCSV(raw);
const headers = rows[0];
const col = {};
headers.forEach((header, index) => { col[header] = index; });

function str(row, name) {
  const idx = col[name];
  if (idx === undefined) return '';
  return String(row[idx] || '').trim();
}

function num(row, name) {
  const idx = col[name];
  if (idx === undefined) return 0;
  const rawValue = String(row[idx] || '').replace(/GBP/gi, '').replace(/[^0-9.-]/g, '').trim();
  return parseFloat(rawValue) || 0;
}

const outRows = [];
const bandCounts = { High: 0, Medium: 0, Low: 0 };
const sellerMatchCounts = { YES: 0, PARTIAL: 0, NO: 0 };
let fortressCount = 0;
const brandTypeCounts = { ESTABLISHED: 0, GENERIC: 0, SYNTHETIC: 0 };
const scored = [];
const falsePositivesAvoided = [];

for (let i = 1; i < rows.length; i++) {
  const row = rows[i];
  const brand = str(row, 'Brand');
  const bbSeller = str(row, 'BB Seller');
  const fbaSellerCount = num(row, 'FBA Seller Count');
  const fbaSeller90dAvg = num(row, 'FBA Seller 90d Avg');
  const reviewCount = num(row, 'Review Count');
  const rating = num(row, 'Star Rating');
  const hasAplus = str(row, 'Has A+ Content');
  const brand1P = str(row, 'Brand 1P');
  const asin = str(row, 'ASIN');
  const monthlyGrossProfit = num(row, 'Monthly Gross Profit');

  const brandNorm = normalizeName(brand);
  const sellerNorm = normalizeName(bbSeller);
  let brandSellerMatch = 'NO';
  if (brandNorm && sellerNorm && (brandNorm.includes(sellerNorm) || sellerNorm.includes(brandNorm))) {
    brandSellerMatch = 'YES';
  } else if (similarity(brand, bbSeller) > 0.7) {
    brandSellerMatch = 'PARTIAL';
  }

  const fortressListing = (fbaSellerCount <= 1 && fbaSeller90dAvg <= 1.5) ? 'YES' : 'NO';
  const derivedBrandType = brandType(brand, reviewCount, rating);
  const aplusPresent = /^(y|yes)$/i.test(hasAplus) ? 'YES' : 'NO';
  const brandStorePresent = (brandSellerMatch === 'YES' && aplusPresent === 'YES') ? 'LIKELY' : 'UNLIKELY';
  const riskLevel = categoryRiskLevel(NICHE);

  let score = 0;
  const reasons = [];

  if (brandSellerMatch === 'YES') {
    score += 3;
    reasons.push('Brand=Seller match (YES)');
  } else if (brandSellerMatch === 'PARTIAL') {
    score += 1;
    reasons.push('Brand=Seller match (PARTIAL)');
  }

  if (fortressListing === 'YES') {
    score += 3;
    reasons.push('Fortress listing');
  }

  if (derivedBrandType === 'ESTABLISHED') {
    score += 1;
    reasons.push('Established brand');
  }

  if (aplusPresent === 'YES') {
    score += 1;
    reasons.push('A+ content');
  }

  if (brandStorePresent === 'LIKELY') {
    score += 1;
    reasons.push('Likely brand store');
  }

  if (riskLevel === 'HIGH') {
    score += 1;
    reasons.push('Category HIGH risk');
  } else if (riskLevel === 'MEDIUM') {
    score += 0.5;
    reasons.push('Category MEDIUM risk');
  }

  const finalScore = clamp(Math.round(score), 0, 10);
  const band = finalScore >= 7 ? 'High' : finalScore >= 4 ? 'Medium' : 'Low';
  const ipReason = reasons.join(' | ');

  sellerMatchCounts[brandSellerMatch] += 1;
  brandTypeCounts[derivedBrandType] += 1;
  bandCounts[band] += 1;
  if (fortressListing === 'YES') fortressCount += 1;

  if (brand1P === 'Y' && band === 'Low') {
    falsePositivesAvoided.push(`${asin} - ${brand} - Low - Brand 1P=Y`);
  }

  scored.push({
    asin,
    brand,
    score: finalScore,
    band,
    reason: ipReason,
    monthlyGrossProfit
  });

  outRows.push([
    ...row,
    brandSellerMatch,
    fortressListing,
    derivedBrandType,
    aplusPresent,
    brandStorePresent,
    riskLevel,
    finalScore,
    band,
    ipReason
  ]);
}

const outHeaders = [...headers, ...IP_HEADERS];
const outCsv = [outHeaders.map(esc).join(',')];
outRows.forEach(row => outCsv.push(row.map(esc).join(',')));
fs.writeFileSync(OUTPUT, outCsv.join('\n') + '\n');

const total = outRows.length || 1;
const top10HighRisk = [...scored]
  .sort((a, b) => (b.score - a.score) || (b.monthlyGrossProfit - a.monthlyGrossProfit) || a.asin.localeCompare(b.asin))
  .slice(0, 10)
  .map((item, index) => `  ${index + 1}. ${item.asin} - ${item.brand} - ${item.score} - ${item.band} - ${item.reason || 'No contributing factors'}`)
  .join('\n');

const stats = `Niche: ${NICHE}
Date: ${new Date().toISOString().slice(0, 10)}
Input: ${outRows.length} products from Phase 3 shortlist

IP Risk Band distribution:
  High:   ${bandCounts.High} (${Math.round((bandCounts.High / total) * 100)}%)
  Medium: ${bandCounts.Medium} (${Math.round((bandCounts.Medium / total) * 100)}%)
  Low:    ${bandCounts.Low} (${Math.round((bandCounts.Low / total) * 100)}%)

Brand Seller Match:
  YES:     ${sellerMatchCounts.YES}
  PARTIAL: ${sellerMatchCounts.PARTIAL}
  NO:      ${sellerMatchCounts.NO}

Fortress Listings: ${fortressCount}

Brand Type:
  ESTABLISHED: ${brandTypeCounts.ESTABLISHED}
  GENERIC:     ${brandTypeCounts.GENERIC}
  SYNTHETIC:   ${brandTypeCounts.SYNTHETIC}

Top 10 highest IP Risk:
${top10HighRisk}

False positives avoided (Low risk with Brand 1P = Y):
${falsePositivesAvoided.length ? falsePositivesAvoided.map(item => `  ${item}`).join('\n') : '  None'}
`;
fs.writeFileSync(STATS, stats);

const handoff = `# Phase 4 Handoff -- ${NICHE}

Status: COMPLETE
Input products: ${outRows.length}

## Files
- ${path.basename(OUTPUT)}
- ${path.basename(STATS)}
- ${path.basename(HANDOFF)}

## Summary
- High IP risk: ${bandCounts.High}
- Medium IP risk: ${bandCounts.Medium}
- Low IP risk: ${bandCounts.Low}
- Fortress listings: ${fortressCount}

## Next step
Run Phase 5 build using ${path.basename(OUTPUT)} as the preferred input.
`;
fs.writeFileSync(HANDOFF, handoff);

console.log(`Saved: ${OUTPUT}`);
console.log(`Saved: ${STATS}`);
console.log(`Saved: ${HANDOFF}`);
