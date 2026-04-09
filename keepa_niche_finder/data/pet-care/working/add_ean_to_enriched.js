#!/usr/bin/env node
/**
 * Adds EAN/UPC/GTIN columns to pet_care_phase2_enriched.csv
 * by joining on ASIN from the raw Keepa export.
 */
const fs = require('fs');
const path = require('path');

const WORKING = path.resolve(__dirname);
const RAW = path.join(WORKING, 'pet_care_phase1_raw.csv');
const ENRICHED = path.join(WORKING, 'pet_care_phase2_enriched.csv');

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
  if (!val && val !== 0) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

// Build ASIN -> EAN/UPC/GTIN lookup from raw
const rawData = fs.readFileSync(RAW, 'utf-8').replace(/^\uFEFF/, '');
const rawLines = rawData.split('\n').filter(l => l.trim());
const rawHeaders = parseCSVLine(rawLines[0]);
const rawCol = {};
rawHeaders.forEach((h, i) => { rawCol[h] = i; });

const lookup = {};
for (let i = 1; i < rawLines.length; i++) {
  const row = parseCSVLine(rawLines[i]);
  const asin = (row[rawCol['ASIN']] || '').trim();
  if (asin) {
    lookup[asin] = {
      ean: (row[rawCol['Product Codes: EAN']] || '').trim(),
      upc: (row[rawCol['Product Codes: UPC']] || '').trim(),
      gtin: (row[rawCol['Product Codes: GTIN']] || '').trim()
    };
  }
}

// Read enriched and add columns
const enrichedData = fs.readFileSync(ENRICHED, 'utf-8').replace(/^\uFEFF/, '');
const enrichedLines = enrichedData.split('\n').filter(l => l.trim());
const enrichedHeaders = parseCSVLine(enrichedLines[0]);

// Check if already has EAN
if (enrichedHeaders.includes('EAN')) {
  console.log('EAN column already exists, skipping');
  process.exit(0);
}

const output = [enrichedHeaders.map(esc).join(',') + ',EAN,UPC,GTIN'];
let matched = 0;
for (let i = 1; i < enrichedLines.length; i++) {
  const row = parseCSVLine(enrichedLines[i]);
  const asin = (row[0] || '').trim();
  const codes = lookup[asin] || { ean: '', upc: '', gtin: '' };
  if (codes.ean || codes.upc || codes.gtin) matched++;
  output.push(row.map(esc).join(',') + ',' + esc(codes.ean) + ',' + esc(codes.upc) + ',' + esc(codes.gtin));
}

fs.writeFileSync(ENRICHED, output.join('\n') + '\n');
console.log(`Added EAN/UPC/GTIN to ${enrichedLines.length - 1} products (${matched} with codes)`);
