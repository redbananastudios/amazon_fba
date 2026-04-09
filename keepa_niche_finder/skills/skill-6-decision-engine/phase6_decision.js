#!/usr/bin/env node
/**
 * Phase 6 Decision Engine template.
 *
 * Copy to:
 *   data/{niche}/working/phase6_decision.js
 *
 * Replace:
 *   const NICHE = '__NICHE__';
 *   const BASE = '__BASE__';
 *
 * Run:
 *   node data/{niche}/working/phase6_decision.js
 */

const fs = require('fs');
const path = require('path');
const ExcelJS = require('exceljs');

const NICHE = '__NICHE__';
const BASE = '__BASE__';
const WORKING = path.join(BASE, 'working');
const NICHE_SNAKE = NICHE.replace(/-/g, '_');

const INPUT = fs.existsSync(path.join(BASE, `${NICHE_SNAKE}_final_results.csv`))
  ? path.join(BASE, `${NICHE_SNAKE}_final_results.csv`)
  : path.join(WORKING, `${NICHE_SNAKE}_final_results.csv`);

const OUTPUT = path.join(WORKING, `${NICHE_SNAKE}_phase6_decisions.csv`);
const STATS = path.join(WORKING, `${NICHE_SNAKE}_phase6_stats.txt`);
const HANDOFF = path.join(WORKING, `${NICHE_SNAKE}_phase6_handoff.md`);
const SHORTLIST_XLSX = path.join(BASE, `${NICHE_SNAKE}_phase6_shortlist.xlsx`);

const DECISION_HEADERS = [
  'Decision',
  'Decision Score',
  'Decision Reason',
  'Joinability Status',
  'Buy Readiness',
  'Max Buy Price',
  'Target Buy Price',
  'Cost Gap',
  'Margin Status',
  'Action Note',
  'Shortlist Flag',
];

const CONFIG = {
  buyScore: 80,
  negotiateScore: 60,
  watchScore: 40,
  targetBuyDiscount: 0.9,
  targetBuyBuffers: {
    BALANCED: 2.0,
    'CASH FLOW': 1.5,
    PROFIT: 2.5,
    default: 1.25,
  },
  negotiateTolerance: -2.0,
  impossibleGap: -4.0,
  commerciallyStrongScore: 68,
  safeEnoughScore: 60,
};

function parseCSVLine(line) {
  const fields = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        field += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === ',' && !inQuotes) {
      fields.push(field);
      field = '';
    } else {
      field += ch;
    }
  }
  fields.push(field);
  return fields;
}

function parseCSV(text) {
  const rows = [];
  let current = '';
  let inQuotes = false;
  for (const line of text.split('\n')) {
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

function esc(val) {
  if (val === null || val === undefined) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function parseMoney(value) {
  const s = String(value || '').replace(/GBP/gi, '').replace(/[^0-9.-]/g, '').trim();
  return s ? parseFloat(s) || 0 : 0;
}

function parsePct(value) {
  const s = String(value || '').replace(/%/g, '').trim();
  return s ? parseFloat(s) || 0 : 0;
}

function gbp(value) {
  if (value === '' || value === null || value === undefined || Number.isNaN(Number(value))) return '';
  return `GBP${Number(value).toFixed(2)}`;
}

function asUpper(value) {
  return String(value || '').trim().toUpperCase();
}

function isTruthyY(value) {
  const v = asUpper(value);
  return v === 'Y' || v === 'YES' || v === 'TRUE';
}

function stableState(value) {
  const v = asUpper(value);
  if (v.includes('STABLE') || v.includes('RISING')) return 'GOOD';
  if (v.includes('SLIGHT DIP')) return 'CAUTION';
  if (v.includes('DROPPING') || v.includes('SURGING') || v.includes('COMPRESSED')) return 'BAD';
  return 'UNKNOWN';
}

function laneBaseScore(lane) {
  const l = asUpper(lane);
  if (l === 'BALANCED') return 92;
  if (l === 'CASH FLOW') return 84;
  if (l === 'PROFIT') return 78;
  return 42;
}

function scoreFromPriority(priority) {
  return clamp(100 - (Math.max(1, Number(priority) || 8) - 1) * 12, 40, 100);
}

function scoreFromMonthlyGross(value) {
  return clamp((Number(value) || 0) / 6, 0, 100);
}

function scoreFromBought(value) {
  return clamp((Number(value) || 0) * 1.25, 0, 100);
}

function scoreFromUnitProfit(value) {
  return clamp((Number(value) || 0) * 12, 0, 100);
}

function scoreFromRoi(value) {
  return clamp((Number(value) || 0) * 2.2, 0, 100);
}

function riskBandScore(band) {
  const v = asUpper(band);
  if (v === 'LOW') return 92;
  if (v === 'MEDIUM') return 55;
  if (v === 'HIGH') return 8;
  return 50;
}

function plRiskScore(risk) {
  const v = asUpper(risk);
  if (v === 'UNLIKELY' || v === 'LOW') return 88;
  if (v === 'LIKELY' || v === 'HIGH') return 22;
  return 55;
}

function routeScore(routeCode) {
  const v = asUpper(routeCode);
  if (!v || v === 'UNCLEAR') return 30;
  return 78;
}

function getTargetBuffer(lane) {
  const key = asUpper(lane);
  return CONFIG.targetBuyBuffers[key] || CONFIG.targetBuyBuffers.default;
}

function calcTargetBuyPrice(maxBuyPrice, lane) {
  if (!maxBuyPrice || maxBuyPrice <= 0) return '';
  const discountPrice = maxBuyPrice * CONFIG.targetBuyDiscount;
  const bufferPrice = maxBuyPrice - getTargetBuffer(lane);
  return Math.max(0, Math.min(discountPrice, bufferPrice));
}

function calcJoinability(row) {
  const ipRisk = asUpper(row['IP Risk Band']);
  const fortress = asUpper(row['Fortress Listing']);
  const brandSeller = asUpper(row['Brand Seller Match']);
  const brandType = asUpper(row['Brand Type']);
  const brandStore = asUpper(row['Brand Store Present']);
  const plRisk = asUpper(row['Private Label Risk']);
  const gated = isTruthyY(row['Gated']);

  if (ipRisk === 'HIGH') return 'Unsafe';
  if (fortress === 'YES' && (brandSeller === 'YES' || brandSeller === 'PARTIAL')) return 'Unsafe';
  if (brandType === 'ESTABLISHED' && brandSeller === 'YES' && brandStore === 'LIKELY') return 'Unsafe';
  if (ipRisk === 'MEDIUM' || plRisk === 'LIKELY' || gated || fortress === 'YES') return 'Review';
  return 'Joinable';
}

function calcMarginStatus(actualSupplierPrice, targetBuyPrice, maxBuyPrice, estProfit, estRoi) {
  if (actualSupplierPrice > 0) {
    if (targetBuyPrice !== '' && actualSupplierPrice <= targetBuyPrice) return 'Safe';
    if (maxBuyPrice !== '' && actualSupplierPrice <= maxBuyPrice) return 'Tight';
    return 'Fail';
  }
  if ((Number(maxBuyPrice) || 0) <= 0) return 'Unknown';
  if ((Number(estProfit) || 0) < 0.75 || (Number(estRoi) || 0) < 10) return 'Fail';
  return 'Unknown';
}

function calcBuyReadiness(joinability, marginStatus, hasSupplierCost, commerciallyStrong, gated) {
  if (joinability === 'Unsafe' || marginStatus === 'Fail') return 'Reject';
  if (!hasSupplierCost) return commerciallyStrong ? 'Cost Needed' : 'Review Needed';
  if (joinability === 'Review' || gated) return 'Review Needed';
  return 'Ready';
}

function calcActionNote(decision, buyReadiness, stabilityState, hasSupplierCost) {
  if (decision === 'BUY') return 'Place opening order';
  if (decision === 'NEGOTIATE' && !hasSupplierCost) return 'Contact supplier for cost';
  if (decision === 'NEGOTIATE') return 'Negotiate lower trade price';
  if (decision === 'WATCH' && buyReadiness === 'Review Needed') return 'Review listing safety';
  if (decision === 'WATCH' && stabilityState === 'BAD') return 'Monitor for 7 days';
  if (decision === 'KILL' && (buyReadiness === 'Reject' || buyReadiness === 'Review Needed')) return 'Avoid listing';
  return 'Review manually';
}

function buildDecisionReason(decision, lane, ipRiskBand, plRisk, priceStability, monthlyGrossProfit, extra) {
  const parts = [
    decision,
    lane || 'UNASSIGNED',
    `${gbp(monthlyGrossProfit)}/mo`,
    `${String(ipRiskBand || 'Unknown').toLowerCase()} IP risk`,
    `${String(plRisk || 'Unknown').toLowerCase()} PL risk`,
    String(priceStability || 'unknown').toLowerCase(),
  ];
  if (extra) parts.push(extra);
  return parts.join(' | ');
}

function countBy(rows, key) {
  const out = {};
  for (const row of rows) {
    const value = row[key] || '-';
    out[value] = (out[value] || 0) + 1;
  }
  return out;
}

async function buildShortlist(shortlistRows, allRows) {
  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'Phase 6 Decision Engine';
  workbook.created = new Date();

  const shortlistSheet = workbook.addWorksheet('Shortlist');
  shortlistSheet.columns = [
    { header: 'ASIN', key: 'ASIN', width: 14 },
    { header: 'Product Name', key: 'Product Name', width: 42 },
    { header: 'Brand', key: 'Brand', width: 18 },
    { header: 'Opportunity Lane', key: 'Opportunity Lane', width: 16 },
    { header: 'Monthly Gross Profit', key: 'Monthly Gross Profit', width: 18 },
    { header: 'Est Profit', key: 'Est Profit', width: 12 },
    { header: 'IP Risk Band', key: 'IP Risk Band', width: 12 },
    { header: 'Private Label Risk', key: 'Private Label Risk', width: 16 },
    { header: 'Decision', key: 'Decision', width: 14 },
    { header: 'Decision Score', key: 'Decision Score', width: 14 },
    { header: 'Max Buy Price', key: 'Max Buy Price', width: 14 },
    { header: 'Target Buy Price', key: 'Target Buy Price', width: 16 },
    { header: 'Supplier Price', key: 'Supplier Price', width: 14 },
    { header: 'Cost Gap', key: 'Cost Gap', width: 14 },
    { header: 'Decision Reason', key: 'Decision Reason', width: 54 },
    { header: 'Action Note', key: 'Action Note', width: 24 },
  ];
  shortlistRows.forEach((row) => shortlistSheet.addRow(row));
  shortlistSheet.getRow(1).font = { bold: true };
  shortlistSheet.views = [{ state: 'frozen', ySplit: 1 }];

  const summarySheet = workbook.addWorksheet('Summary');
  let cursor = 1;

  function writeSummaryBlock(title, counts) {
    summarySheet.getCell(cursor, 1).value = title;
    summarySheet.getCell(cursor, 1).font = { bold: true };
    cursor++;
    summarySheet.getCell(cursor, 1).value = 'Value';
    summarySheet.getCell(cursor, 2).value = 'Count';
    summarySheet.getRow(cursor).font = { bold: true };
    cursor++;
    for (const [key, value] of Object.entries(counts)) {
      summarySheet.getCell(cursor, 1).value = key;
      summarySheet.getCell(cursor, 2).value = value;
      cursor++;
    }
    cursor++;
  }

  writeSummaryBlock('Decision', countBy(allRows, 'Decision'));
  writeSummaryBlock('Opportunity Lane', countBy(allRows, 'Opportunity Lane'));
  writeSummaryBlock('IP Risk Band', countBy(allRows, 'IP Risk Band'));
  writeSummaryBlock('Private Label Risk', countBy(allRows, 'Private Label Risk'));

  summarySheet.columns = [
    { width: 24 },
    { width: 12 },
  ];

  await workbook.xlsx.writeFile(SHORTLIST_XLSX);
}

function main() {
  if (!fs.existsSync(INPUT)) {
    throw new Error(`Phase 5 final results CSV not found: ${INPUT}`);
  }

  const raw = fs.readFileSync(INPUT, 'utf-8');
  const rows = parseCSV(raw);
  const headers = rows[0];
  const data = rows.slice(1);
  const index = {};
  headers.forEach((header, i) => {
    index[header] = i;
  });

  function get(row, name) {
    return index[name] === undefined ? '' : (row[index[name]] || '').trim();
  }

  const outRows = [];
  const shortlistRows = [];

  for (const row of data) {
    const product = {};
    headers.forEach((header, i) => {
      product[header] = row[i] || '';
    });

    const lane = get(row, 'Opportunity Lane');
    const monthlyGrossProfit = parseMoney(get(row, 'Monthly Gross Profit'));
    const boughtPerMonth = parseMoney(get(row, 'Bought per Month'));
    const estProfit = parseMoney(get(row, 'Est Profit'));
    const estRoi = parsePct(get(row, 'Est ROI %'));
    const realRoi = parsePct(get(row, 'Real ROI %'));
    const priority = parseMoney(get(row, 'Commercial Priority'));
    const maxBuyPrice = parseMoney(get(row, 'Max Cost 20% ROI'));
    const tradePrice = parseMoney(get(row, 'Trade Price'));
    const tradePriceFound = isTruthyY(get(row, 'Trade Price Found'));
    const ipRiskBand = get(row, 'IP Risk Band');
    const plRisk = get(row, 'Private Label Risk');
    const priceStability = get(row, 'Price Stability');
    const routeCode = get(row, 'Route Code');
    const gated = isTruthyY(get(row, 'Gated'));

    const hasSupplierCost = tradePriceFound && tradePrice > 0;
    const targetBuyPrice = calcTargetBuyPrice(maxBuyPrice, lane);
    const costGap = hasSupplierCost ? maxBuyPrice - tradePrice : '';
    const stabilityState = stableState(priceStability);
    const joinability = calcJoinability(product);

    const commercialScore = clamp(
      laneBaseScore(lane) * 0.35 +
      scoreFromMonthlyGross(monthlyGrossProfit) * 0.25 +
      scoreFromBought(boughtPerMonth) * 0.20 +
      scoreFromUnitProfit(estProfit) * 0.10 +
      scoreFromPriority(priority) * 0.10,
      0,
      100
    );

    const commerciallyStrong = commercialScore >= CONFIG.commerciallyStrongScore;

    const marginStatus = calcMarginStatus(tradePrice, targetBuyPrice, maxBuyPrice, estProfit, estRoi);
    const buyReadiness = calcBuyReadiness(joinability, marginStatus, hasSupplierCost, commerciallyStrong, gated);

    let feasibilityBase = hasSupplierCost
      ? clamp(((costGap + 5) / 10) * 100, 0, 100)
      : (commerciallyStrong ? 52 : 36);

    if (marginStatus === 'Safe') feasibilityBase += 10;
    if (marginStatus === 'Tight') feasibilityBase += 2;

    const feasibilityScore = clamp(
      feasibilityBase * 0.55 +
      routeScore(routeCode) * 0.20 +
      (buyReadiness === 'Ready' ? 95 : buyReadiness === 'Cost Needed' ? 55 : buyReadiness === 'Review Needed' ? 45 : 10) * 0.25,
      0,
      100
    );

    const safetyScore = clamp(
      riskBandScore(ipRiskBand) * 0.45 +
      plRiskScore(plRisk) * 0.25 +
      (joinability === 'Joinable' ? 95 : joinability === 'Review' ? 55 : 5) * 0.30,
      0,
      100
    );

    const marginSafetyScore = clamp(
      (marginStatus === 'Safe' ? 95 : marginStatus === 'Tight' ? 60 : marginStatus === 'Unknown' ? 50 : 5) * 0.45 +
      scoreFromRoi(realRoi || estRoi) * 0.30 +
      scoreFromUnitProfit(estProfit) * 0.25,
      0,
      100
    );

    const safeEnough = safetyScore >= CONFIG.safeEnoughScore;
    let decisionScore = clamp(
      commercialScore * 0.35 +
      feasibilityScore * 0.25 +
      safetyScore * 0.25 +
      marginSafetyScore * 0.15,
      0,
      100
    );

    let decision = '';
    const ipRisk = asUpper(ipRiskBand);
    const plRiskUpper = asUpper(plRisk);
    const laneUpper = asUpper(lane);

    if (
      ipRisk === 'HIGH' ||
      joinability === 'Unsafe' ||
      marginStatus === 'Fail' ||
      (hasSupplierCost && costGap < CONFIG.impossibleGap)
    ) {
      decision = 'KILL';
      decisionScore = Math.min(decisionScore, 35);
    } else if (
      commerciallyStrong &&
      safeEnough &&
      hasSupplierCost &&
      marginStatus === 'Safe' &&
      joinability === 'Joinable' &&
      ipRisk === 'LOW' &&
      plRiskUpper !== 'LIKELY'
    ) {
      decision = 'BUY';
      decisionScore = Math.max(decisionScore, CONFIG.buyScore);
    } else if (
      (ipRisk === 'MEDIUM' && !hasSupplierCost) ||
      (plRiskUpper === '-' && joinability !== 'Joinable') ||
      stabilityState === 'BAD'
    ) {
      decision = 'WATCH';
      decisionScore = Math.min(Math.max(decisionScore, CONFIG.watchScore), CONFIG.negotiateScore - 1);
    } else if (
      commerciallyStrong &&
      safeEnough &&
      (!hasSupplierCost ||
        marginStatus === 'Tight' ||
        (hasSupplierCost && costGap < 0 && costGap >= CONFIG.negotiateTolerance))
    ) {
      decision = 'NEGOTIATE';
      decisionScore = Math.max(Math.min(decisionScore, CONFIG.buyScore - 1), CONFIG.negotiateScore);
    } else if (decisionScore >= CONFIG.buyScore) {
      decision = hasSupplierCost ? 'BUY' : 'NEGOTIATE';
    } else if (decisionScore >= CONFIG.negotiateScore) {
      decision = 'NEGOTIATE';
    } else if (decisionScore >= CONFIG.watchScore) {
      decision = 'WATCH';
    } else {
      decision = 'KILL';
    }

    if (decision === 'BUY' && buyReadiness !== 'Ready') {
      decision = hasSupplierCost ? 'NEGOTIATE' : 'WATCH';
    }

    if (decision === 'BUY' && laneUpper !== 'BALANCED' && laneUpper !== 'CASH FLOW') {
      decision = 'NEGOTIATE';
    }

    const extraReason = hasSupplierCost
      ? `cost gap ${costGap >= 0 ? '+' : ''}${gbp(costGap)}`
      : 'supplier cost missing';
    const decisionReason = buildDecisionReason(
      decision,
      lane,
      ipRiskBand,
      plRisk,
      priceStability,
      monthlyGrossProfit,
      extraReason
    );

    const actionNote = calcActionNote(decision, buyReadiness, stabilityState, hasSupplierCost);
    const shortlistFlag = decision === 'BUY' || decision === 'NEGOTIATE' ? 'Y' : 'N';

    const appended = [
      decision,
      String(Math.round(decisionScore)),
      decisionReason,
      joinability,
      buyReadiness,
      maxBuyPrice > 0 ? gbp(maxBuyPrice) : '',
      targetBuyPrice !== '' ? gbp(targetBuyPrice) : '',
      hasSupplierCost ? gbp(costGap) : '',
      marginStatus,
      actionNote,
      shortlistFlag,
    ];

    outRows.push([...row, ...appended]);

    if (shortlistFlag === 'Y') {
      shortlistRows.push({
        'ASIN': get(row, 'ASIN'),
        'Product Name': get(row, 'Product Name'),
        'Brand': get(row, 'Brand'),
        'Opportunity Lane': lane,
        'Monthly Gross Profit': gbp(monthlyGrossProfit),
        'Est Profit': gbp(estProfit),
        'IP Risk Band': ipRiskBand,
        'Private Label Risk': plRisk,
        'Decision': decision,
        'Decision Score': Math.round(decisionScore),
        'Max Buy Price': maxBuyPrice > 0 ? gbp(maxBuyPrice) : '',
        'Target Buy Price': targetBuyPrice !== '' ? gbp(targetBuyPrice) : '',
        'Supplier Price': hasSupplierCost ? gbp(tradePrice) : '',
        'Cost Gap': hasSupplierCost ? gbp(costGap) : '',
        'Decision Reason': decisionReason,
        'Action Note': actionNote,
      });
    }
  }

  outRows.sort((a, b) => {
    const aScore = parseFloat(a[headers.length + 1]) || 0;
    const bScore = parseFloat(b[headers.length + 1]) || 0;
    if (aScore !== bScore) return bScore - aScore;
    const aGross = parseMoney(a[index['Monthly Gross Profit']]);
    const bGross = parseMoney(b[index['Monthly Gross Profit']]);
    if (aGross !== bGross) return bGross - aGross;
    return String(a[index['ASIN']] || '').localeCompare(String(b[index['ASIN']] || ''));
  });

  const decisionCsv = [[...headers, ...DECISION_HEADERS].map(esc).join(',')];
  outRows.forEach((row) => decisionCsv.push(row.map(esc).join(',')));
  fs.writeFileSync(OUTPUT, `${decisionCsv.join('\n')}\n`);

  const flatRows = outRows.map((row) => {
    const obj = {};
    [...headers, ...DECISION_HEADERS].forEach((header, i) => {
      obj[header] = row[i] || '';
    });
    return obj;
  });

  buildShortlist(shortlistRows, flatRows)
    .then(() => {

      const decisionCounts = countBy(flatRows, 'Decision');
      const laneCounts = countBy(flatRows, 'Opportunity Lane');
      const ipCounts = countBy(flatRows, 'IP Risk Band');
      const plCounts = countBy(flatRows, 'Private Label Risk');

      const top15 = flatRows
        .slice()
        .sort((a, b) => (parseFloat(b['Decision Score']) || 0) - (parseFloat(a['Decision Score']) || 0))
        .slice(0, 15);

      const stats = [
        `Niche: ${NICHE}`,
        `Date: ${new Date().toISOString().slice(0, 10)}`,
        `Input: ${data.length} products from Phase 5 final results`,
        '',
        'Decision distribution:',
        `  BUY: ${decisionCounts['BUY'] || 0}`,
        `  NEGOTIATE: ${decisionCounts['NEGOTIATE'] || 0}`,
        `  WATCH: ${decisionCounts['WATCH'] || 0}`,
        `  KILL: ${decisionCounts['KILL'] || 0}`,
        '',
        'Opportunity Lane:',
        ...Object.entries(laneCounts).map(([k, v]) => `  ${k}: ${v}`),
        '',
        'IP Risk Band:',
        ...Object.entries(ipCounts).map(([k, v]) => `  ${k}: ${v}`),
        '',
        'Private Label Risk:',
        ...Object.entries(plCounts).map(([k, v]) => `  ${k}: ${v}`),
        '',
        `Shortlist rows: ${shortlistRows.length}`,
        '',
        'Top 15 by Decision Score:',
        ...top15.map((row, i) => `  ${i + 1}. ${row['ASIN']} | ${row['Decision']} | ${row['Decision Score']} | ${row['Decision Reason']}`),
      ].join('\n');
      fs.writeFileSync(STATS, `${stats}\n`);

      const handoff = [
        `# Phase 6 Handoff -- ${NICHE}`,
        '',
        `Generated: ${new Date().toISOString().slice(0, 10)}`,
        '',
        'Outputs:',
        `- ${OUTPUT}`,
        `- ${STATS}`,
        `- ${HANDOFF}`,
        `- ${SHORTLIST_XLSX}`,
        '',
        'Decision counts:',
        `- BUY: ${decisionCounts['BUY'] || 0}`,
        `- NEGOTIATE: ${decisionCounts['NEGOTIATE'] || 0}`,
        `- WATCH: ${decisionCounts['WATCH'] || 0}`,
        `- KILL: ${decisionCounts['KILL'] || 0}`,
        '',
        'Next step:',
        '- Review the shortlist workbook first, then use the full decision CSV for audit detail.',
      ].join('\n');
      fs.writeFileSync(HANDOFF, `${handoff}\n`);

      console.log(`Phase 6 complete for ${NICHE}`);
      console.log(`Decision CSV: ${OUTPUT}`);
      console.log(`Shortlist XLSX: ${SHORTLIST_XLSX}`);
      console.log(`Stats: ${STATS}`);
      console.log(`Handoff: ${HANDOFF}`);
    })
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}

main();
