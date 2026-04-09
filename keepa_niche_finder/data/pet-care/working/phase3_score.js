const fs = require('fs');
const path = require('path');

const BASE = path.resolve(__dirname, '..');
const INPUT = path.join(BASE, 'pet_care_phase2_enriched.csv');

// --- CSV parser (handles quoted fields) ---
function* parseCSVGen(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (inQuotes && text[i+1] === '"') { field += '"'; i++; }
      else { inQuotes = !inQuotes; }
    } else if (c === ',' && !inQuotes) {
      row.push(field); field = '';
    } else if ((c === '\n' || c === '\r') && !inQuotes) {
      if (c === '\r' && text[i+1] === '\n') i++;
      row.push(field); field = '';
      if (row.some(f => f.length > 0)) yield row;
      row = [];
    } else {
      field += c;
    }
  }
  row.push(field);
  if (row.some(f => f.length > 0)) yield row;
}

function readCSV(filepath) {
  const text = fs.readFileSync(filepath, 'utf-8');
  const gen = parseCSVGen(text);
  const headerRow = gen.next().value;
  const headers = headerRow.map(h => h.trim());
  const records = [];
  for (const row of gen) {
    const obj = {};
    headers.forEach((h, i) => { obj[h] = (row[i] || '').trim(); });
    records.push(obj);
  }
  return { headers, records };
}

// --- Helpers ---
function num(val) {
  if (!val) return 0;
  const n = parseFloat(String(val).replace(/[£%,+]/g, '').trim());
  return isNaN(n) ? 0 : n;
}

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

// --- Scoring ---
function demandScore(row) {
  const bsr = num(row['BSR Current']);
  let score;
  if (bsr < 10000) score = 10;
  else if (bsr <= 20000) score = 9;
  else if (bsr <= 30000) score = 8;
  else if (bsr <= 40000) score = 7;
  else if (bsr <= 50000) score = 6;
  else if (bsr <= 60000) score = 5;
  else if (bsr <= 80000) score = 3;
  else score = 1;

  const drops = num(row['BSR Drops last 90 days']);
  if (drops >= 15) score += 1;
  else if (drops < 3) score -= 1;

  const bought = num(row['Bought in past month']);
  if (bought >= 300) score += 1;
  else if (bought < 100) score -= 1;

  return clamp(score, 0, 10);
}

function stabilityScore(row) {
  const dropPct = num(row['Price drop % 90-day']);
  // Note: positive = price rose, negative = price dropped
  // The column has values like "10 %" (rose 10%), "-5 %" (dropped 5%)
  let score;
  if (dropPct >= 0) score = 10;         // flat or rising
  else if (dropPct >= -5) score = 8;
  else if (dropPct >= -10) score = 6;
  else if (dropPct >= -15) score = 4;
  else if (dropPct >= -20) score = 2;
  else score = 0;                        // PRICE EROSION

  const flags = (row['Pre-filter flags'] || '') + ' ' + (row['SellerAmp flags'] || '');
  if (flags.includes('PRICE CHECK')) score -= 1;

  return clamp(score, 0, 10);
}

function competitionScore(row) {
  const sellers = num(row['New FBA Offer Count']);
  const bought = num(row['Bought in past month']);

  // Dynamic ceiling
  let maxSellers;
  if (bought < 300) maxSellers = 8;
  else if (bought <= 600) maxSellers = 12;
  else if (bought <= 1000) maxSellers = 15;
  else maxSellers = 20;

  if (sellers > maxSellers) return 0;

  let score;
  if (sellers <= 2) score = 10;
  else if (sellers === 3) score = 9;
  else if (sellers <= 5) score = 7;
  else if (sellers <= 8) score = 5;
  else if (sellers <= 12) score = 3;
  else score = 1;

  // Amazon Buy Box modifier
  const amazonShare = row['Amazon Buy Box Share'] || '';
  // Parse share - could be "Probably", a percentage, etc.
  const amazonOnListing = row['Amazon on listing'] || '';
  const bbPctMatch = amazonShare.match(/(\d+)/);
  let bbPct = 0;
  if (amazonShare.toLowerCase().includes('probably') || amazonShare.toLowerCase().includes('likely')) {
    bbPct = 75; // assume high
  } else if (bbPctMatch) {
    bbPct = parseInt(bbPctMatch[1]);
  }

  if (amazonOnListing === 'Y' && bbPct > 70) score -= 3;
  else if (amazonOnListing === 'Y' && bbPct >= 50) score -= 1;

  return clamp(score, 0, 10);
}

function marginScore(row) {
  const roiStr = row['ROI %'] || '0';
  const roi = num(roiStr);
  let score;
  if (roi > 40) score = 10;
  else if (roi >= 35) score = 9;
  else if (roi >= 30) score = 7;
  else if (roi >= 25) score = 5;
  else if (roi >= 20) score = 3;
  else score = 1;

  const profit = num(row['Est Profit (GBP)']);
  if (profit > 8) score += 1;
  else if (profit < 3) score -= 1;

  return clamp(score, 0, 10);
}

function compositeScore(demand, stability, competition, margin) {
  return Math.round((demand * 0.30 + stability * 0.30 + competition * 0.20 + margin * 0.20) * 10) / 10;
}

function assignVerdict(row, demand, stability, competition, margin, composite) {
  const hazmat = (row['Hazmat'] || '').toUpperCase();
  if (hazmat === 'Y' || hazmat === 'YES') return { verdict: 'HAZMAT', reason: 'Confirmed hazmat' };

  if (stability === 0) return { verdict: 'PRICE EROSION', reason: 'Price drop >20% over 90 days' };

  const gated = (row['Gated (Y/N)'] || '').toUpperCase();
  if (gated === 'Y' || gated === 'YES') return { verdict: 'GATED', reason: 'Restricted listing' };

  // Check for BUY THE DIP before YES/MAYBE
  const dropPct = num(row['Price drop % 90-day']);
  const avgPrice = num(row['Buy Box 90-day avg (GBP)']);
  const currentPrice = num(row['Current Price (GBP)']);
  if (dropPct < -25 && avgPrice > currentPrice * 1.25) {
    return { verdict: 'BUY THE DIP', reason: `Price ${dropPct}% below avg, recovery potential` };
  }

  const sellers = num(row['New FBA Offer Count']);
  const flags = (row['SellerAmp flags'] || '') + ' ' + (row['Pre-filter flags'] || '');
  if (sellers >= 2 && sellers <= 3 && flags.includes('BRAND APPROACH')) {
    return { verdict: 'BRAND APPROACH', reason: `${sellers} sellers, weak listing, contact brand` };
  }

  if (composite >= 7) return { verdict: 'YES', reason: 'Strong composite score, all filters pass' };
  if (composite >= 5) return { verdict: 'MAYBE', reason: 'Moderate score, review concerns' };

  // Check BRAND APPROACH even without flag if 2-3 sellers
  if (sellers >= 2 && sellers <= 3) {
    return { verdict: 'BRAND APPROACH', reason: `${sellers} sellers, potential brand opportunity` };
  }

  return { verdict: 'NO', reason: `Low composite ${composite}` };
}

// --- Main ---
const { headers, records } = readCSV(INPUT);
console.log(`Read ${records.length} products from Phase 2`);

// Step 1: Hard rejects
const scored = [];
const rejected = [];

for (const row of records) {
  const price = num(row['Current Price (GBP)']);
  const sellers = num(row['New FBA Offer Count']);
  const hazmat = (row['Hazmat'] || '').toUpperCase();

  if (hazmat === 'Y' || hazmat === 'YES') {
    row._reject = 'HAZMAT'; rejected.push(row); continue;
  }
  if (price < 20 || price > 70) {
    row._reject = `NO (Price ${price} outside 20-70)`; rejected.push(row); continue;
  }
  if (sellers > 20) {
    row._reject = `NO (Oversaturated ${sellers} sellers)`; rejected.push(row); continue;
  }
  scored.push(row);
}

console.log(`Hard rejects: ${rejected.length}, Scoring: ${scored.length}`);

// Step 2-4: Score and assign verdicts
const allScored = [];
for (const row of scored) {
  const d = demandScore(row);
  const s = stabilityScore(row);
  const c = competitionScore(row);
  const m = marginScore(row);
  const comp = compositeScore(d, s, c, m);
  const { verdict, reason } = assignVerdict(row, d, s, c, m, comp);

  allScored.push({
    ...row,
    'Demand Score': d,
    'Stability Score': s,
    'Competition Score': c,
    'Margin Score': m,
    'Composite Score': comp,
    'Verdict': verdict,
    'Verdict Reason': reason
  });
}

// Also add rejected with scores = 0
for (const row of rejected) {
  allScored.push({
    ...row,
    'Demand Score': 0,
    'Stability Score': 0,
    'Competition Score': 0,
    'Margin Score': 0,
    'Composite Score': 0,
    'Verdict': row._reject.split(' ')[0] === 'NO' ? 'NO' : row._reject,
    'Verdict Reason': row._reject
  });
}

// Verdict distribution
const verdictCounts = {};
allScored.forEach(r => {
  verdictCounts[r.Verdict] = (verdictCounts[r.Verdict] || 0) + 1;
});
console.log('Verdict distribution:', JSON.stringify(verdictCounts, null, 2));

// Step 5: Build shortlist
const shortlistVerdicts = new Set(['YES', 'MAYBE', 'BRAND APPROACH', 'BUY THE DIP', 'GATED']);
let shortlist = allScored.filter(r => shortlistVerdicts.has(r.Verdict));

console.log(`Initial shortlist: ${shortlist.length} products`);

// Adjust threshold if needed
let threshold = 5;
while (shortlist.length > 100 && threshold <= 9) {
  threshold += 0.5;
  shortlist = allScored.filter(r =>
    shortlistVerdicts.has(r.Verdict) &&
    (r['Composite Score'] >= threshold || r.Verdict === 'GATED' || r.Verdict === 'BRAND APPROACH' || r.Verdict === 'BUY THE DIP')
  );
  console.log(`Raised threshold to ${threshold}, shortlist now: ${shortlist.length}`);
}
if (shortlist.length < 30) {
  threshold = 4.5;
  // Re-include products above lower threshold
  shortlist = allScored.filter(r =>
    shortlistVerdicts.has(r.Verdict) ||
    (r['Composite Score'] >= threshold && r.Verdict !== 'NO' && r.Verdict !== 'HAZMAT' && r.Verdict !== 'PRICE EROSION')
  );
  console.log(`Lowered threshold to ${threshold}, shortlist now: ${shortlist.length}`);
}

// Sort shortlist
const verdictOrder = { 'YES': 0, 'MAYBE': 1, 'BRAND APPROACH': 2, 'BUY THE DIP': 3, 'GATED': 4 };
shortlist.sort((a, b) => {
  const va = verdictOrder[a.Verdict] ?? 99;
  const vb = verdictOrder[b.Verdict] ?? 99;
  if (va !== vb) return va - vb;
  return b['Composite Score'] - a['Composite Score'];
});

console.log(`Final shortlist: ${shortlist.length} products`);

// --- Step 6: Save output files ---

// CSV writer
function toCSV(rows, columns) {
  const escape = (v) => {
    const s = String(v ?? '');
    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  };
  const lines = [columns.map(escape).join(',')];
  rows.forEach(r => {
    lines.push(columns.map(c => escape(r[c])).join(','));
  });
  return lines.join('\n');
}

const scoredColumns = [...headers, 'Demand Score', 'Stability Score', 'Competition Score', 'Margin Score', 'Composite Score', 'Verdict', 'Verdict Reason'];

// Full scored file
fs.writeFileSync(
  path.join(BASE, 'pet_care_phase3_scored.csv'),
  toCSV(allScored, scoredColumns)
);

// Shortlist CSV
fs.writeFileSync(
  path.join(BASE, 'pet_care_phase3_shortlist.csv'),
  toCSV(shortlist, scoredColumns)
);

// Shortlist JSON
const shortlistJSON = shortlist.map(r => {
  const obj = {};
  scoredColumns.forEach(c => { obj[c] = r[c]; });
  return obj;
});
fs.writeFileSync(
  path.join(BASE, 'pet_care_phase3_shortlist.json'),
  JSON.stringify(shortlistJSON, null, 2)
);

// Top 5 by composite
const top5 = [...shortlist].sort((a, b) => b['Composite Score'] - a['Composite Score']).slice(0, 5);

// Stats file
const statsContent = `Niche: pet-care
Date: 2026-03-20
Input: ${records.length} products from Phase 2
Hard rejects: ${rejected.length}
Composite score threshold used: ${threshold}

Verdict breakdown:
  YES:            ${verdictCounts['YES'] || 0}
  MAYBE:          ${verdictCounts['MAYBE'] || 0}
  BRAND APPROACH: ${verdictCounts['BRAND APPROACH'] || 0}
  BUY THE DIP:    ${verdictCounts['BUY THE DIP'] || 0}
  GATED:          ${verdictCounts['GATED'] || 0}
  PRICE EROSION:  ${verdictCounts['PRICE EROSION'] || 0}
  NO:             ${verdictCounts['NO'] || 0}
  HAZMAT:         ${verdictCounts['HAZMAT'] || 0}

Shortlist total: ${shortlist.length}
Top 5 by composite score:
${top5.map((r, i) => `  ${i+1}. ${r.ASIN} - ${r.Title?.substring(0, 60)} - ${r['Composite Score']} - ${r.Verdict}`).join('\n')}
`;
fs.writeFileSync(path.join(BASE, 'pet_care_phase3_stats.txt'), statsContent);

// Handoff file
const yesCount = shortlist.filter(r => r.Verdict === 'YES').length;
const maybeCount = shortlist.filter(r => r.Verdict === 'MAYBE').length;
const brandCount = shortlist.filter(r => r.Verdict === 'BRAND APPROACH').length;
const dipCount = shortlist.filter(r => r.Verdict === 'BUY THE DIP').length;
const gatedCount = shortlist.filter(r => r.Verdict === 'GATED').length;

const handoffContent = `# Phase 3 Handoff -- pet-care

Status: COMPLETE
Shortlist: ${shortlist.length} products
Files:
  pet_care_phase3_scored.csv    -- all products with scores
  pet_care_phase3_shortlist.csv -- shortlist for supplier research
  pet_care_phase3_shortlist.json

## Priority actions from shortlist
YES products (${yesCount}): ready for supplier research immediately
BRAND APPROACH (${brandCount}): contact brand direct -- weak Amazon presence
BUY THE DIP (${dipCount}): check availability at dip price
GATED (${gatedCount}): apply for approval before supplier research
MAYBE (${maybeCount}): review concerns before committing

## Next step
Run Skill 4 -- Supplier Research
Input: data\\pet-care\\pet_care_phase3_shortlist.csv
Also read: SUPPLIERS.CSV in project root
`;
fs.writeFileSync(path.join(BASE, 'pet_care_phase3_handoff.md'), handoffContent);

console.log('\n--- Phase 3 COMPLETE ---');
console.log(`Scored: ${allScored.length}`);
console.log(`Shortlist: ${shortlist.length}`);
console.log(`Files saved to ${BASE}`);
