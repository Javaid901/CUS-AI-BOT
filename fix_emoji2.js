const fs = require('fs');
let content = fs.readFileSync('frontend/js/chatbot.js', 'utf-8');

// Replace the corrupted ico content for helpful
// The pattern is: "ï¿½?" which should be "✅"
content = content.replace(/ï?.?/g, '✅');

// Also replace the nothelpful icon
// The pattern might be similar

fs.writeFileSync('frontend/js/chatbot.js', content, 'utf-8');

// Check if ï¿½ is still present
const fd = fs.readFileSync('frontend/js/chatbot.js', 'utf-8');
if (fd.includes('ï¿½')) {
    console.log('ï¿½ still present after fix');
    // Try a more targeted replacement
    let content2 = fd;
    content2 = content2.replace(/ï¿½?/g, '✅');
    fs.writeFileSync('frontend/js/chatbot.js', content2, 'utf-8');
    console.log('Applied second fix');
}

// Verify
const check = fs.readFileSync('frontend/js/chatbot.js', 'utf-8');
if (check.includes('ï¿½')) {
    console.log('ï¿½ still present after all fixes');
} else {
    console.log('ï¿½ successfully removed');
}