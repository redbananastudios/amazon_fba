// Phase 2 Enrichment v2: Merge Keepa prefiltered + SAS enrichment data
const fs = require('fs');
const path = require('path');

// Resolve project data dir relative to this script (scripts/ -> project root/data)
const DATA = path.resolve(__dirname, '..', 'data', 'pet-care');

// Parse simple CSV (no quoted fields with commas for SAS data)
function parseSimpleCSV(text) {
  return text.trim().split('\n').map(line => line.split(','));
}

// Parse Keepa CSV (has quoted fields)
function parseCSV(text) {
  const rows = [];
  let current = '', inQ = false, row = [];
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') { if (inQ && text[i+1] === '"') { current += '"'; i++; } else inQ = !inQ; }
    else if (ch === ',' && !inQ) { row.push(current); current = ''; }
    else if ((ch === '\n' || ch === '\r') && !inQ) {
      if (ch === '\r' && text[i+1] === '\n') i++;
      row.push(current); current = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else current += ch;
  }
  if (current || row.length > 0) { row.push(current); rows.push(row); }
  return rows;
}

function pn(v) { if (!v || v==='-' || v==='?') return null; const n = parseFloat(v.replace(/[£%,+]/g,'')); return isNaN(n)?null:n; }
function csvRow(arr) { return arr.map(c => { const s=String(c||''); return s.includes(',')||s.includes('"')?'"'+s.replace(/"/g,'""')+'"':s; }).join(','); }

// Load Keepa prefiltered
const keepaRaw = fs.readFileSync(path.join(DATA, 'pet_care_phase2_prefiltered.csv'), 'utf-8').replace(/^\uFEFF/,'');
const keepaRows = parseCSV(keepaRaw);
const kH = keepaRows[0], kD = keepaRows.slice(1);
const kCol = {}; kH.forEach((h,i) => kCol[h]=i);

// Load SAS enrichment data
const sasRaw = fs.readFileSync(path.join(DATA, 'sas_enrichment_data.csv'), 'utf-8').replace(/^\uFEFF/,'');
const sasLines = sasRaw.trim().split('\n');
const sasHeader = sasLines[0].split(',');
const sasMap = {};
for (let i = 1; i < sasLines.length; i++) {
  const parts = sasLines[i].split(',');
  const asin = parts[0];
  if (asin === 'ERROR' || parts[1] === 'ERROR') { sasMap[asin] = { error: true }; continue; }
  sasMap[asin] = {
    sale_price: parts[1], profit: parts[2], bsr: parts[3], est_sales: parts[4],
    max_cost: parts[5], breakeven: parts[6], offers: parts[7], fba: parts[8],
    fbm: parts[9], amazon_on_listing: parts[10], gated: parts[11], hazmat: parts[12],
    amazon_bb: parts[13], private_label: parts[14], total_fees: parts[15]
  };
}

// Output headers
const outH = ['ASIN','Title','Brand','Amazon URL','Category','Current Price (GBP)','Buy Box 90-day avg (GBP)','Price drop % 90-day','BSR Current','BSR Drops last 90 days','Bought in past month','New FBA Offer Count','Amazon on listing','Total Fees (GBP)','Est Cost 65% (GBP)','Est Profit (GBP)','ROI %','Max Cost (GBP)','Breakeven (GBP)','Gated (Y/N)','Hazmat','Amazon Buy Box Share','Private Label Risk','Sales velocity (SA)','SA FBA Count','Pre-filter flags','SellerAmp flags','Verdict','Data source'];

const enriched = [], gated = [], hazmat = [];
const stats = { input: kD.length, sa_ok: 0, sa_err: 0, hazmat_rm: 0, gated_fl: 0, maybe_roi: 0, amz_dom: 0, final: 0 };

for (const row of kD) {
  const asin = row[kCol['ASIN']];
  if (!asin) continue;
  const sas = sasMap[asin];
  if (!sas || sas.error) { stats.sa_err++; continue; }
  stats.sa_ok++;

  const sp = pn(sas.sale_price) || pn(row[kCol['Buy Box: Current']]) || 0;
  const totalFees = pn(sas.total_fees) || 0;
  const estCost = sp * 0.65;
  const estProfit = sp - estCost - totalFees;
  const roi = estCost > 0 ? ((estProfit / estCost) * 100) : 0;
  const preFlags = row[kH.length - 1] || '';
  const saFlags = [];
  let verdict = '';

  // Hazmat check
  if (sas.hazmat === 'Yes' || sas.hazmat === 'Y') { stats.hazmat_rm++; hazmat.push([asin, row[kCol['Title']], sas.hazmat]); continue; }

  // Gating
  if (sas.gated === 'Y') { stats.gated_fl++; saFlags.push('GATED'); verdict = 'GATED'; }

  // ROI flags
  if (roi < 20) { stats.maybe_roi++; saFlags.push('MAYBE-ROI'); if (!verdict) verdict = 'MAYBE-ROI'; }

  // Amazon dominant
  const bb = (sas.amazon_bb || '').toLowerCase();
  if (bb.includes('probably') || bb === 'yes') { stats.amz_dom++; saFlags.push('AMAZON DOMINANT'); }

  // FBA count for BRAND APPROACH
  const fbaCount = parseInt(sas.fba) || 0;
  if (fbaCount >= 2 && fbaCount <= 3 && verdict !== 'GATED') {
    if (!bb.includes('probably')) verdict = 'BRAND APPROACH';
  }

  if (!verdict) {
    if (roi >= 20) verdict = 'YES';
    else if (roi >= 20) verdict = 'MAYBE';
    else verdict = 'MAYBE-ROI';
  }

  const outRow = [
    asin, row[kCol['Title']]||'', row[kCol['Brand']]||'',
    'https://www.amazon.co.uk/dp/'+asin, row[kCol['Categories: Root']]||'',
    sp.toFixed(2), pn(row[kCol['Buy Box: 90 days avg.']])?.toFixed(2)||'',
    row[kCol['Buy Box: 90 days drop %']]||'',
    row[kCol['Sales Rank: Current']]||'', row[kCol['Sales Rank: Drops last 90 days']]||'',
    row[kCol['Bought in past month']]||'', row[kCol['New Offer Count: Current']]||'',
    sas.amazon_on_listing||'',
    totalFees.toFixed(2), estCost.toFixed(2), estProfit.toFixed(2), roi.toFixed(1)+'%',
    sas.max_cost||'', sas.breakeven||'',
    sas.gated||'', sas.hazmat||'', sas.amazon_bb||'', sas.private_label||'',
    sas.est_sales||'', sas.fba||'',
    preFlags, saFlags.join('; '), verdict, 'SA'
  ];

  enriched.push(outRow);
  if (sas.gated === 'Y') gated.push(outRow);
}
stats.final = enriched.length;

// Write files
fs.writeFileSync(path.join(DATA, 'pet_care_phase2_enriched.csv'), '\uFEFF'+[csvRow(outH),...enriched.map(csvRow)].join('\n'), 'utf-8');
fs.writeFileSync(path.join(DATA, 'pet_care_phase2_gated.csv'), '\uFEFF'+[csvRow(outH),...gated.map(csvRow)].join('\n'), 'utf-8');
fs.writeFileSync(path.join(DATA, 'pet_care_phase2_hazmat.csv'), '\uFEFF'+'ASIN,Title,Hazmat\n'+hazmat.map(csvRow).join('\n'), 'utf-8');

// Verdict distribution
const vd = {};
enriched.forEach(r => { const v = r[outH.indexOf('Verdict')]; vd[v] = (vd[v]||0)+1; });

// Stats
fs.writeFileSync(path.join(DATA, 'pet_care_phase2_stats.txt'),
`Phase 1 input count: 245
After pre-filter: ${stats.input}
Removed at pre-filter:
  Price erosion: 6
  Low velocity: 0
  Oversaturated: 5
SellerAmp processed: ${stats.sa_ok}
SellerAmp errors: ${stats.sa_err}
After enrichment trim:
  Hazmat removed: ${stats.hazmat_rm}
  Gated (flagged): ${stats.gated_fl}
  MAYBE-ROI flagged: ${stats.maybe_roi}
  Amazon dominant flagged: ${stats.amz_dom}
Final enriched count: ${stats.final}
Verdict distribution: ${JSON.stringify(vd)}
`);

// Handoff
fs.writeFileSync(path.join(DATA, 'pet_care_phase2_handoff.md'),
`# Phase 2 Handoff -- pet-care

Status: COMPLETE
Input: pet_care_phase1_raw.csv (245 products)
Pre-filtered: pet_care_phase2_prefiltered.csv (${stats.input} products)
Output: pet_care_phase2_enriched.csv (${stats.final} products)
Gated list: pet_care_phase2_gated.csv (${stats.gated_fl} products)
Hazmat list: pet_care_phase2_hazmat.csv (${stats.hazmat_rm} products)

## Verdict distribution
${Object.entries(vd).map(([k,v]) => '  '+k+': '+v).join('\n')}

## Key flags in the enriched file
PRICE CHECK: may be dipping -- confirm chart in Skill 3
AMAZON CHECK: Amazon on listing -- Buy Box % confirms dominance
LOW SELLER CHECK: only 2 sellers -- check brand quality
MAYBE-ROI: below 20% estimated at 65% cost -- may improve with real trade price
AMAZON DOMINANT: Amazon likely holds Buy Box >70% -- scored down in Skill 3
GATED: restricted listing -- flag for ungating decision
BRAND APPROACH: 2-3 FBA sellers, potential brand direct opportunity

## Next step
Run Skill 3 -- Scoring and Shortlist
Input: data/pet-care/pet_care_phase2_enriched.csv
`);

console.log(JSON.stringify(stats, null, 2));
console.log('Verdict distribution:', JSON.stringify(vd));
