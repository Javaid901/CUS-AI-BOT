const fs = require('fs');
const file = 'C:/Users/LENOVO/OneDrive/Desktop/CUS-AI-BOT/frontend/js/chatbot.js';
const c = fs.readFileSync(file, 'utf8');

const oldStr = "input-area\">' +\r\n        '<div class=\"input-wrap\">' +\r\n          '<textarea rows=\"1\" placeholder=\"Ask anything about Cluster University Srinagar...\" aria-label=\"Type your message\"></textarea>' +\r\n          '<button class=\"send\" aria-label=\"Send message\" disabled>' +\r\n            '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M22 2 11 13\"/><path d=\"M22 2 15 22l-4-9-9-4 20-7z\"/></svg>' +\r\n          '</button>' +\r\n        '</div>' +\r\n      '</div>' +\r\n    '</div>';";

const newStr = "input-area\">' +\r\n        '<div class=\"input-wrap\">' +\r\n          '<textarea rows=\"1\" placeholder=\"Ask anything about Cluster University Srinagar...\" aria-label=\"Type your message\"></textarea>' +\r\n          '<button class=\"mic\" aria-label=\"Hold to speak\" title=\"Hold to speak\" type=\"button\" aria-pressed=\"false\">' +\r\n            '<svg class=\"mic-icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" width=\"20\" height=\"20\"><path d=\"M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-6 0z\"/><path d=\"M19 10v2a7 7 0 0 1-14 0v-2\"/><line x1=\"12\" y1=\"19\" x2=\"12\" y2=\"22\"/><line x1=\"8\" y1=\"23\" x2=\"16\" y2=\"23\"/></svg>' +\r\n            '<span class=\"mic-label\">Listening...</span>' +\r\n          '</button>' +\r\n          '<button class=\"send\" aria-label=\"Send message\" disabled>' +\r\n            '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M22 2 11 13\"/><path d=\"M22 2 15 22l-4-9-9-4 20-7z\"/></svg>' +\r\n          '</button>' +\r\n        '</div>' +\r\n      '</div>' +\r\n    '</div>';";

console.log('oldStr found:', c.includes(oldStr));
if (c.includes(oldStr)) {
    const newC = c.replace(oldStr, newStr);
    fs.writeFileSync(file, newC, 'utf8');
    console.log('Done: replacement successful');
} else {
    console.log('oldStr NOT found');
    const idx = c.indexOf('input-area');
    console.log('Actual content:', JSON.stringify(c.substring(idx, idx + 800)));
}