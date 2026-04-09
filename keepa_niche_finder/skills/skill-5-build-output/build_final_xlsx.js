#!/usr/bin/env node
/**
 * build_final_xlsx.js
 * Converts {niche}_final_results.csv into a styled .xlsx workbook.
 *
 * Usage:  node build_final_xlsx.js --niche pet-care
 *         node build_final_xlsx.js --csv path/to/final.csv --out path/to/output.xlsx
 */

const ExcelJS = require('exceljs');
const fs = require('fs');
const path = require('path');

// CLI args
const args = process.argv.slice(2);
function flag(name) {
  const i = args.indexOf('--' + name);
  return i !== -1 ? args[i + 1] : null;
}

const niche = flag('niche');
// Resolve project data dir relative to this script (skills/skill-5-build-output/ -> project root/data)
const BASE = path.resolve(__dirname, '..', '..', 'data');
const nicheSnake = niche.replace(/-/g, '_');
const csvName = `${nicheSnake}_final_results.csv`;
let csvPath = flag('csv') || path.join(BASE, niche, csvName);
if (!fs.existsSync(csvPath)) {
  csvPath = path.join(BASE, niche, 'working', csvName);
}
const outPath = flag('out') || path.join(BASE, niche, `${nicheSnake}_final_results.xlsx`);

if (!fs.existsSync(csvPath)) {
  console.error(`CSV not found in data/${niche}/ or data/${niche}/working/`);
  process.exit(1);
}

// Parse CSV (handles quoted fields with commas)
function parseCSV(text) {
  const rows = [];
  let current = '';
  let inQuotes = false;
  const lines = text.split('\n');

  for (const line of lines) {
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

function parseCSVLine(line) {
  const fields = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') { field += '"'; i++; }
      else inQuotes = !inQuotes;
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

// Colours
const COLOURS = {
  headerBg:     'FF1B2A4A',  // dark navy
  headerFont:   'FFFFFFFF',
  yesGreen:     'FFD5F5E3',
  maybeyellow:  'FFFEF9E7',
  noRed:        'FFFADBD8',
  brandBlue:    'FFD6EAF8',
  dipOrange:    'FFFDEBD0',
  erosionRed:   'FFF5B7B1',
  gatedPurple:  'FFE8DAEF',
  zebraEven:    'FFF8F9FA',
  borderGrey:   'FFD5D8DC',
  greenText:    'FF27AE60',
  redText:      'FFC0392B',
  linkBlue:     'FF2980B9',
  scoreBgHigh:  'FF82E0AA',
  scoreBgMid:   'FFF9E79F',
  scoreBgLow:   'FFF1948A',
};

function verdictFill(verdict) {
  if (!verdict) return null;
  const v = verdict.toUpperCase();
  if (v === 'YES') return COLOURS.yesGreen;
  if (v.startsWith('MAYBE')) return COLOURS.maybeyellow;
  if (v === 'BRAND APPROACH') return COLOURS.brandBlue;
  if (v === 'BUY THE DIP') return COLOURS.dipOrange;
  if (v === 'PRICE EROSION') return COLOURS.erosionRed;
  if (v === 'GATED') return COLOURS.gatedPurple;
  if (v === 'NO') return COLOURS.noRed;
  return null;
}

function scoreFill(val) {
  const n = parseFloat(val);
  if (isNaN(n)) return null;
  if (n >= 8) return COLOURS.scoreBgHigh;
  if (n >= 5) return COLOURS.scoreBgMid;
  return COLOURS.scoreBgLow;
}

// Column config
// Group headers for merged cells at top
const GROUPS = [
  { label: 'Product', start: 1, end: 6 },
  { label: 'Verdict & Scores', start: 7, end: 22 },
  { label: 'Pricing & Margins', start: 23, end: 33 },
  { label: 'Demand, Competition & Risk', start: 34, end: 54 },
  { label: 'Supplier & Sourcing', start: 55, end: 64 },
];

// Column widths (by 1-based index) -- 64 columns
const COL_WIDTHS = {
  1: 14,   // ASIN
  2: 45,   // Product Name
  3: 18,   // Brand
  4: 36,   // Amazon URL
  5: 14,   // Category
  6: 12,   // Weight Flag
  7: 16,   // Verdict
  8: 40,   // Verdict Reason
  9: 16,   // Opportunity Lane
  10: 8,   // Commercial Priority
  11: 40,  // Lane Reason
  12: 10,  // Composite
  13: 8,   // Demand
  14: 8,   // Stability
  15: 8,   // Competition
  16: 8,   // Margin
  17: 8,   // Cash Flow Score
  18: 8,   // Profit Score
  19: 8,   // Balanced Score
  20: 14,  // Monthly Gross Profit
  21: 14,  // Price Compression
  22: 14,  // Listing Quality
  23: 12,  // Price
  24: 12,  // BB 90d avg
  25: 14,  // Price Stability
  26: 12,  // Fulfilment Fee
  27: 12,  // Amazon Fees (referral+per-item+DSF)
  28: 14,  // Total Amazon Fees
  29: 12,  // Est Cost
  30: 10,  // Est Profit
  31: 10,  // Est ROI%
  32: 14,  // Max Cost
  33: 12,  // Breakeven
  34: 10,  // BSR
  35: 10,  // BSR Drops
  36: 12,  // Bought/mo
  37: 10,  // Star Rating
  38: 12,  // Review Count
  39: 8,   // Brand 1P
  40: 10,  // Sellers
  41: 10,  // Amazon
  42: 12,  // BB Share
  43: 10,  // PL Risk
  44: 14,  // Brand Seller Match
  45: 14,  // Fortress Listing
  46: 14,  // Brand Type
  47: 14,  // A+ Content Present
  48: 14,  // Brand Store Present
  49: 14,  // Category Risk Level
  50: 10,  // IP Risk Score
  51: 10,  // IP Risk Band
  52: 36,  // IP Reason
  53: 8,   // Gated
  54: 14,  // SAS Flags
  55: 14,  // Route
  56: 22,  // Supplier
  57: 22,  // Website
  58: 22,  // Contact
  59: 12,  // MOQ
  60: 10,  // Trade Found
  61: 12,  // Trade Price
  62: 10,  // Real ROI
  63: 30,  // Notes
  64: 18,  // Outreach
};

// Numeric columns (1-based) -- format as numbers (64 cols)
const NUMERIC_COLS = new Set([10,12,13,14,15,16,17,18,19,20,23,24,26,27,28,29,30,31,32,33,34,35,36,37,38,50,62]);

// Percentage columns
const PCT_COLS = new Set([31, 62]);

// GBP columns
const GBP_COLS = new Set([20, 23, 24, 26, 27, 28, 29, 30, 32, 33, 61]);

// Build workbook
async function build() {
  const raw = fs.readFileSync(csvPath, 'utf-8');
  const rows = parseCSV(raw);
  const headers = rows[0];
  const data = rows.slice(1);

  const wb = new ExcelJS.Workbook();
  wb.creator = 'FBA Sourcing Pipeline';
  wb.created = new Date();

  const ws = wb.addWorksheet('Results', {
    views: [{ state: 'frozen', xSplit: 3, ySplit: 3 }],  // freeze first 3 cols + 3 rows
  });

  // Row 1: Title
  const nicheLabel = niche ? niche.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Niche';
  ws.mergeCells(1, 1, 1, 64);
  const titleCell = ws.getCell(1, 1);
  titleCell.value = `${nicheLabel} -- Final Results (${data.length} products)  |  Generated ${new Date().toISOString().slice(0, 10)}`;
  titleCell.font = { bold: true, size: 14, color: { argb: 'FFFFFFFF' } };
  titleCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.headerBg } };
  titleCell.alignment = { horizontal: 'left', vertical: 'middle' };
  ws.getRow(1).height = 30;

  // Row 2: Group headers
  for (const g of GROUPS) {
    ws.mergeCells(2, g.start, 2, g.end);
    const cell = ws.getCell(2, g.start);
    cell.value = g.label;
    cell.font = { bold: true, size: 11, color: { argb: 'FFFFFFFF' } };
    cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF2C3E50' } };
    cell.alignment = { horizontal: 'center', vertical: 'middle' };
    cell.border = {
      left: { style: 'thin', color: { argb: 'FFFFFFFF' } },
      right: { style: 'thin', color: { argb: 'FFFFFFFF' } },
    };
  }
  ws.getRow(2).height = 22;

  // Row 3: Column headers
  const headerRow = ws.getRow(3);
  headers.forEach((h, i) => {
    const cell = headerRow.getCell(i + 1);
    cell.value = h;
    cell.font = { bold: true, size: 10, color: { argb: COLOURS.headerFont } };
    cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.headerBg } };
    cell.alignment = { horizontal: 'center', vertical: 'middle', wrapText: true };
    cell.border = {
      bottom: { style: 'medium', color: { argb: COLOURS.borderGrey } },
      left: { style: 'thin', color: { argb: 'FF34495E' } },
      right: { style: 'thin', color: { argb: 'FF34495E' } },
    };
  });
  headerRow.height = 36;

  // Column widths
  for (const [col, w] of Object.entries(COL_WIDTHS)) {
    ws.getColumn(parseInt(col)).width = w;
  }

  // Data rows
  data.forEach((row, ri) => {
    const excelRow = ws.getRow(ri + 4);  // data starts at row 4
    const isEven = ri % 2 === 0;

    row.forEach((val, ci) => {
      const colIdx = ci + 1;
      const cell = excelRow.getCell(colIdx);

      // Parse numeric values
      if (NUMERIC_COLS.has(colIdx) && val !== '' && val !== '-') {
        const stripped = val.replace(/GBP/gi, '').replace(/[^0-9.-]/g, '').trim();
        const num = parseFloat(stripped);
        if (!isNaN(num)) {
          cell.value = num;
          if (PCT_COLS.has(colIdx)) {
            cell.numFmt = '0.0"%"';
          } else if (GBP_COLS.has(colIdx)) {
            cell.numFmt = '"GBP"#,##0.00';
          } else if (Number.isInteger(num)) {
            cell.numFmt = '#,##0';
          } else {
            cell.numFmt = '0.0';
          }
        } else {
          cell.value = val;
        }
      } else if (colIdx === 4 && val) {
        // Amazon URL -- make it a clickable hyperlink
        cell.value = { text: val, hyperlink: val };
        cell.font = { color: { argb: COLOURS.linkBlue }, underline: true, size: 9 };
      } else {
        cell.value = val;
      }

      // Default font
      if (colIdx !== 4) {
        cell.font = { size: 10 };
      }

      // Alignment
      if (colIdx === 2 || colIdx === 8 || colIdx === 11 || colIdx >= 46) {
        cell.alignment = { wrapText: true, vertical: 'top' };
      } else {
        cell.alignment = { horizontal: 'center', vertical: 'middle' };
      }

      // Zebra striping
      if (isEven) {
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.zebraEven } };
      }

      // Border
      cell.border = {
        bottom: { style: 'hair', color: { argb: COLOURS.borderGrey } },
        left: { style: 'hair', color: { argb: COLOURS.borderGrey } },
        right: { style: 'hair', color: { argb: COLOURS.borderGrey } },
      };
    });
    // Conditional formatting (applied per-row)
    const verdict = row[6];  // col 7 (0-indexed = 6)

    // Verdict cell colour
    const vFill = verdictFill(verdict);
    if (vFill) {
      const vc = excelRow.getCell(7);
      vc.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: vFill } };
      vc.font = { bold: true, size: 10 };
    }

    // Opportunity Lane colour -- col 9 (0-idx 8)
    const laneVal = (row[8] || '').toUpperCase();
    if (laneVal === 'BALANCED') {
      const lCell = excelRow.getCell(9);
      lCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.yesGreen } };
      lCell.font = { bold: true, size: 10 };
    } else if (laneVal === 'PROFIT') {
      const lCell = excelRow.getCell(9);
      lCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.brandBlue } };
      lCell.font = { bold: true, size: 10 };
    } else if (laneVal.includes('CASH')) {
      const lCell = excelRow.getCell(9);
      lCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      lCell.font = { bold: true, size: 10 };
    }

    // Composite score colour -- col 12 (0-idx 11)
    const compCell = excelRow.getCell(12);
    const sFill = scoreFill(row[11]);
    if (sFill) {
      compCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: sFill } };
      compCell.font = { bold: true, size: 11 };
    }

    // Price Compression -- highlight COMPRESSED in red, SQUEEZED in orange -- col 21 (0-idx 20)
    const pcVal = (row[20] || '').toUpperCase();
    if (pcVal === 'COMPRESSED') {
      const pcCell = excelRow.getCell(21);
      pcCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.noRed } };
      pcCell.font = { bold: true, size: 10 };
    } else if (pcVal === 'SQUEEZED') {
      const pcCell = excelRow.getCell(21);
      pcCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      pcCell.font = { bold: true, size: 10 };
    }

    // ROI colour (green if positive, red if negative) -- col 31 (0-idx 30)
    const roiVal = parseFloat((row[30] || '').replace('%', ''));
    if (!isNaN(roiVal)) {
      const roiCell = excelRow.getCell(31);
      roiCell.font = {
        bold: true,
        size: 10,
        color: { argb: roiVal >= 0 ? COLOURS.greenText : COLOURS.redText },
      };
    }

    // IP Risk Band colouring -- col 51 (0-idx 50)
    const ipRiskBand = (row[50] || '').toUpperCase();
    if (ipRiskBand === 'HIGH') {
      const ipCell = excelRow.getCell(51);
      ipCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.noRed } };
      ipCell.font = { bold: true, size: 10 };
    } else if (ipRiskBand === 'MEDIUM') {
      const ipCell = excelRow.getCell(51);
      ipCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      ipCell.font = { bold: true, size: 10 };
    } else if (ipRiskBand === 'LOW') {
      const ipCell = excelRow.getCell(51);
      ipCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.yesGreen } };
      ipCell.font = { bold: true, size: 10 };
    }

    // Real ROI colour -- col 62 (0-idx 61)
    const realRoi = parseFloat((row[61] || '').replace('%', ''));
    if (!isNaN(realRoi)) {
      const rrCell = excelRow.getCell(62);
      rrCell.font = {
        bold: true,
        size: 10,
        color: { argb: realRoi >= 0 ? COLOURS.greenText : COLOURS.redText },
      };
    }

    // Amazon on listing -- highlight Y in orange -- col 41 (0-idx 40)
    if ((row[40] || '').toUpperCase() === 'Y') {
      const amzCell = excelRow.getCell(41);
      amzCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      amzCell.font = { bold: true, size: 10 };
    }

    // Gated -- highlight Y in purple -- col 53 (0-idx 52)
    if ((row[52] || '').toUpperCase() === 'Y') {
      const gCell = excelRow.getCell(53);
      gCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.gatedPurple } };
      gCell.font = { bold: true, size: 10 };
    }

    // Listing Quality -- highlight WEAK in orange -- col 22 (0-idx 21)
    if ((row[21] || '').toUpperCase() === 'WEAK') {
      const lqCell = excelRow.getCell(22);
      lqCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      lqCell.font = { bold: true, size: 10 };
    }

    // Brand 1P -- highlight Y in red -- col 39 (0-idx 38)
    if ((row[38] || '').toUpperCase() === 'Y') {
      const b1pCell = excelRow.getCell(39);
      b1pCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.noRed } };
      b1pCell.font = { bold: true, size: 10 };
    }

    // Weight Flag -- highlight HEAVY/OVERSIZE in orange
    const wf = (row[5] || '').toUpperCase();
    if (wf.includes('HEAVY') || wf.includes('OVERSIZE')) {
      const wfCell = excelRow.getCell(6);
      wfCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      wfCell.font = { bold: true, size: 10 };
    }

    excelRow.height = 22;
  });
  // Auto-filter on header row
  ws.autoFilter = { from: { row: 3, column: 1 }, to: { row: data.length + 3, column: 64 } };
  // Legend sheet
  const legend = wb.addWorksheet('Legend');
  legend.getColumn(1).width = 20;
  legend.getColumn(2).width = 60;

  const legendData = [
    ['Verdict', 'Meaning'],
    ['YES', 'Composite 7+, all filters pass -- pursue this product'],
    ['MAYBE', 'Composite 5-6, one concern -- review needed'],
    ['MAYBE-ROI', 'ROI below 20% estimated -- may improve with real trade price'],
    ['BRAND APPROACH', '2-3 sellers, weak listing -- contact brand direct'],
    ['BUY THE DIP', 'Price 30%+ below 90-day avg -- recovery pattern detected'],
    ['PRICE EROSION', 'Consistent downward slope -- reject'],
    ['GATED', 'Restricted listing -- flag for ungating decision'],
    ['NO', 'Fails filter, reason stated'],
    ['', ''],
    ['Lane', 'Meaning'],
    ['BALANCED', 'Strong velocity + strong margin -- premium opportunity'],
    ['PROFIT', 'Strong unit profit/ROI, may have lower velocity -- capital efficient'],
    ['CASH FLOW', 'High velocity, acceptable margin -- turnover and cash generation'],
    ['', ''],
    ['Column', 'Explanation'],
    ['Price Compression', 'OK / SQUEEZED (price 10-20% below 90d avg) / COMPRESSED (price 20%+ below avg)'],
    ['Est Cost 65%', 'Estimated cost at 65% of selling price (rough placeholder)'],
    ['Max Cost for 20% ROI', 'Maximum you can pay for stock and still hit 20% ROI after FBA fees'],
    ['Breakeven Price', 'Minimum selling price to cover FBA fees + cost (at 65% estimate)'],
    ['Price Stability', 'STABLE / SLIGHT DIP / DROPPING / RISING / SURGING -- based on 90-day trend'],
    ['Route Code', 'EXISTING ACCOUNT / DISTRIBUTOR / BRAND DIRECT / TRADE PLATFORM / UNCLEAR'],
    ['FBA Seller Count', 'Number of FBA sellers on listing (2-20 target range)'],
    ['Amazon Buy Box Share', 'Percentage of time Amazon holds the Buy Box (flag if >70%)'],
    ['Listing Quality', 'STRONG (6+ images, A+, 5+ bullets) / AVERAGE / WEAK'],
    ['Weight Flag', 'OK / HEAVY (>5kg) / OVERSIZE (>45cm) / HEAVY+OVERSIZE'],
    ['Brand 1P', 'Y = brand sells direct on Amazon (hard to compete)'],
    ['Star Rating', 'Average customer rating 1.0-5.0'],
    ['Review Count', 'Total customer reviews (>500 = proven, <20 = risky)'],
  ];

  legendData.forEach((row, i) => {
    const r = legend.getRow(i + 1);
    r.getCell(1).value = row[0];
    r.getCell(2).value = row[1];
    if (i === 0 || i === 10 || i === 16) {
      r.getCell(1).font = { bold: true, size: 11 };
      r.getCell(2).font = { bold: true, size: 11 };
      r.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.headerBg } };
      r.getCell(2).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.headerBg } };
      r.getCell(1).font = { bold: true, size: 11, color: { argb: 'FFFFFFFF' } };
      r.getCell(2).font = { bold: true, size: 11, color: { argb: 'FFFFFFFF' } };
    }
    // Colour-code verdict rows
    if (i >= 1 && i <= 8) {
      const fill = verdictFill(row[0]);
      if (fill) {
        r.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: fill } };
        r.getCell(1).font = { bold: true, size: 10 };
      }
    }
    // Colour-code lane rows
    if (i === 11) { // BALANCED
      r.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.yesGreen } };
      r.getCell(1).font = { bold: true, size: 10 };
    } else if (i === 12) { // PROFIT
      r.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.brandBlue } };
      r.getCell(1).font = { bold: true, size: 10 };
    } else if (i === 13) { // CASH FLOW
      r.getCell(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: COLOURS.dipOrange } };
      r.getCell(1).font = { bold: true, size: 10 };
    }
  });
  // Save
  await wb.xlsx.writeFile(outPath);
  console.log(`Saved: ${outPath}`);
  console.log(`Rows: ${data.length} products`);
  console.log(`Sheets: Results + Legend`);
}

build().catch(err => { console.error(err); process.exit(1); });


