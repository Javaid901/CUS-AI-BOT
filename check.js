const fs = require('fs');
let c = fs.readFileSync('frontend/js/chatbot.js', 'utf-8');

// Check for actual ???? (4 question marks) sequence
const idxQQQQ = c.indexOf('????');
console.log('???? found at index:', idxQQQQ);

if (idxQQQQ >= 0) {
  console.log('Context around ???? :', JSON.stringify(c.substring(idxQQQQ-30, idxQQQQ+30)));
}

// Check for ï¿½ pattern
const idxI = c.indexOf('\u00ef\u00bf\u00b0');
console.log('\ï¿½ found at index:', idxI);

if (idxI >= 0) {
  console.log('Context around ï¿½ :', JSON.stringify(c.substring(idxI-30, idxI+30)));
}

// Check for emoji starting with ðŸ
const regex = /ðŸ[a-zA-Z]/g;
const matches = c.match(regex);
if (matches) {
  const unique = [...new Set(matches)];
  console.log('\nðŸ patterns found:', unique.length);
  for (const m of unique) {
    const idx = c.indexOf(m);
    console.log('  ' + m + ' at index ' + idx);
  }
}

// Check renderDetail fields iteration
const detailMatch = c.match(/function renderDetail[\s\S]*?^\}/m);
if (detailMatch) {
  const f = detailMatch[0];
  console.log('\nrenderDetail has fields.forEach: ', f.includes('fields.forEach'));
  console.log('renderDetail has table: ', f.includes('<table'));
  console.log('renderDetail has message: ', f.includes('payload.message'));
}

// Check for dtbl table in entire file
console.log('\nHas dtbl table (global): ', c.includes('<table class="dtbl")'));

// Show the fields.forEach context
const fieldsIdx = c.indexOf('fields.forEach');
if (fieldsIdx >= 0) {
  console.log('\nfields.forEach found at index', fieldsIdx);
  console.log('Context:', c.substring(fieldsIdx-30, fieldsIdx+100));
}