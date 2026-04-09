#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const templatePath = path.resolve(__dirname, '..', '..', '..', 'skills', 'skill-6-decision-engine', 'phase6_decision.js');
let source = fs.readFileSync(templatePath, 'utf8');
source = source.replace(/^#!.*\r?\n/, '');
source = source.replace(/const NICHE = '__NICHE__';/g, "const NICHE = 'educational-toys';");
source = source.replace(/const BASE = '__BASE__';/g, "const BASE = '" + path.resolve(__dirname, "..").replace(/\/g, "\\\\") + "';");
new Function('require', 'console', 'process', '__filename', '__dirname', source)(
  require,
  console,
  process,
  templatePath,
  path.dirname(templatePath)
);
