const fs = require('fs');
const file = 'C:/Users/LENOVO/OneDrive/Desktop/CUS-AI-BOT/frontend/js/chatbot.js';
const c = fs.readFileSync(file, 'utf8');

const oldStr = "input-area\"></div>' +\r\n      '</div>' +\r\n    '</div>';\r\n  document.body.appendChild(root);";

const newStr = "input-area\"></div>' +\r\n      '</div>' +\r\n    '</div>';\r\n  document.body.appendChild(root);";

console.log('oldStr found:', c.includes(oldStr));
if (c.includes(oldStr)) {
    const newC = c.replace(oldStr, newStr);
    fs.writeFileSync(file, newC, 'utf8');
    console.log('Done: replacement successful');
} else {
    console.log('oldStr NOT found');
}