const fs = require('fs');
let content = fs.readFileSync('frontend/js/chatbot.js', 'utf-8');

// Search for the corrupted patterns
const idx1 = content.indexOf('\u00ef\u00bf\u00b0');
if (idx1 >= 0) {
    console.log('ï¿½ found at index', idx1);
    console.log('Context 1:', content.substring(idx1-50, idx1+50));
}

const idx2 = content.indexOf('ico: "ï¿½');
if (idx2 >= 0) {
    console.log('ico corrupted found at index', idx2);
    console.log('Context 2:', content.substring(idx2-30, idx2+30));
}

const idx3 = content.indexOf('???? Back');
if (idx3 >= 0) {
    console.log('???? Back found at index', idx3);
    console.log('Context 3:', content.substring(idx3-30, idx3+30));
}

const idx4 = content.indexOf('\u0002 Back');
if (idx4 >= 0) {
    console.log('Control-Back found at index', idx4);
    console.log('Context 4:', content.substring(idx4-30, idx4+30));
}

const idx5 = content.indexOf('\uFFFC?');
if (idx5 >= 0) {
    console.log('??? found at index', idx5);
    console.log('Context 5:', content.substring(idx5-30, idx5+30));
}

const regex = /ðŸ[a-zA-Z]/g;
const matches = content.match(regex);
if (matches) {
    const unique = [...new Set(matches)];
    console.log('Found ' + unique.length + ' unique ðŸ patterns:');
    for (const m of unique) {
        const idx = content.indexOf(m);
        console.log('  ' + m + ' at index ' + idx + ': ' + content.substring(idx-15, idx+15));
    }
}