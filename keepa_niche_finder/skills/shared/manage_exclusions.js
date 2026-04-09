#!/usr/bin/env node
/**
 * manage_exclusions.js
 * Manages the ASIN exclusion list to avoid re-processing known bad products.
 *
 * Usage:
 *   Add exclusions from a scored/enriched CSV:
 *     node manage_exclusions.js --add --niche educational-toys --csv path/to/phase3_scored.csv --verdicts NO,PRICE_EROSION,HAZMAT
 *
 *   Filter a phase1 CSV against the exclusion list:
 *     node manage_exclusions.js --filter --niche educational-toys --input path/to/phase1_raw.csv --output path/to/phase1_filtered.csv
 *
 *   Show stats:
 *     node manage_exclusions.js --stats --niche educational-toys
 */

const fs = require('fs');
const path = require('path');

// Resolve project root relative to this script (skills/shared/ -> project root)
const BASE = path.resolve(__dirname, '..', '..');
const EXCLUSIONS_FILE = path.join(BASE, 'data', 'exclusions.csv');
const HEADERS = 'ASIN,Niche,Verdict,Reason,Date Added,Source Phase';

const args = process.argv.slice(2);
function flag(name) {
  const i = args.indexOf('--' + name);
  return i !== -1 ? args[i + 1] : null;
}
function hasFlag(name) {
  return args.includes('--' + name);
}

// Ensure exclusions file exists
if (!fs.existsSync(EXCLUSIONS_FILE)) {
  fs.mkdirSync(path.dirname(EXCLUSIONS_FILE), { recursive: true });
  fs.writeFileSync(EXCLUSIONS_FILE, HEADERS + '\n');
  console.log(`Created: ${EXCLUSIONS_FILE}`);
}

// ── CSV helpers ───────────────────────────────────────────────────────────
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

function parseCSV(text) {
  return text.split('\n')
    .filter(line => line.trim())
    .map(parseCSVLine);
}

function readExclusions() {
  const text = fs.readFileSync(EXCLUSIONS_FILE, 'utf-8');
  const rows = parseCSV(text);
  return rows.slice(1); // skip header
}

function escapeCSV(val) {
  if (!val) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// ── ADD mode ──────────────────────────────────────────────────────────────
if (hasFlag('add')) {
  const niche = flag('niche');
  const csvPath = flag('csv');
  const verdictsStr = flag('verdicts') || 'NO,PRICE_EROSION,HAZMAT';
  const sourcePhase = flag('phase') || 'Phase 3';

  if (!niche || !csvPath) {
    console.error('Usage: --add --niche <niche> --csv <path> [--verdicts NO,PRICE_EROSION,HAZMAT] [--phase "Phase 3"]');
    process.exit(1);
  }

  const targetVerdicts = new Set(verdictsStr.split(',').map(v => v.trim().toUpperCase().replace(/_/g, ' ')));
  const existing = new Set(readExclusions().map(r => r[0])); // existing ASINs
  const csvText = fs.readFileSync(csvPath, 'utf-8');
  const rows = parseCSV(csvText);
  const headers = rows[0].map(h => h.trim().toLowerCase());
  const data = rows.slice(1);

  const asinIdx = headers.findIndex(h => h === 'asin');
  const verdictIdx = headers.findIndex(h => h.includes('verdict') && !h.includes('reason'));
  const reasonIdx = headers.findIndex(h => h.includes('verdict reason') || h.includes('reason'));

  if (asinIdx === -1 || verdictIdx === -1) {
    console.error('CSV must have ASIN and Verdict columns');
    process.exit(1);
  }

  const date = new Date().toISOString().slice(0, 10);
  let added = 0;
  const lines = [];

  for (const row of data) {
    const asin = (row[asinIdx] || '').trim();
    const verdict = (row[verdictIdx] || '').trim().toUpperCase();
    const reason = reasonIdx !== -1 ? (row[reasonIdx] || '').trim() : '';

    if (!asin || existing.has(asin)) continue;
    if (!targetVerdicts.has(verdict)) continue;

    lines.push([asin, niche, verdict, reason, date, sourcePhase].map(escapeCSV).join(','));
    existing.add(asin);
    added++;
  }

  if (lines.length > 0) {
    fs.appendFileSync(EXCLUSIONS_FILE, lines.join('\n') + '\n');
  }

  console.log(`Added ${added} ASINs to exclusions (verdicts: ${verdictsStr})`);
  console.log(`Total exclusions: ${existing.size}`);
}

// ── FILTER mode ───────────────────────────────────────────────────────────
if (hasFlag('filter')) {
  const niche = flag('niche');
  const inputPath = flag('input');
  const outputPath = flag('output');

  if (!niche || !inputPath || !outputPath) {
    console.error('Usage: --filter --niche <niche> --input <path> --output <path>');
    process.exit(1);
  }

  // Load exclusions for this niche (and global ones with no niche)
  const exclusions = readExclusions();
  const excludedASINs = new Map(); // ASIN -> { verdict, reason }
  for (const row of exclusions) {
    const [asin, excNiche, verdict, reason] = row;
    if (excNiche === niche || excNiche === '*') {
      excludedASINs.set(asin, { verdict, reason });
    }
  }

  if (excludedASINs.size === 0) {
    console.log('No exclusions found for this niche. Copying input to output unchanged.');
    fs.copyFileSync(inputPath, outputPath);
    process.exit(0);
  }

  // Read input CSV
  const inputText = fs.readFileSync(inputPath, 'utf-8');
  const inputLines = inputText.split('\n').filter(l => l.trim());
  const header = inputLines[0];
  const headerFields = parseCSVLine(header);
  const asinIdx = headerFields.findIndex(h => h.trim().toLowerCase() === 'asin');

  if (asinIdx === -1) {
    console.error('Input CSV must have an ASIN column');
    process.exit(1);
  }

  const kept = [header];
  const removed = { total: 0, byVerdict: {} };

  for (let i = 1; i < inputLines.length; i++) {
    const fields = parseCSVLine(inputLines[i]);
    const asin = (fields[asinIdx] || '').trim();
    const exclusion = excludedASINs.get(asin);

    if (exclusion) {
      removed.total++;
      removed.byVerdict[exclusion.verdict] = (removed.byVerdict[exclusion.verdict] || 0) + 1;
    } else {
      kept.push(inputLines[i]);
    }
  }

  fs.writeFileSync(outputPath, kept.join('\n') + '\n');

  console.log(`Input: ${inputLines.length - 1} products`);
  console.log(`Excluded (previous runs): ${removed.total}`);
  for (const [verdict, count] of Object.entries(removed.byVerdict)) {
    console.log(`  ${verdict}: ${count}`);
  }
  console.log(`Output: ${kept.length - 1} products`);
  console.log(`Saved to: ${outputPath}`);
}

// ── STATS mode ────────────────────────────────────────────────────────────
if (hasFlag('stats')) {
  const niche = flag('niche');
  const exclusions = readExclusions();

  const nicheExclusions = niche
    ? exclusions.filter(r => r[1] === niche)
    : exclusions;

  const byVerdict = {};
  const byPhase = {};
  for (const row of nicheExclusions) {
    const verdict = row[2] || 'UNKNOWN';
    const phase = row[5] || 'UNKNOWN';
    byVerdict[verdict] = (byVerdict[verdict] || 0) + 1;
    byPhase[phase] = (byPhase[phase] || 0) + 1;
  }

  console.log(`Exclusion stats${niche ? ` for ${niche}` : ' (all niches)'}:`);
  console.log(`Total: ${nicheExclusions.length}`);
  console.log('By verdict:');
  for (const [v, c] of Object.entries(byVerdict)) console.log(`  ${v}: ${c}`);
  console.log('By source phase:');
  for (const [p, c] of Object.entries(byPhase)) console.log(`  ${p}: ${c}`);
}
