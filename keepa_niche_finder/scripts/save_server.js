const http = require('http');
const fs = require('fs');
const path = require('path');
const server = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(200); res.end(); return; }
  if (req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      const outPath = path.resolve(__dirname, '..', 'data', 'pet-care', 'sas_enrichment_data.csv');
      fs.writeFileSync(outPath, body, 'utf-8');
      console.log('Saved ' + body.length + ' bytes');
      res.writeHead(200); res.end('OK ' + body.length);
      setTimeout(() => process.exit(0), 1000);
    });
  }
});
server.listen(18765, '127.0.0.1', () => console.log('Listening on 18765'));
setTimeout(() => process.exit(1), 60000);
