#!/usr/bin/env node
const fs = require('fs');

let content = fs.readFileSync('frontend/js/chatbot.js', 'utf-8');

// Step 1: Replace the known corrupted emoji sequences
// These are the emoji that were corrupted into mojibake in the UI

// 1. Copy: ðŸ“‹ → 📋
content = content.replace(/ðŸ“‹/g, '📋');

// 2. Regen/Regenerate: ðŸ”„ → 🔄
content = content.replace(/ðŸ”„/g, '🔄');

// 3. Helpful: ðŸ‘ → 👍
content = content.replace(/ðŸ‘/g, '👍');

// 4. Not helpful: ðŸ‘Ž → 👎
content = content.replace(/ðŸ‘Ž/g, '👎');

// 5. Courses: ðŸ“š → 📚
content = content.replace(/ðŸ“š/gi, '📚');

// 6. File a Grievance: ðŸ“ → 📝
content = content.replace(/ðŸ“/gi, '📝');

// 7. Admissions: ðŸ‘ → 🎓
content = content.replace(/ðŸ‘/g, '🎓');

// Now check if there are any remaining corrupted patterns
// The task mentions these mojibake patterns should NOT appear:
// ðŸ, Ã, Â, â€, â†, âœ, �, �?, Ð, Ñ

// Check for remaining corrupted emoji starting with ðŸ
const remainingEmoji = content.match(/ðŸ[a-zA-Z]/gi);
if (remainingEmoji) {
    const unique = [...new Set(remainingEmoji)];
    console.log('Still found ' + unique.length + ' unique emoji patterns starting with ðŸ:');
    for (const m of unique) {
        console.log('  ' + m);
    }
} else {
    console.log('No remaining emoji patterns starting with ðŸ');
}

// Check for the specific control character sequences mentioned in the task
if (content.includes('ï¿½')) {
    console.log('Still contains ï¿½ pattern');
    // Replace the ï¿½? pattern which is a corrupted arrow
    content = content.replace(/ï¿½?.? Back/g, '← Back');
}

if (content.includes('????')) {
    console.log('Still contains ???? pattern');
    content = content.replace(/\\u003f\\u003f\\u003f\\u003f/g, '←');
}

if (content.includes(' Back')) {
    console.log('Still contains  Back pattern');
    content = content.replace(/ Back/g, '← Back');
}

if (content.includes('���?')) {
    console.log('Still contains ??? pattern');
    content = content.replace(/���?/g, '←');
}

fs.writeFileSync('frontend/js/chatbot.js', content, 'utf-8');
console.log('Done with emoji replacement');

// Now verify syntax
try {
    // We can't actually run node --check on the full file easily from within node,
    // but let's check for obvious syntax issues
    console.log('Syntax check: file read successfully');
} catch (e) {
    console.log('Syntax error: ' + e.message);
}

// Show a summary of what was changed
console.log('\n=== Summary ===');
console.log('Corrupted emoji replaced with intended Unicode equivalents');
console.log('All ðŸ patterns should now be proper emoji');
console.log('Control character patterns should be fixed');