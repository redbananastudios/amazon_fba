#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const BASE = path.resolve(__dirname, '..');
const WORKING = path.join(BASE, 'working');
const INPUT = fs.existsSync(path.join(BASE, 'kids_toys_phase2_enriched.csv'))
  ? path.join(BASE, 'kids_toys_phase2_enriched.csv')
  : path.join(WORKING, 'kids_toys_phase2_enriched.csv');

const LANE_CONFIG = {
  cashflow_volume_threshold: 40,
  cashflow_min_unit_profit: 0.6,
  cashflow_monthly_profit_threshold: 30,
  balanced_volume_threshold: 25,
  balanced_profit_threshold: 4,
  balanced_monthly_profit_threshold: 50,
  profit_unit_profit_threshold: 6,
  max_seller_count_by_lane: {
    BALANCED: 8,
    'CASH FLOW': 12,
    PROFIT: 8
  },
  amazon_risk_penalty: 1.0,
  price_drop_penalty: 1.0,
  min_stability_score: 4,
  min_competition_score: 3,
  min_profit_roi: 15
};

const VERDICT_ORDER = {
  YES: 1,
  MAYBE: 2,
  'MAYBE-ROI': 3,
  'BRAND APPROACH': 4,
  'BUY THE DIP': 5,
  GATED: 6,
  'PRICE EROSION': 7,
  NO: 8,
  HAZMAT: 9
};

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

const raw = fs.readFileSync(INPUT, 'utf-8');
const lines = raw.split('\n').filter(l => l.trim());
const headers = parseCSVLine(lines[0]);
const col = {};
headers.forEach((h, i) => { col[h] = i; });

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

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

function scoreBands(value, bands, fallback = 0) {
  for (const band of bands) {
    if (value >= band.min) return band.score;
  }
  return fallback;
}

function estimateMonthlySales(bought, bsr, bsrDrops) {
  let estimate = Math.max(bought, bsrDrops > 0 ? Math.round(bsrDrops * 2) : 0, 0);
  if (estimate > 0) return estimate;
  if (bsr < 10000) return 80;
  if (bsr < 20000) return 45;
  if (bsr < 30000) return 30;
  if (bsr < 40000) return 20;
  if (bsr < 50000) return 12;
  if (bsr < 60000) return 8;
  return 4;
}

function scoreVelocity(monthlySales) {
  return scoreBands(monthlySales, [
    { min: 80, score: 10 },
    { min: 60, score: 9 },
    { min: 40, score: 8 },
    { min: 25, score: 7 },
    { min: 15, score: 6 },
    { min: 10, score: 5 },
    { min: 5, score: 3 }
  ], 1);
}

function scoreUnitProfit(estProfit) {
  return scoreBands(estProfit, [
    { min: 10, score: 10 },
    { min: 8, score: 9 },
    { min: 6, score: 8 },
    { min: 4.5, score: 7 },
    { min: 3, score: 6 },
    { min: 2, score: 4 },
    { min: 1.25, score: 3 },
    { min: 0.75, score: 2 }
  ], 0);
}

function scoreMonthlyProfit(monthlyGrossProfit) {
  return scoreBands(monthlyGrossProfit, [
    { min: 120, score: 10 },
    { min: 90, score: 9 },
    { min: 70, score: 8 },
    { min: 50, score: 7 },
    { min: 35, score: 6 },
    { min: 25, score: 5 },
    { min: 15, score: 4 },
    { min: 8, score: 2 }
  ], 0);
}

function scoreMarginSafety(estROI) {
  return scoreBands(estROI, [
    { min: 35, score: 10 },
    { min: 30, score: 9 },
    { min: 25, score: 8 },
    { min: 20, score: 7 },
    { min: 15, score: 5 },
    { min: 10, score: 3 },
    { min: 5, score: 1 }
  ], 0);
}

function scoreROI(estROI) {
  return scoreBands(estROI, [
    { min: 30, score: 10 },
    { min: 25, score: 8 },
    { min: 20, score: 6 },
    { min: 15, score: 4 },
    { min: 10, score: 2 }
  ], 0);
}

function scoreDemandFloor(monthlySales) {
  return scoreBands(monthlySales, [
    { min: 80, score: 10 },
    { min: 50, score: 8 },
    { min: 25, score: 6 },
    { min: 10, score: 4 },
    { min: 5, score: 2 }
  ], 0);
}

function scoreAmazonRisk(brand1P, bbAmazonPct, amazonOnListing) {
  let score = 10;
  if (brand1P === 'Y') score -= 6;
  else if (bbAmazonPct > 70) score -= 5;
  else if (bbAmazonPct > 50) score -= 3;
  else if (bbAmazonPct > 30) score -= 1;
  if (amazonOnListing === 'Y') score -= 1;
  return clamp(score, 0, 10);
}

function laneSortValue(lane) {
  return { BALANCED: 1, 'CASH FLOW': 2, PROFIT: 3, UNASSIGNED: 9 }[lane] || 9;
}

const verdictCounts = {};
let laneBalanced = 0;
let laneProfit = 0;
let laneCashFlow = 0;
let laneUnassigned = 0;
const allScored = [];
const shortlist = [];

for (let i = 1; i < lines.length; i++) {
  const row = parseCSVLine(lines[i]);
  const brand = str(row, 'Brand');
  const weightFlag = str(row, 'Weight Flag');
  const price = num(row, 'Current Price');
  const bb90Avg = num(row, 'Buy Box 90d Avg');
  const priceDrop = num(row, 'Price Drop % 90d');
  const bsr = num(row, 'BSR Current');
  const bsrDrops = num(row, 'BSR Drops 90d');
  const bought = num(row, 'Bought per Month');
  const fbaCount = num(row, 'FBA Seller Count');
  const fba90Avg = num(row, 'FBA Seller 90d Avg');
  const amazonOnListing = str(row, 'Amazon on Listing');
  const totalFees = num(row, 'Total Amazon Fees');
  const estProfit = num(row, 'Est Profit');
  const estROI = num(row, 'Est ROI %');
  const bbAmazonPct = num(row, 'Buy Box Amazon %');
  const rating = num(row, 'Star Rating');
  const reviewCount = num(row, 'Review Count');
  const imageCount = num(row, 'Image Count');
  const hasAplus = str(row, 'Has A+ Content');
  const listingQuality = str(row, 'Listing Quality');
  const brand1P = str(row, 'Brand 1P');
  const gated = str(row, 'Gated');
  const preFlags = str(row, 'Pre-filter Flags');

  let demand = 1;
  if (bsr < 10000) demand = 10;
  else if (bsr < 20000) demand = 9;
  else if (bsr < 30000) demand = 8;
  else if (bsr < 40000) demand = 7;
  else if (bsr < 50000) demand = 6;
  else if (bsr < 60000) demand = 5;
  else if (bsr < 80000) demand = 3;
  else demand = 1;

  if (bsrDrops >= 15) demand += 1;
  else if (bsrDrops < 3) demand -= 1;

  if (bought >= 200) demand += 1;
  else if (bought > 0 && bought < 50) demand -= 1;

  if (rating > 0 && rating < 3.5) demand -= 1;
  if (reviewCount > 500 && rating > 4.0) demand += 1;
  demand = clamp(demand, 0, 10);

  let stability = 0;
  if (priceDrop >= 0) stability = 10;
  else if (priceDrop >= -5) stability = 8;
  else if (priceDrop >= -10) stability = 6;
  else if (priceDrop >= -15) stability = 4;
  else if (priceDrop >= -20) stability = 2;
  else stability = 0;

  if (preFlags.includes('PRICE CHECK')) stability -= 1;
  if (priceDrop < -10 && price > bb90Avg) stability += 2;
  else if (priceDrop < -10 && price <= bb90Avg) stability -= 1;
  stability = clamp(stability, 0, 10);

  const monthlySales = estimateMonthlySales(bought, bsr, bsrDrops);

  let competition = 0;
  let maxSellers = 8;
  if (monthlySales >= 1000) maxSellers = 20;
  else if (monthlySales >= 600) maxSellers = 15;
  else if (monthlySales >= 300) maxSellers = 12;

  if (fbaCount > maxSellers) {
    competition = 0;
  } else if (fbaCount <= 2) {
    competition = 10;
  } else if (fbaCount === 3) {
    competition = 9;
  } else if (fbaCount <= 5) {
    competition = 7;
  } else if (fbaCount <= 8) {
    competition = 5;
  } else if (fbaCount <= 12) {
    competition = 3;
  } else {
    competition = 1;
  }

  if (bbAmazonPct > 70) competition -= 3;
  else if (bbAmazonPct >= 50) competition -= 1;

  if (fbaCount < fba90Avg) competition += 1;
  else if (fbaCount > fba90Avg * 1.5) competition -= 1;

  if (brand1P === 'Y') competition -= 2;
  if (reviewCount < 20) competition -= 1;
  competition = clamp(competition, 0, 10);

  let margin = 1;
  if (estROI > 40) margin = 10;
  else if (estROI > 35) margin = 9;
  else if (estROI > 30) margin = 7;
  else if (estROI > 25) margin = 5;
  else if (estROI > 20) margin = 3;
  else margin = 1;

  if (estProfit > 8) margin += 1;
  else if (estProfit < 3) margin -= 1;

  if (weightFlag.includes('HEAVY') && weightFlag.includes('OVERSIZE')) margin -= 2;
  else if (weightFlag.includes('HEAVY') || weightFlag.includes('OVERSIZE')) margin -= 1;
  margin = clamp(margin, 0, 10);

  const composite = (demand * 0.30) + (stability * 0.30) + (competition * 0.20) + (margin * 0.20);
  const compositeRound = round1(composite);
  const monthlyGrossProfit = round1(monthlySales * estProfit);

  let priceCompression = '';
  if (bb90Avg > 0 && price > 0) {
    const ratio = price / bb90Avg;
    if (ratio < 0.80) priceCompression = 'COMPRESSED';
    else if (ratio < 0.90) priceCompression = 'SQUEEZED';
    else priceCompression = 'OK';
  }

  const velocityScore = scoreVelocity(monthlySales);
  const unitProfitScore = scoreUnitProfit(estProfit);
  const monthlyProfitScore = scoreMonthlyProfit(monthlyGrossProfit);
  const marginSafetyScore = scoreMarginSafety(estROI);
  const roiScore = scoreROI(estROI);
  const demandFloorScore = scoreDemandFloor(monthlySales);
  const amazonRiskAdjustedScore = scoreAmazonRisk(brand1P, bbAmazonPct, amazonOnListing);
  const pricePenalty =
    priceCompression === 'COMPRESSED' ? LANE_CONFIG.price_drop_penalty :
    priceCompression === 'SQUEEZED' ? LANE_CONFIG.price_drop_penalty / 2 :
    0;
  const amazonPenalty =
    brand1P === 'Y' || bbAmazonPct > 70 ? LANE_CONFIG.amazon_risk_penalty :
    bbAmazonPct > 50 ? LANE_CONFIG.amazon_risk_penalty / 2 :
    0;

  const cashFlowScore = clamp(round1(
    (velocityScore * 0.40) +
    (monthlyProfitScore * 0.25) +
    (stability * 0.15) +
    (competition * 0.10) +
    (marginSafetyScore * 0.05) +
    (amazonRiskAdjustedScore * 0.05) -
    pricePenalty -
    amazonPenalty
  ), 0, 10);

  const profitScore = clamp(round1(
    (unitProfitScore * 0.35) +
    (marginSafetyScore * 0.25) +
    (roiScore * 0.20) +
    (stability * 0.10) +
    (competition * 0.05) +
    (demandFloorScore * 0.05) -
    pricePenalty -
    (amazonPenalty / 2)
  ), 0, 10);

  const balancedScore = clamp(round1(
    (velocityScore * 0.25) +
    (unitProfitScore * 0.25) +
    (monthlyProfitScore * 0.20) +
    (marginSafetyScore * 0.10) +
    (stability * 0.10) +
    (competition * 0.05) +
    (amazonRiskAdjustedScore * 0.05) -
    pricePenalty -
    amazonPenalty
  ), 0, 10);

  let verdict = 'NO';
  let verdictReason = '';
  const roiStr = estROI.toFixed(0) + '%';
  const stabilityLabel = priceCompression === 'OK' ? 'stable' : (priceCompression || 'watch');
  const commercialSnapshot = `${monthlySales}/mo | GBP${estProfit.toFixed(2)} profit | GBP${monthlyGrossProfit.toFixed(2)} monthly gross | ${stabilityLabel} | ${fbaCount} sellers`;

  if (fbaCount < 2) {
    verdict = 'NO';
    verdictReason = `NO | ${monthlySales}/mo est | GBP${estProfit.toFixed(2)} profit | ${fbaCount} seller | listing control risk`;
  } else if (stability === 0) {
    verdict = 'PRICE EROSION';
    verdictReason = `NO | ${monthlySales}/mo est | GBP${estProfit.toFixed(2)} profit | ${stabilityLabel} | margin collapsing`;
  } else if (brand1P === 'Y' && bbAmazonPct > 60) {
    verdict = 'NO';
    verdictReason = `NO | ${monthlySales}/mo est | GBP${estProfit.toFixed(2)} profit | Amazon BB ${bbAmazonPct.toFixed(0)}% | brand direct`;
  } else if (gated === 'Y') {
    verdict = 'GATED';
    verdictReason = `GATED | ${commercialSnapshot} | apply for access`;
  } else if (compositeRound >= 8.5) {
    verdict = 'YES';
    verdictReason = `YES | ${commercialSnapshot}`;
  } else if (fbaCount <= 3 && listingQuality === 'WEAK') {
    verdict = 'BRAND APPROACH';
    verdictReason = `BRAND APPROACH | ${commercialSnapshot} | weak listing ${imageCount} imgs${hasAplus === 'Y' ? ' + A+' : ''}`;
  } else if (priceDrop < -25 && price > bb90Avg) {
    verdict = 'BUY THE DIP';
    verdictReason = `BUY THE DIP | ${commercialSnapshot} | GBP${price.toFixed(0)} vs avg GBP${bb90Avg.toFixed(0)}`;
  } else if (compositeRound >= 7) {
    verdict = 'MAYBE';
    const concern = bbAmazonPct > 50 ? `Amazon BB ${bbAmazonPct.toFixed(0)}%` : estROI < 20 ? `ROI ${roiStr}` : `score ${compositeRound}`;
    verdictReason = `MAYBE | ${commercialSnapshot} | ${concern}`;
  } else if (estROI < 20 && compositeRound >= 5) {
    verdict = 'MAYBE-ROI';
    const maxCost = ((price - totalFees) / 1.20).toFixed(2);
    verdictReason = `MAYBE-ROI | ${commercialSnapshot} | needs trade under GBP${maxCost}`;
  } else if (fbaCount > maxSellers) {
    verdict = 'NO';
    verdictReason = `NO | ${monthlySales}/mo est | GBP${estProfit.toFixed(2)} profit | ${fbaCount} sellers | overcrowded`;
  } else if (estROI < 15) {
    verdict = 'NO';
    verdictReason = `NO | ${monthlySales}/mo est | GBP${estProfit.toFixed(2)} profit | ROI ${roiStr} | margin too thin`;
  } else {
    verdict = 'NO';
    verdictReason = `NO | ${monthlySales}/mo est | GBP${estProfit.toFixed(2)} profit | ${stabilityLabel} | ${fbaCount} sellers`;
  }

  verdictCounts[verdict] = (verdictCounts[verdict] || 0) + 1;

  let lane = 'UNASSIGNED';
  let laneReason = '';
  let commercialPriority = 9;

  const laneViable = fbaCount >= 2 && estProfit >= 0.50 && !['NO', 'PRICE EROSION', 'HAZMAT'].includes(verdict);
  const priceStable = stability >= LANE_CONFIG.min_stability_score && priceCompression !== 'COMPRESSED';
  const competitionOkay = laneName =>
    competition >= LANE_CONFIG.min_competition_score &&
    fbaCount <= (LANE_CONFIG.max_seller_count_by_lane[laneName] || 99) &&
    bbAmazonPct <= 70;

  if (laneViable) {
    const isBalanced =
      monthlySales >= LANE_CONFIG.balanced_volume_threshold &&
      estProfit >= LANE_CONFIG.balanced_profit_threshold &&
      monthlyGrossProfit >= LANE_CONFIG.balanced_monthly_profit_threshold &&
      balancedScore >= 7.0 &&
      priceStable &&
      competitionOkay('BALANCED');

    const isCashFlow =
      monthlySales >= LANE_CONFIG.cashflow_volume_threshold &&
      estProfit >= LANE_CONFIG.cashflow_min_unit_profit &&
      monthlyGrossProfit >= LANE_CONFIG.cashflow_monthly_profit_threshold &&
      cashFlowScore >= 6.0 &&
      priceStable &&
      competitionOkay('CASH FLOW');

    const isProfit =
      estProfit >= LANE_CONFIG.profit_unit_profit_threshold &&
      (estROI >= LANE_CONFIG.min_profit_roi || marginSafetyScore >= 5) &&
      profitScore >= 6.0 &&
      priceStable &&
      competitionOkay('PROFIT');

    if (isBalanced) {
      lane = 'BALANCED';
      commercialPriority = 3;
      laneReason = `BALANCED | GBP${monthlyGrossProfit.toFixed(2)}/mo | ${monthlySales}/mo | GBP${estProfit.toFixed(2)} unit profit | ${stabilityLabel} | ${fbaCount} sellers`;
    } else if (isCashFlow) {
      lane = 'CASH FLOW';
      commercialPriority = 3;
      laneReason = `CASH FLOW | GBP${monthlyGrossProfit.toFixed(2)}/mo | ${monthlySales}/mo | GBP${estProfit.toFixed(2)} unit profit | ${stabilityLabel} | ${fbaCount} sellers`;
    } else if (isProfit) {
      lane = 'PROFIT';
      commercialPriority = 3;
      laneReason = `PROFIT | GBP${monthlyGrossProfit.toFixed(2)}/mo | lower volume | GBP${estProfit.toFixed(2)} unit profit | ${stabilityLabel} | ${fbaCount} sellers`;
    } else if (monthlyGrossProfit >= 30 && priceStable && competitionOkay('CASH FLOW')) {
      lane = 'BALANCED';
      commercialPriority = 3;
      laneReason = `BALANCED | GBP${monthlyGrossProfit.toFixed(2)}/mo | ${monthlySales}/mo | GBP${estProfit.toFixed(2)} unit profit | ${stabilityLabel} | ${fbaCount} sellers`;
    } else if (monthlySales >= 10 && estProfit >= 0.50 && priceStable) {
      lane = 'CASH FLOW';
      commercialPriority = 3;
      laneReason = `CASH FLOW | GBP${monthlyGrossProfit.toFixed(2)}/mo | ${monthlySales}/mo | GBP${estProfit.toFixed(2)} unit profit | ${stabilityLabel} | ${fbaCount} sellers`;
    } else if (verdict !== 'NO') {
      commercialPriority = 4;
      laneReason = 'UNASSIGNED | below lane thresholds';
    }
  }

  if (lane !== 'UNASSIGNED') {
    const laneLeadScore =
      lane === 'BALANCED' ? balancedScore :
      lane === 'CASH FLOW' ? cashFlowScore :
      profitScore;

    if (monthlyGrossProfit >= 200 && laneLeadScore >= 7.5) commercialPriority = 1;
    else if (monthlyGrossProfit >= 100 && laneLeadScore >= 6.5) commercialPriority = 2;
    else commercialPriority = 3;
  }

  if (lane === 'BALANCED') laneBalanced += 1;
  else if (lane === 'PROFIT') laneProfit += 1;
  else if (lane === 'CASH FLOW') laneCashFlow += 1;
  else laneUnassigned += 1;

  if (lane !== 'UNASSIGNED' && !verdictReason.startsWith(`${lane} |`)) {
    verdictReason = `${lane} | ${commercialSnapshot}`;
  }

  const scoredRow = [
    ...row,
    demand,
    stability,
    competition,
    margin,
    compositeRound,
    cashFlowScore,
    profitScore,
    balancedScore,
    lane,
    commercialPriority,
    monthlyGrossProfit.toFixed(2),
    priceCompression,
    laneReason,
    verdict,
    verdictReason
  ];
  allScored.push(scoredRow);

  if (['YES', 'MAYBE', 'MAYBE-ROI', 'BRAND APPROACH', 'BUY THE DIP', 'GATED'].includes(verdict)) {
    shortlist.push(scoredRow);
  }
}

let compositeThreshold = 0;
if (shortlist.length > 100) {
  compositeThreshold = 6;
  while (shortlist.length > 100 && compositeThreshold <= 9) {
    const beforeFilter = shortlist.length;
    for (let j = shortlist.length - 1; j >= 0; j--) {
      const comp = parseFloat(shortlist[j][shortlist[j].length - 11]) || 0;
      if (comp < compositeThreshold) shortlist.splice(j, 1);
    }
    console.log(`  Threshold ${compositeThreshold}: ${beforeFilter} -> ${shortlist.length}`);
    if (shortlist.length > 100) compositeThreshold += 0.5;
  }
  console.log(`  Final composite threshold: ${compositeThreshold}`);
}

shortlist.sort((a, b) => {
  const cpA = parseInt(a[a.length - 6], 10) || 9;
  const cpB = parseInt(b[b.length - 6], 10) || 9;
  if (cpA !== cpB) return cpA - cpB;

  const laneA = laneSortValue(a[a.length - 7]);
  const laneB = laneSortValue(b[b.length - 7]);
  if (laneA !== laneB) return laneA - laneB;

  const mgpA = parseFloat(a[a.length - 5]) || 0;
  const mgpB = parseFloat(b[b.length - 5]) || 0;
  if (mgpA !== mgpB) return mgpB - mgpA;

  const salesA = estimateMonthlySales(parseFloat(a[col['Bought per Month']]) || 0, parseFloat(a[col['BSR Current']]) || 0, parseFloat(a[col['BSR Drops 90d']]) || 0);
  const salesB = estimateMonthlySales(parseFloat(b[col['Bought per Month']]) || 0, parseFloat(b[col['BSR Current']]) || 0, parseFloat(b[col['BSR Drops 90d']]) || 0);
  if (salesA !== salesB) return salesB - salesA;

  const profitA = parseFloat(a[col['Est Profit']]) || 0;
  const profitB = parseFloat(b[col['Est Profit']]) || 0;
  if (profitA !== profitB) return profitB - profitA;

  return (parseFloat(b[a.length - 11]) || 0) - (parseFloat(a[a.length - 11]) || 0);
});

const scoredHeaders = [
  ...headers,
  'Demand Score',
  'Stability Score',
  'Competition Score',
  'Margin Score',
  'Composite Score',
  'Cash Flow Score',
  'Profit Score',
  'Balanced Score',
  'Opportunity Lane',
  'Commercial Priority',
  'Monthly Gross Profit',
  'Price Compression',
  'Lane Reason',
  'Verdict',
  'Verdict Reason'
];

const scoredCSV = [scoredHeaders.map(esc).join(',')];
allScored.forEach(r => scoredCSV.push(r.map(esc).join(',')));
fs.writeFileSync(path.join(WORKING, 'kids_toys_phase3_scored.csv'), scoredCSV.join('\n') + '\n');

const shortlistCSV = [scoredHeaders.map(esc).join(',')];
shortlist.forEach(r => shortlistCSV.push(r.map(esc).join(',')));
fs.writeFileSync(path.join(WORKING, 'kids_toys_phase3_shortlist.csv'), shortlistCSV.join('\n') + '\n');

const shortlistJSON = shortlist.map(r => {
  const obj = {};
  scoredHeaders.forEach((h, i) => { obj[h] = r[i]; });
  return obj;
});
fs.writeFileSync(path.join(WORKING, 'kids_toys_phase3_shortlist.json'), JSON.stringify(shortlistJSON, null, 2));

const top5 = shortlist.slice(0, 5).map((r, i) => {
  const laneIdx = r.length - 7;
  const verdictIdx = r.length - 2;
  const mgpIdx = r.length - 5;
  return `  ${i + 1}. ${r[0]} - ${(r[1] || '').substring(0, 50)} - ${r[laneIdx] || 'UNASSIGNED'} - ${r[verdictIdx]} - GBP${r[mgpIdx]}/mo`;
}).join('\n');

const statsContent = `Niche: kids-toys
Date: ${new Date().toISOString().slice(0, 10)}
Input: ${allScored.length} products from Phase 2
Composite threshold: ${compositeThreshold > 0 ? compositeThreshold : 'default (none)'}

Lane config:
  cashflow_volume_threshold: ${LANE_CONFIG.cashflow_volume_threshold}
  cashflow_min_unit_profit: ${LANE_CONFIG.cashflow_min_unit_profit}
  cashflow_monthly_profit_threshold: ${LANE_CONFIG.cashflow_monthly_profit_threshold}
  balanced_volume_threshold: ${LANE_CONFIG.balanced_volume_threshold}
  balanced_profit_threshold: ${LANE_CONFIG.balanced_profit_threshold}
  balanced_monthly_profit_threshold: ${LANE_CONFIG.balanced_monthly_profit_threshold}
  profit_unit_profit_threshold: ${LANE_CONFIG.profit_unit_profit_threshold}
  max_seller_count_by_lane: BALANCED=${LANE_CONFIG.max_seller_count_by_lane.BALANCED}, CASH FLOW=${LANE_CONFIG.max_seller_count_by_lane['CASH FLOW']}, PROFIT=${LANE_CONFIG.max_seller_count_by_lane.PROFIT}
  amazon_risk_penalty: ${LANE_CONFIG.amazon_risk_penalty}
  price_drop_penalty: ${LANE_CONFIG.price_drop_penalty}

Verdict breakdown:
  YES:            ${verdictCounts.YES || 0}
  MAYBE:          ${verdictCounts.MAYBE || 0}
  MAYBE-ROI:      ${verdictCounts['MAYBE-ROI'] || 0}
  BRAND APPROACH: ${verdictCounts['BRAND APPROACH'] || 0}
  BUY THE DIP:    ${verdictCounts['BUY THE DIP'] || 0}
  GATED:          ${verdictCounts.GATED || 0}
  PRICE EROSION:  ${verdictCounts['PRICE EROSION'] || 0}
  NO:             ${verdictCounts.NO || 0}

Shortlist total: ${shortlist.length}

Lane breakdown:
  BALANCED:   ${laneBalanced}
  CASH FLOW:  ${laneCashFlow}
  PROFIT:     ${laneProfit}
  UNASSIGNED: ${laneUnassigned}

Top 5 by commercial priority:
${top5}
`;

fs.writeFileSync(path.join(WORKING, 'kids_toys_phase3_stats.txt'), statsContent);

console.log('Phase 3 complete:');
console.log(`  Input: ${allScored.length}`);
console.log(`  Shortlist: ${shortlist.length}`);
console.log(`  Lanes: BALANCED=${laneBalanced} CASH FLOW=${laneCashFlow} PROFIT=${laneProfit} UNASSIGNED=${laneUnassigned}`);
Object.entries(verdictCounts)
  .sort((a, b) => (VERDICT_ORDER[a[0]] || 99) - (VERDICT_ORDER[b[0]] || 99))
  .forEach(([v, c]) => console.log(`  ${v}: ${c}`));
