(function () {
  "use strict";

  if (!window.CUS_API_BASE) throw new Error("CUS_API_BASE not defined — ensure config.js loads before chatbot.js");
  var API = window.CUS_API_BASE;
  var TITLE = "CUS AI Assistant";
  var AUTH_KEY = "cus_auth";  // JSON: {user, pass, token}

  /* ---------- Persistent auth ---------- */
  // Migrate from legacy cus_token-only storage to cus_auth (user+pass+token)
  (function () {
    var oldToken = localStorage.getItem("cus_token");
    if (oldToken && !localStorage.getItem(AUTH_KEY)) {
      localStorage.setItem(AUTH_KEY, JSON.stringify({ user: null, pass: null, token: oldToken }));
      localStorage.removeItem("cus_token");
      console.log("[CUS] Migrated legacy token to cus_auth");
    }
  })();

  function loadAuth() {
    try { var raw = localStorage.getItem(AUTH_KEY); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }
  function saveAuth(user, pass, token) {
    localStorage.setItem(AUTH_KEY, JSON.stringify({ user: user, pass: pass, token: token }));
  }
  function clearAuth() {
    localStorage.removeItem(AUTH_KEY);
  }

  var _auth = loadAuth();
  var _authPromise = null;        // guards against concurrent ensureAuth() calls
  var state = {
    token: _auth ? _auth.token : null,
    authUser: _auth ? _auth.user : null,
    authPass: _auth ? _auth.pass : null,
    chatId: null,
    streaming: false,
    controller: null,
    authReady: false,
    firstMsg: true,
  };
  window.CUS = window.CUS || {};

  /* ---------- Debug logger ---------- */
  function logReq(method, url, extra) { console.log("[CUS] " + method + " " + url + (extra ? " | " + extra : "")); }
  function logErr(method, url, status, body) { console.error("[CUS] FAIL " + method + " " + url + " | " + status + (body ? " | " + (typeof body === "string" ? body.slice(0, 200) : JSON.stringify(body).slice(0, 200)) : "")); }

  /* ---------- Safe markdown ---------- */
  function escapeHtml(s) { return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function renderMarkdown(text) {
    var esc = escapeHtml(text);
    esc = esc.replace(/```([\s\S]*?)```/g, function (_, c) { return "<pre><code>" + c.replace(/^\n/, "") + "</code></pre>"; });
    esc = esc.replace(/`([^`]+)`/g, "<code>$1</code>");
    esc = esc.replace(/^### (.*)$/gm, "<h4>$1</h4>");
    esc = esc.replace(/^## (.*)$/gm, "<h3>$1</h3>");
    esc = esc.replace(/^# (.*)$/gm, "<h3>$1</h3>");
    esc = esc.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    esc = esc.replace(/(?:^|\n)(?:- |\* )(.*)/g, function (m) { return "\n<li>" + m.replace(/^[^\s]* /, "") + "</li>"; });
    esc = esc.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
    esc = esc.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    esc = esc.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
    return "<p>" + esc + "</p>";
  }

  /* ---------- Grievance intent detection (client-side) ---------- */
  // Natural-language trigger for the in-chat grievance workflow. Runs before
  // the API call so filing intent opens the grievance form instantly.
  // Conservative on purpose: pure informational/process questions ("how to
  // file a grievance", "what is the grievance process", "where is the
  // grievance cell?") are never treated as filing intent.
  var GRIEVANCE_INTENT_PHRASES = [
    "file a grievance", "file grievance", "file a complaint", "file complaint",
    "submit a grievance", "submit grievance", "submit a complaint", "submit complaint",
    "register a grievance", "register grievance", "register a complaint", "register complaint",
    "raise a grievance", "raise grievance", "raise a complaint", "raise complaint",
    "lodge a grievance", "lodge grievance", "lodge a complaint", "lodge complaint",
    "i want to complain", "i would like to complain", "want to complain",
    "i want to file", "i would like to file", "want to file",
    "i want to register", "i want to submit",
    "i have a grievance", "i have a complaint",
    "i have an issue", "i have a problem", "i am having a problem", "i am having an issue",
    "complaint about", "complaint regarding", "complaint against",
    "grievance about", "grievance regarding", "grievance against",
    "report a problem", "report a complaint", "report an issue", "report the issue",
    "grievance cell", "complaint box",
    "complain about", "complaining about",
    "facing a problem", "facing an issue", "having a problem", "having an issue",
    "raise an issue", "raise an objection",
    "meri complaint", "mujhe complaint", "meri shikayat", "mujhe shikayat",
    "complaint file karni", "complaint karni", "complaint hai", "shikayat hai",
    "complaint daalni", "complaint deni"
  ];
  var GRIEVANCE_QUERY_FRAMES = [
    "how to", "how do", "how can", "what is", "what are", "what's",
    "where is", "where can", "where do", "when will", "when do",
    "tell me", "explain", "is there", "does the", "can i", "can you", "do i"
  ];
  var GRIEVANCE_PROBLEM_MARKERS = [
    "not received", "not recived", "not showing", "not working", "not generated",
    "not updated", "not available", "missing", "wrong", "incorrect", "delayed",
    "didn't", "didnt", "hasn't", "haven't", "stuck", "error", "problem with",
    "issue with", "refused", "denied", "overcharged", "deducted", "tampered",
    "problem hai", "issue hai", "dikkat", "problem aa",
    "nahi aa", "nahi aaya", "nahi aayi", "nahi ho", "nahi ho raha",
    "nahi mila", "nahi mili", "nahi mil", "nahi khul", "khul nahi",
    "login nahi", "refund nahi", "charge zyada", "zyada charge",
    "galat", "galti", "der ho", "bahut der", "response nahi", "reply nahi"
  ];
  function isGrievanceIntent(message) {
    var raw = (message || "").trim();
    if (raw.length < 3) return false;
    var m = " " + raw.toLowerCase().replace(/\s+/g, " ") + " ";
    if (m.trim() === "grievance" || m.trim() === "complaint") return true;
    var isQuery = false;
    for (var f = 0; f < GRIEVANCE_QUERY_FRAMES.length; f++) {
      if (m.indexOf(GRIEVANCE_QUERY_FRAMES[f]) !== -1) { isQuery = true; break; }
    }
    if (isQuery) {
      // A complaint inside a question is still a grievance
      // ("where is my admit card? I haven't received it").
      for (var pm = 0; pm < GRIEVANCE_PROBLEM_MARKERS.length; pm++) {
        if (m.indexOf(GRIEVANCE_PROBLEM_MARKERS[pm]) !== -1) return true;
      }
      return false;
    }
    for (var p = 0; p < GRIEVANCE_INTENT_PHRASES.length; p++) {
      if (m.indexOf(GRIEVANCE_INTENT_PHRASES[p]) !== -1) return true;
    }
    return false;
  }
  // Strip a leading filing phrase so the grievance textarea starts with the
  // actual problem ("i want to file a complaint about X" -> "X").
  var GRIEVANCE_LEAD_RE = /^(?:i|i'd|i would|i want to|i'd like to|i would like to|want to|would like to)\s+(?:file|register|submit|raise|lodge|make)\s+(?:a\s+)?(?:formal\s+)?(?:grievance|complaint)\s+(?:about|regarding|against|on|for|because)\s+/i;
  var GRIEVANCE_LEAD_RE2 = /^(?:i|i'd|i've)\s+(?:have|got)\s+(?:a\s+)?(?:grievance|complaint|problem|issue)\s+(?:about|regarding|with|against|on|because)\s+/i;
  var GRIEVANCE_LEAD_RE3 = /^(?:i|i'd|i would|i want to|i'd like to|i would like to|want to|would like to)\s+(?:complain|complaining)\s+(?:about|regarding|against|on|for|because)\s+/i;
  var GRIEVANCE_LEAD_RE4 = /^(?:file|register|submit|raise|lodge|make)\s+(?:a\s+)?(?:formal\s+)?(?:grievance|complaint)\s+(?:about|regarding|against|on|for|because)\s+/i;
  var GRIEVANCE_BARE_RE = /^(?:(?:i|i'd|i've|i would|i want to|i'd like to|i would like to|want to|would like to)\s+)?(?:file|register|submit|raise|lodge|make|complain|complaining)\s+(?:a\s+)?(?:formal\s+)?(?:grievance|complaint)$/i;
  function grievancePrefill(text) {
    var t = (text || "").trim();
    t = t.replace(GRIEVANCE_LEAD_RE, "").replace(GRIEVANCE_LEAD_RE2, "").replace(GRIEVANCE_LEAD_RE3, "").replace(GRIEVANCE_LEAD_RE4, "").trim();
    if (!t) return "";
    if (GRIEVANCE_BARE_RE.test(t)) return "";
    return t.length >= 3 ? t : "";
  }

  /* ---------- Build DOM ---------- */
  var root = document.createElement("div");
  root.id = "cusw";
  root.innerHTML =
    '<button class="bubble" title="Chat with CUS AI" aria-label="Open chat">CUS<span class="badge">AI</span></button>' +
    '<div class="backdrop"></div>' +
    '<div class="panel" role="dialog" aria-label="CUS AI Assistant">' +
      '<div class="head">' +
        '<div class="head-brand">' +
          '<div class="head-logo"><svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M12 3 1 8l11 5 9-4.09V17h2V8L12 3z"/><path d="M5 13.18v4L12 21l7-3.82v-4L12 17l-7-3.82z"/></svg></div>' +
          '<div class="head-info">' +
            '<div class="head-title">' + TITLE + '</div>' +
            '<div class="head-sub">Cluster University Srinagar</div>' +
            '<div class="head-status"><span class="status on" id="cus-status"></span><span class="stxt">Ready to assist</span></div>' +
          '</div>' +
        '</div>' +
        '<div class="head-actions">' +
          '<button class="act-clear" title="Clear chat">🗑</button>' +
          '<button class="act-close" title="Close">✕</button>' +
        '</div>' +
      '</div>' +
      '<div class="body" role="log" aria-live="polite" aria-label="Conversation"></div>' +
      '<div class="suggest">' +
        '<button data-chip="Admissions">Admissions</button>' +
        '<button data-chip="Courses">Courses</button>' +
        '<button data-chip="Scholarships">Scholarships</button>' +
        '<button data-chip="File a Grievance">📋 File a Grievance</button>' +
      '</div>' +
      '<div class="input-area">' +
        '<div class="input-wrap">' +
          '<textarea rows="1" placeholder="Ask anything about Cluster University Srinagar..." aria-label="Type your message"></textarea>' +
          '<button class="send" aria-label="Send message" disabled>' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg>' +
          '</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  document.body.appendChild(root);

  var bubble = root.querySelector(".bubble");
  var badge = root.querySelector(".badge");
  var backdrop = root.querySelector(".backdrop");
  var panel = root.querySelector(".panel");
  var body = root.querySelector(".body");
  var input = root.querySelector("textarea");
  var sendBtn = root.querySelector(".send");
  var suggest = root.querySelector(".suggest");

  /* ---------- Helpers ---------- */
  function toast(msg) { var t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(function () { t.remove(); }, 2600); }
  function setToken(t) { state.token = t; if (state.authUser && state.authPass) saveAuth(state.authUser, state.authPass, t); }
  function authHeaders() { var h = { "Content-Type": "application/json" }; if (state.token) h.Authorization = "Bearer " + state.token; return h; }
  function timeNow() { var d = new Date(); return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2); }

  function addMsg(role, html, cites, context, queryMeta) {
    var row = document.createElement("div"); row.className = "row " + role;
    var av = document.createElement("div"); av.className = "avatar"; av.textContent = role === "bot" ? "C" : "U";
    var cell = document.createElement("div"); cell.style.minWidth = "0"; cell.style.flex = "1";
    var msg = document.createElement("div"); msg.className = "msg"; msg.innerHTML = html;

    // Show correction banner if query was corrected
    if (role === "bot" && queryMeta && queryMeta.corrected) {
      var corr = document.createElement("div"); corr.className = "corr-banner";
      corr.innerHTML = "💡 Did you mean: <strong>" + escapeHtml(queryMeta.clean) + "</strong>?";
      cell.appendChild(corr);
    }

    cell.appendChild(msg);
    if (cites && cites.length) {
      var cwrap = document.createElement("div"); cwrap.className = "cites";
      var seen = {};
      cites.forEach(function (c) {
        var id = c.document_id || c.document_title || c.source;
        if (id && seen[id]) return; if (id) seen[id] = 1;
        var el = document.createElement("span"); el.className = "cite";
        el.innerHTML = "<b>" + escapeHtml(c.document_title || c.source || "Document") + "</b>" + (c.score != null ? " · " + Number(c.score).toFixed(2) : "");
        cwrap.appendChild(el);
      });
      cell.appendChild(cwrap);
    }
    if (role === "bot") {
      var tools = document.createElement("div"); tools.className = "tools";
      var btns = [
        { label: "Copy", ico: "📋", action: "copy" },
        { label: "Regen", ico: "🔄", action: "regen" },
        { label: "Helpful", ico: "👍", action: "helpful" },
        { label: "Not helpful", ico: "👎", action: "nothelpful" },
      ];
      btns.forEach(function (b) {
        var btn = document.createElement("button"); btn.innerHTML = '<span class="ico">' + b.ico + '</span> ' + b.label;
        btn.dataset.action = b.action;
        tools.appendChild(btn);
      });
      cell.appendChild(tools);
    }
    var meta = document.createElement("div"); meta.className = "meta"; meta.textContent = timeNow();
    cell.appendChild(meta);
    // Insert breadcrumbs at top of bot messages when context is present
    if (role === "bot" && context && context.breadcrumbs && context.breadcrumbs.length) {
      var bc = document.createElement("div"); bc.className = "breadcrumbs";
      context.breadcrumbs.forEach(function (crumb, i) {
        if (i > 0) bc.appendChild(document.createTextNode(" › "));
        var span = document.createElement("span"); span.textContent = crumb; bc.appendChild(span);
      });
      if (context.programme) bc.dataset.prog = context.programme;
      if (context.college) {
        bc.dataset.college = context.college;
        bc.dataset.college_name = context.college_name || "";
        bc.style.borderLeft = "3px solid var(--green)";
        bc.style.paddingLeft = "8px";
      }
      cell.insertBefore(bc, msg);
    }
    row.appendChild(av); row.appendChild(cell);
    body.appendChild(row); body.scrollTop = body.scrollHeight;
    return { msgEl: msg, toolsEl: role === "bot" ? tools : null, rowEl: row };
  }

  /* ---------- Student Service definitions ---------- */
  var STUDENT_SERVICES = [
    { id: "results", label: "Results" },
    { id: "admit_card", label: "Admit Card" },
    { id: "exam_form", label: "Exam Form" },
    { id: "fee", label: "Fee Receipt" },
    { id: "registration", label: "Course Registration" },
    { id: "attendance", label: "Attendance" },
    { id: "re_evaluation", label: "Re-evaluation" },
    { id: "xerox_copy", label: "Xerox Copy" },
    { id: "semester_admission", label: "Semester Admission" },
    { id: "migration", label: "Migration Certificate" },
    { id: "transcript", label: "Transcript" },
    { id: "backlog", label: "Backlog Status" },
    { id: "profile", label: "Student Profile" },
    { id: "helpdesk", label: "Helpdesk" },
  ];

  // Services whose results change per semester (enable semester switcher chips)
  var _SEMESTER_SERVICES = ["results", "admit_card", "exam_form", "attendance", "registration"];

  function addGreeting() {
    body.innerHTML = "";
    addMsg("bot", "<p>Hello! <span>I can help you with admissions, courses, student services, and more. Select a topic below or type your question.</span></p>");
    // Show welcome chips
    renderOptions({
      type: "options",
      title: "Quick Help",
      message: "",
      options: [
        { id: "admissions", label: "Admissions" },
        { id: "courses", label: "Courses" },
        { id: "fee", label: "Fee Structure" },
        { id: "results", label: "Results" },
        { id: "datesheet", label: "Date Sheet" },
        { id: "scholarships", label: "Scholarships" },
        { id: "colleges", label: "Colleges" },
        { id: "student_services", label: "Student Services" },
        { id: "contact", label: "Contact Info" },
      ]
    });
  }

  function renderServiceForm(payload) {
    /* Render a service param collection form.
       payload: { type: "service_form", service: "...", title: "...", message: "...",
                   fields: [{id, label, type, options, placeholder}] }
       On submit, packs values as "field1||field2||..." and sends as chat message.
    */
    var fid = "svc_" + Date.now().toString(36);
    var title = payload.title || "Service Details";
    var msg = payload.message || "";
    var fields = payload.fields || [];
    var html = "";
    html += '<div class="service-form" id="' + fid + '">';
    html += "<h4>" + escapeHtml(title) + "</h4>";
    if (msg) html += "<p>" + escapeHtml(msg) + "</p>";
    fields.forEach(function (f) {
      var inputId = fid + "_" + f.id;
      html += '<div class="svc-field">';
      html += '<label for="' + inputId + '">' + escapeHtml(f.label || f.id) + "</label>";
      if (f.options && f.options.length) {
        html += '<select id="' + inputId + '" class="svc-input svc-select">';
        html += '<option value="">-- Select --</option>';
        f.options.forEach(function (opt) {
          html += '<option value="' + escapeHtml(opt.value || opt) + '">' + escapeHtml(opt.label || opt) + "</option>";
        });
        html += "</select>";
      } else {
        html += '<input type="' + (f.type || "text") + '" id="' + inputId + '" class="svc-input" placeholder="' + escapeHtml(f.placeholder || "") + '" />';
      }
      html += "</div>";
    });
    html += '<div class="svc-actions">';
    html += '<button class="chip svc-submit" data-role="svc-submit" data-form="' + fid + '">Continue</button>';
    html += '<button class="chip back" data-role="option" data-value="back">Cancel</button>';
    html += "</div></div>";
    addMsg("bot", html);
  }

  /* ---------- Query correction tracking ---------- */
  function trackCorrection(queryMeta) {
    if (queryMeta && queryMeta.corrected && queryMeta.clean) {
      console.log("[CUS] Query corrected: '" + (queryMeta.original || "") + "' -> '" + queryMeta.clean + "'");
    }
  }

  /* ---------- Structured response renderers ---------- */
  function renderOptions(payload) {
    var msg = payload.message || "";
    var title = payload.title || "";
    var items = payload.options || [];
    var context = payload.context || null;
    var queryMeta = payload._query || null;
    var html = "";
    if (title) html += "<strong>" + escapeHtml(title) + "</strong>";
    if (msg && msg !== title) html += "<p>" + escapeHtml(msg) + "</p>";
    if (payload.selector === "scheme") {
      // Scheme picker — larger cards with descriptions
      html += '<div class="opts scheme-opts">';
      items.forEach(function (o) {
        html += '<button class="scheme-card" data-role="option" data-value="' + escapeHtml(o.id) + '">' +
          '<span class="scheme-label">' + escapeHtml(o.label) + '</span>' +
          (o.description ? '<span class="scheme-desc">' + escapeHtml(o.description) + '</span>' : "") +
          '</button>';
      });
      html += "</div>";
    } else {
      html += '<div class="opts">';
      items.forEach(function (o) {
        html += '<button class="chip" data-role="option" data-value="' + escapeHtml(o.id) + '">' + escapeHtml(o.label) + '</button>';
      });
      if (!payload.no_back) {
        html += '<button class="chip back" data-role="option" data-value="back">← Back</button>';
      }
      html += "</div>";
    }
    addMsg("bot", html, null, context, queryMeta);
  }

  function renderProfileCard(student) {
    // Returns a profile summary card (or empty string) for the authenticated student.
    if (!student) return "";
    var html = '<div class="profile-card">';
    html += '<div class="profile-card-head">👤 Student Profile</div>';
    html += '<div class="profile-card-grid">';
    if (student.name) html += '<div><span class="pl">Name</span><span class="pv">' + escapeHtml(student.name) + '</span></div>';
    if (student.reg_no) html += '<div><span class="pl">Reg No</span><span class="pv">' + escapeHtml(student.reg_no) + '</span></div>';
    if (student.programme) html += '<div><span class="pl">Programme</span><span class="pv">' + escapeHtml(student.programme.toUpperCase()) + '</span></div>';
    if (student.semester) html += '<div><span class="pl">Semester</span><span class="pv">' + escapeHtml(student.semester) + '</span></div>';
    if (student.scheme_label) html += '<div><span class="pl">Scheme</span><span class="pv">' + escapeHtml(student.scheme_label) + '</span></div>';
    html += "</div></div>";
    return html;
  }

  function renderDetail(payload) {
    var title = payload.title || "Details";
    var fields = payload.fields || [];
    var actions = payload.actions || [];
    var context = payload.context || null;
    var queryMeta = payload._query || null;
    var html = "";
    html += '<div class="detail-card">';
    html += "<h4>" + escapeHtml(title) + "</h4>";
    if (payload.message) html += "<p>" + escapeHtml(payload.message) + "</p>";
    // Student profile summary card
    var student = context && context.student;
    if (student && student.reg_no) html += renderProfileCard(student);
    html += '<table class="dtbl">';
    fields.forEach(function (f) {
      html += "<tr><td class=\"dl\">" + escapeHtml(f.label) + "</td><td class=\"dv\">" + escapeHtml(f.value) + "</td></tr>";
    });
    html += "</table>";
    if (actions.length) {
      html += '<div class="dacts">';
      actions.forEach(function (a) {
        html += '<button class="chip" data-role="action" data-value="' + escapeHtml(a.id) + '">' + escapeHtml(a.label) + '</button>';
      });
      html += "</div>";
    }
    // Semester switcher — shown after a student service result for semester-aware services
    var svc = context && context.service;
    var stu = context && context.student;
    if (svc && stu && stu.semester && _SEMESTER_SERVICES.indexOf(svc) !== -1) {
      var curSem = parseInt(stu.semester, 10);
      var items = [];
      if (!isNaN(curSem) && curSem > 1) items.push(["previous semester", "◀ Previous semester"]);
      items.push(["current semester", "Current semester (" + escapeHtml(stu.semester) + ")"]);
      items.push(["next semester", "Next semester ▶"]);
      html += '<div class="qactions sem-switch">';
      html += '<span class="qalabel">🧭 View another semester:</span>';
      items.forEach(function (it) {
        html += '<button class="chip qa" data-role="option" data-value="' + it[0] + '">' + it[1] + '</button>';
      });
      html += "</div>";
    }
    // Quick action chips when a college is active
    if (context && context.college) {
      html += '<div class="qactions">';
      if (context.college_name) {
        html += '<span class="qalabel">' + escapeHtml(context.college_name) + '</span>';
      }
      html += '<button class="chip qa" data-role="action" data-value="about">About</button>';
      html += '<button class="chip qa" data-role="action" data-value="courses">Courses</button>';
      html += '<button class="chip qa" data-role="action" data-value="departments">Departments</button>';
      html += '<button class="chip qa" data-role="action" data-value="admissions">Admissions</button>';
      html += '<button class="chip qa" data-role="action" data-value="fee">Fee</button>';
      html += '<button class="chip qa" data-role="action" data-value="eligibility">Eligibility</button>';
      html += '<button class="chip qa" data-role="action" data-value="facilities">Facilities</button>';
      html += '<button class="chip qa" data-role="action" data-value="contact">Contact</button>';
      html += '<button class="chip qa" data-role="action" data-value="principal">Principal</button>';
      html += "</div>";
    }
    // Quick action chips when a programme is active (and no college)
    if (context && context.programme) {
      html += '<div class="qactions">';
      if (!context.college) {
        html += '<button class="chip qa" data-role="action" data-value="fee">Fee</button>';
        html += '<button class="chip qa" data-role="action" data-value="eligibility">Eligibility</button>';
        html += '<button class="chip qa" data-role="action" data-value="duration">Duration</button>';
        html += '<button class="chip qa" data-role="action" data-value="dates">Dates</button>';
      }
      // Add colleges offering this course action
      html += '<button class="chip qa" data-role="action" data-value="colleges_for_course">🏛 Colleges offering this</button>';
      html += "</div>";
    }
    html += '<button class="chip back" data-role="option" data-value="back">← Back</button>';
    html += "</div>";
    addMsg("bot", html, null, context, queryMeta);
  }

  function renderAuthForm(payload) {
    // Auth form with input fields and a submit button.
    // On submit, values are packed as "reg_no||password" and sent as a chat message.
    var title = payload.title || "Sign In";
    var msg = payload.message || "";
    var service = payload.service || "";
    var fields = payload.fields || [];
    var submitLabel = payload.submit_label || "Sign In";

    // Generate unique ID for this form instance
    var fid = "auth_" + Date.now().toString(36);
    var html = "";
    html += '<div class="auth-form" id="' + fid + '">';
    html += "<h4>" + escapeHtml(title) + "</h4>";
    if (msg) html += "<p>" + escapeHtml(msg) + "</p>";
    fields.forEach(function (f) {
      var inputId = fid + "_" + f.id;
      html += '<div class="afield">';
      html += '<label for="' + inputId + '">' + escapeHtml(f.label) + "</label>";
      html += '<input type="' + (f.type || "text") + '" id="' + inputId + '" class="ainput" placeholder="' + escapeHtml(f.placeholder || "") + '" autocomplete="' + (f.type === "password" ? "current-password" : "username") + '" />';
      html += "</div>";
    });
    html += '<div class="aacts">';
    html += '<button class="chip asubmit" data-role="auth-submit" data-form="' + fid + '">' + escapeHtml(submitLabel) + '</button>';
    html += '<button class="chip back" data-role="option" data-value="back">← Cancel</button>';
    html += "</div>";
    html += "</div>";
    addMsg("bot", html);
  }
  function showTyping() { var row = document.createElement("div"); row.className = "row bot"; row.id = "cus-typing"; row.innerHTML = '<div class="avatar">C</div><div class="msg typing"><span></span><span></span><span></span></div>'; body.appendChild(row); body.scrollTop = body.scrollHeight; }
  function removeTyping() { var t = document.getElementById("cus-typing"); if (t) t.remove(); }
  function showSpinner() {
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<span class="spinner"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10" stroke-dasharray="31.4 31.4" stroke-linecap="round"/></svg></span>';
  }
  function hideSpinner() {
    sendBtn.disabled = !input.value.trim();
    sendBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg>';
  }
  function updateSendState() { if (state.streaming) return; sendBtn.disabled = !input.value.trim(); }

  /* ---------- Message action handlers ---------- */
  body.addEventListener("click", function (e) {
    var t = e.target.closest("button");
    if (!t) return;
    var role = t.getAttribute("data-role");

    // Auth form submit — collect input values and send as "val1||val2"
    if (role === "auth-submit") {
      if (state.streaming) return;
      var formId = t.getAttribute("data-form");
      var formEl = document.getElementById(formId);
      if (!formEl) return;
      var inputs = formEl.querySelectorAll(".ainput");
      var values = [];
      inputs.forEach(function (inp) { values.push(inp.value.trim()); });
      if (!values[0]) { toast("Please enter your Registration Number"); return; }
      if (!values[1]) { toast("Please enter your Password"); return; }
      if (state.firstMsg) { suggest.classList.add("hidden"); state.firstMsg = false; }
      addMsg("user", "<p>Credentials submitted for <strong>" + escapeHtml(values[0]) + "</strong></p>");
      showSpinner(); showTyping();
      doChat(values.join("||"));
      return;
    }

    // Service form submit — collect input values and send as "val1||val2||..."
    if (role === "svc-submit") {
      if (state.streaming) return;
      var svcFormId = t.getAttribute("data-form");
      var svcFormEl = document.getElementById(svcFormId);
      if (!svcFormEl) return;
      var svcInputs = svcFormEl.querySelectorAll(".svc-input");
      var svcValues = [];
      svcInputs.forEach(function (inp) { svcValues.push(inp.value.trim()); });
      if (!svcValues[0]) { toast("Please fill in the required fields"); return; }
      if (state.firstMsg) { suggest.classList.add("hidden"); state.firstMsg = false; }
      addMsg("user", "<p>Details submitted</p>");
      showSpinner(); showTyping();
      doChat(svcValues.join("||"));
      return;
    }

    if (role === "option" || role === "action") {
      if (state.streaming) return;
      var val = t.getAttribute("data-value") || "";
      if (!val) return;
      if (state.firstMsg) { suggest.classList.add("hidden"); state.firstMsg = false; }
      var label = t.textContent.replace("←", "").trim();

      // Special handling: "student_services" shows the service list locally
      if (val === "student_services") {
        if (state.firstMsg) { suggest.classList.add("hidden"); state.firstMsg = false; }
        addMsg("user", escapeHtml("Student Services"));
        var svcHtml = '<div class="detail-card">';
        svcHtml += "<h4>Student Services</h4><p>Select a service below:</p>";
        svcHtml += '<div class="opts">';
        STUDENT_SERVICES.forEach(function (s) {
          svcHtml += '<button class="chip" data-role="option" data-value="' + escapeHtml(s.id) + '">' + escapeHtml(s.label) + '</button>';
        });
        svcHtml += '<button class="chip back" data-role="option" data-value="back">← Back</button>';
        svcHtml += "</div></div>";
        addMsg("bot", svcHtml);
        return;
      }

      // Special handling: "colleges_for_course" action sends a natural query
      if (val === "colleges_for_course") {
        var bcEl = t.closest(".detail-card")?.querySelector(".breadcrumbs");
        var prog = bcEl ? bcEl.dataset.prog : null;
        if (prog) {
          label = "which colleges offer " + prog.toUpperCase();
          val = label;
        }
      }

      addMsg("user", escapeHtml(label));
      showSpinner(); showTyping();
      doChat(val);
      return;
    }

    var toolsEl = t.closest(".tools");
    if (!toolsEl) return;
    var rowEl = toolsEl.closest(".row");
    var msgEl = rowEl ? rowEl.querySelector(".msg") : null;
    var text = msgEl ? msgEl.textContent || "" : "";
    var action = t.dataset.action;
    if (action === "copy") {
      navigator.clipboard.writeText(text).then(function () { t.innerHTML = '<span class="ico">✓</span> Copied'; t.classList.add("copied"); toast("Copied to clipboard"); setTimeout(function () { t.innerHTML = '<span class="ico">📋</span> Copy'; t.classList.remove("copied"); }, 1600); }, function () { toast("Copy failed"); });
    } else if (action === "regen") { if (state.streaming) return; if (rowEl) rowEl.remove(); input.value = text; sendClick(); }
    else if (action === "helpful") { t.innerHTML = '<span class="ico">✅</span> Helpful'; t.style.color = "#0F5132"; t.style.fontWeight = "600"; t.dataset.action = "done"; }
    else if (action === "nothelpful") { t.innerHTML = '<span class="ico">✅</span> Not helpful'; t.style.color = "#0F5132"; t.style.fontWeight = "600"; t.dataset.action = "done"; }
  });

  /* ---------- Auto-resize textarea ---------- */
  function autosize() {
    input.style.height = "auto";
    var lineH = 22;
    var maxH = lineH * 5 + 20;
    input.style.height = Math.min(Math.max(input.scrollHeight, 28), maxH) + "px";
    updateSendState();
  }

  /* ---------- Auth: persistent register/login ---------- */
  function ensureAuth() {
    if (state.authReady) { return Promise.resolve(); }

    // Return in-flight promise to prevent concurrent auth flows
    if (_authPromise) { return _authPromise; }

    if (state.token) {
      state.authReady = true;
      _authPromise = null;
      return Promise.resolve();
    }

    if (state.authUser && state.authPass) {
      _authPromise = _doLogin(state.authUser, state.authPass).finally(function () { _authPromise = null; });
      return _authPromise;
    }

    var user = "web_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    var pass = "g" + Math.random().toString(36).slice(2, 12);
    _authPromise = _doRegister(user, pass).finally(function () { _authPromise = null; });
    return _authPromise;
  }

  function _doRegister(user, pass) {
    var url = API + "/api/auth/register";
    logReq("POST", url, "user=" + user);
    return fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user, email: user + "@guest.cus", password: pass, role: "student" }),
    }).then(function (r) {
      if (r.ok) {
        console.log("[CUS] Register OK");
        return _doLogin(user, pass);
      }
      if (r.status === 409) {
        // Username collision (extremely rare with timestamp-based names) — try again
        console.warn("[CUS] Register 409 (collision), retrying with different name");
        user = "web_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
        pass = "g" + Math.random().toString(36).slice(2, 12);
        return _doRegister(user, pass);
      }
      // Any other register error — try login anyway (user might exist from prior session)
      console.warn("[CUS] Register returned " + r.status + ", attempting login");
      return _doLogin(user, pass);
    });
  }

  function _doLogin(user, pass) {
    var url = API + "/api/auth/login";
    logReq("POST", url, "user=" + user);
    return fetch(url, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "username=" + encodeURIComponent(user) + "&password=" + encodeURIComponent(pass),
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (d) {
          throw new Error("Login failed (" + r.status + "): " + (d.detail || "unknown"));
        });
      }
      return r.json();
    }).then(function (d) {
      if (!d.access_token) throw new Error("No access_token in login response");
      state.authUser = user;
      state.authPass = pass;
      setToken(d.access_token);
      state.authReady = true;
      saveAuth(user, pass, d.access_token);
      console.log("[CUS] Auth: JWT obtained and saved");
    });
  }

  /* ---------- Send ---------- */
  function sendClick() {
    var text = input.value.trim(); if (!text || state.streaming) return;
    if (state.firstMsg) { suggest.classList.add("hidden"); state.firstMsg = false; }

    addMsg("user", "<p>" + escapeHtml(text) + "</p>");
    input.value = ""; autosize();

    // Grievance / complaint intent — open the in-chat workflow instantly,
    // with the user's own words pre-filled so they can AI-draft from them.
    if (isGrievanceIntent(text)) {
      addMsg("bot", "<p>Sure — I'll open the grievance form for you. Describe the problem below and we'll route it to the right office.</p>");
      govStart(grievancePrefill(text), "");
      govAutoMatchAuthority(text);
      return;
    }

    showSpinner(); showTyping();
    doChat(text);
  }

  function doChat(text, isRetry) {
    var assistantHtml = ""; var msgObj = null; var cites = [];

    ensureAuth().then(function () {
      if (!state.token) {
        removeTyping(); hideSpinner();
        addMsg("bot", "<p>⚠️ Authentication failed. Please refresh the page to try again.</p>");
        return;
      }
      state.streaming = true;
      state.controller = new AbortController();
      var chatUrl = API + "/api/chat/ask";
      logReq("POST", chatUrl, "stream=true" + (isRetry ? " (retry)" : ""));
      var t0 = Date.now();

      fetch(chatUrl, {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ message: text, chat_id: state.chatId, stream: true }),
        signal: state.controller.signal,
      }).then(function (resp) {
        if (!resp.ok) {
          if (resp.status === 401 && !isRetry) {
            console.warn("[CUS] Token rejected (HTTP 401). Re-authenticating...");
            state.token = null;
            state.authReady = false;
            // Don't clearAuth() — keep credentials for re-login attempt
            (function attempt(creds) {
              if (creds) {
                return _doLogin(creds.user, creds.pass).then(function () {
                  console.log("[CUS] Re-auth OK, retrying chat request");
                  removeTyping(); hideSpinner();
                  doChat(text, true);
                }).catch(function () {
                  console.warn("[CUS] Re-login failed, trying full re-register");
                  state.authUser = null; state.authPass = null; clearAuth();
                  return attempt(null);
                });
              }
              var user = "web_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
              var pass = "g" + Math.random().toString(36).slice(2, 12);
              return _doRegister(user, pass).then(function () {
                console.log("[CUS] Re-register OK, retrying chat request");
                removeTyping(); hideSpinner();
                doChat(text, true);
              }).catch(function (e) {
                removeTyping(); hideSpinner();
                addMsg("bot", "<p>⚠️ Cannot authenticate. Please refresh the page.</p>");
                state.streaming = false;
              });
            })(state.authUser && state.authPass ? { user: state.authUser, pass: state.authPass } : null);
            return;
          }
          // Non-401 error or already retried
          if (resp.status === 401) {
            removeTyping(); hideSpinner();
            addMsg("bot", "<p>⚠️ Still unauthorized after re-authentication. Please refresh the page.</p>");
            state.streaming = false; hideSpinner();
          } else {
            logErr("POST", chatUrl, resp.status, "HTTP error");
            resp.text().then(function (t) { logErr("POST", chatUrl, resp.status, t); }).catch(function () {});
            removeTyping(); hideSpinner();
            addMsg("bot", "<p>⚠️ Server error (HTTP " + resp.status + "). Please try again.</p>");
            state.streaming = false; hideSpinner();
          }
          return;
        }

        // Successful response — stream tokens or structured events
        var reader = resp.body.getReader(); var dec = new TextDecoder(); var buf = "";
        function read() {
          return reader.read().then(function (r) {
            if (r.done) { finish(); return; }  // stream closed — keep any partial answer
            buf += dec.decode(r.value, { stream: true });
            var blocks = buf.split("\n\n"); buf = blocks.pop();
            for (var i = 0; i < blocks.length; i++) {
              var block = blocks[i]; if (!block.trim()) continue;
              var lines = block.split("\n"); var ev = ""; var dataLines = [];
              lines.forEach(function (ln) {
                if (ln.indexOf("event:") === 0) ev = ln.slice(6).trim();
                else if (ln.indexOf("data:") === 0) {
                  var val = ln.slice(5);
                  if (val.charAt(0) === " ") val = val.slice(1);
                  dataLines.push(val);
                }
              });
              var data = dataLines.join("\n");
              if (ev === "done") {
                try { var p = JSON.parse(data); if (p.chat_id) state.chatId = p.chat_id; if (p.cited_chunks) cites = p.cited_chunks; } catch (e) {}
                console.log("[CUS] Chat done in " + (Date.now() - t0) + "ms | citations: " + (cites.length || 0));
                finish(); return;
              } else if (ev === "error") {
                // Show the backend's real (friendly) message with its trace
                // reference instead of a hardcoded "Generation failed."
                var errMsg = "Something went wrong. Please try again.";
                var errRef = "";
                try { var ed = JSON.parse(data); if (ed && ed.message) errMsg = ed.message; if (ed && ed.ref) errRef = ed.ref; } catch (e) {}
                if (errRef) errMsg += " (Ref: " + errRef + ")";
                removeTyping(); hideSpinner();
                addMsg("bot", "<p>⚠️ " + errMsg.replace(/[<>&]/g, function (c) { return {"<":"&lt;",">":"&gt;","&":"&amp;"}[c]; }) + "</p>");
                state.streaming = false;
                if (assistantHtml && !msgObj) { msgObj = addMsg("bot", renderMarkdown(assistantHtml), cites); }
                return;
              } else if (ev === "options") {
                // Structured navigation options
                removeTyping(); hideSpinner();
                try { var optsData = JSON.parse(data); renderOptions(optsData); if (optsData._query && optsData._query.corrected) trackCorrection(optsData._query); } catch (e) { addMsg("bot", "<p>⚠️ Could not load options.</p>"); }
              } else if (ev === "detail") {
                // Structured detail card
                removeTyping(); hideSpinner();
                try { var detData = JSON.parse(data); renderDetail(detData); if (detData._query && detData._query.corrected) trackCorrection(detData._query); } catch (e) { addMsg("bot", "<p>⚠️ Could not load details.</p>"); }
              } else if (ev === "service_form") {
                // Service param collection form (programme, semester, etc.)
                removeTyping(); hideSpinner();
                try { renderServiceForm(JSON.parse(data)); } catch (e) { addMsg("bot", "<p>⚠️ Could not load service form.</p>"); }
              } else if (ev === "auth_form") {
                // Student login form
                removeTyping(); hideSpinner();
                try { renderAuthForm(JSON.parse(data)); } catch (e) { addMsg("bot", "<p>⚠️ Could not load login form.</p>"); }
              } else if (ev === "grievance") {
                // Grievance intake: start the in-chat workflow with prefill
                removeTyping(); hideSpinner();
                try {
                  var gvPayload = JSON.parse(data).payload || {};
                  govStart(gvPayload.prefill || "", gvPayload.category || "");
                  // Server-detected grievance (Hinglish markers, typos, ...) —
                  // auto-preselect the office named in the message too.
                  var gvText = (gvPayload.prefill || "").trim() || text;
                  govAutoMatchAuthority(gvText);
                } catch (e) { addMsg("bot", "<p>⚠️ Could not open the grievance form.</p>"); }
              } else if (ev === "queued" || ev === "processing") {
                // Queue status events from the request manager — never render raw state.
              } else if (data) {
                assistantHtml += data;
                if (!msgObj) { removeTyping(); msgObj = addMsg("bot", ""); }
                msgObj.msgEl.innerHTML = renderMarkdown(assistantHtml);
                body.scrollTop = body.scrollHeight;
              }
            }
            return read();
          });
        }
        return read();
      }).catch(function (e) {
        if (e.name !== "AbortError") {
          removeTyping(); addMsg("bot", "<p>⚠️ Cannot connect to backend (" + API + "). Please ensure FastAPI is running.</p>");
          console.error("[CUS] Network error:", e.message || e);
        }
      }).finally(function () { state.streaming = false; hideSpinner(); });
    }).catch(function (err) {
      // ensureAuth() itself failed (register/login network error)
      removeTyping(); hideSpinner();
      console.error("[CUS] ensureAuth failed:", err.message || err);
      addMsg("bot", "<p>⚠️ Cannot reach the backend (" + API + "). Please ensure FastAPI is running.</p>");
    });

    function finish() {
      removeTyping(); hideSpinner();
      if (!msgObj && !assistantHtml) return;
      if (!msgObj) {
        msgObj = addMsg("bot", renderMarkdown(assistantHtml), cites);
      } else {
        msgObj.msgEl.innerHTML = renderMarkdown(assistantHtml);
        if (cites.length) {
          var cwrap = document.createElement("div"); cwrap.className = "cites";
          var seen = {};
          cites.forEach(function (c) {
            var id = c.document_id || c.document_title || c.source;
            if (id && seen[id]) return; if (id) seen[id] = 1;
            var el = document.createElement("span"); el.className = "cite";
            el.innerHTML = "<b>" + escapeHtml(c.document_title || c.source || "Document") + "</b>" + (c.score != null ? " · " + Number(c.score).toFixed(2) : "");
            cwrap.appendChild(el);
          });
          msgObj.msgEl.parentNode.insertBefore(cwrap, msgObj.toolsEl);
        }
      }
    }
  }

  /* =====================================================================
     Grievance workflow — IN-CHAT state machine (Phase 5)
     Modes:  idle → authorities → authority_select → composing →
             generating → review → details → final_review →
             submitting → success | error

     The whole workflow renders as a bot message INSIDE the chat — no
     overlay/modal, no page navigation. One trigger (chip / SSE event /
     natural language) → one govStart().
     ===================================================================== */
  var GOV = {
    mode: "idle",
    authorities: [],
    colleges: [],
    authority: null,
    original: "",
    category: "Other",
    draft: { generated: false, subject: "", text: "" },
    student: { name: "", roll_number: "", college: "", semester: "", email: "" },
    receipt: null,
    idemKey: null,
    submitting: false,
    panel: null,
    errorMsg: "",
  };

  function govUid() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "grv-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
  }

  function govField(id) {
    var el = GOV.panel ? GOV.panel.querySelector("#" + id) : null;
    return el ? el.value.trim() : "";
  }

  function govStart(prefill, category) {
    // A fresh grievance replaces any in-flight one (never stacks panels).
    GOV.mode = "authorities";
    GOV.authorities = [];
    GOV.colleges = [];
    GOV.authority = null;
    GOV.original = (prefill || "").trim();
    GOV.category = (category && category !== "Other") ? category : "Other";
    GOV.draft = { generated: false, subject: "", text: "" };
    GOV.student = { name: "", roll_number: "", college: "", semester: "", email: "" };
    GOV.receipt = null;
    GOV.idemKey = null;
    GOV.submitting = false;
    GOV.errorMsg = "";
    if (state.firstMsg) { suggest.classList.add("hidden"); state.firstMsg = false; }

    if (GOV.panel && GOV.panel.parentNode) GOV.panel.remove();
    var ref = addMsg("bot", '<div class="gpanel" id="cus-gpanel"></div>');
    GOV.panel = ref.msgEl.querySelector(".gpanel");
    GOV.panel.addEventListener("click", govOnClick);

    govLoadColleges();
    govLoadAuthorities();
  }

  function govLoadColleges() {
    if (GOV.colleges.length) return;
    fetch(API + "/api/college/list")
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        GOV.colleges = Array.isArray(d) ? d : (d && d.colleges ? d.colleges : []);
      })
      .catch(function () { GOV.colleges = []; });
  }

  /* ---------- Authority mention auto-detection ---------- */
  // When the user names an office in their grievance message ("I want to
  // complain to Dean Science"), the backend match endpoint resolves it
  // against REAL ACTIVE authorities. On a unique confident match the office
  // is preselected and the text-entry step opens directly — the student only
  // reviews/confirms instead of picking from the list again.
  function govWaitAuthorities(cb, tries) {
    if (GOV.authorities.length) { cb(); return; }
    if (!tries) return;
    setTimeout(function () { govWaitAuthorities(cb, tries - 1); }, 250);
  }

  function govAutoMatchAuthority(text) {
    if (!text || !text.trim()) return;
    var body = { text: String(text).slice(0, 400) };
    fetch(API + "/api/authority/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || d.status !== "matched" || !d.authority) return;
        var id = d.authority.authority_id;
        if (!id) return;
        govWaitAuthorities(function () {
          if (GOV.mode === "idle") return; // user cancelled meanwhile
          var found = null;
          for (var i = 0; i < GOV.authorities.length; i++) {
            if (GOV.authorities[i].authority_id === id) { found = GOV.authorities[i]; break; }
          }
          if (!found) return; // not in the active list — show the picker
          GOV.authority = found;
          addMsg("user", "<p>Authority: <strong>" + escapeHtml(found.authority_name) +
            "</strong> <span class=\"meta-inline\">(detected from your message)</span></p>");
          GOV.mode = "composing";
          govRender();
        }, 20);
      })
      .catch(function () { /* non-fatal: fall back to the authority list */ });
  }

  function govLoadAuthorities() {
    GOV.mode = "authorities";
    govRender();
    fetch(API + "/api/authority/active")
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        GOV.authorities = (d && d.authorities) || [];
        GOV.mode = "authority_select";
        govRender();
      })
      .catch(function () {
        GOV.mode = "error";
        GOV.errorMsg = "Unable to load authorities right now. Please try again.";
        govRender();
      });
  }

  /* ---------- Stepper ---------- */
  var GOV_STEPS = ["Authority", "Grievance", "Details", "Review"];

  function govStepIndex() {
    var m = GOV.mode;
    if (m === "authorities" || m === "authority_select") return 0;
    if (m === "composing" || m === "generating" || m === "review") return 1;
    if (m === "details") return 2;
    if (m === "final_review") return 3;
    return 0;
  }

  function govStepper() {
    var cur = govStepIndex();
    var html = '<div class="gsteps" aria-label="Grievance progress">';
    GOV_STEPS.forEach(function (s, i) {
      html += '<span class="gseg' + (cur === i ? " on" : cur > i ? " done" : "") + '">' + (i + 1) + "&nbsp;" + s + "</span>";
      if (i < GOV_STEPS.length - 1) html += '<span class="garr">›</span>';
    });
    return html + "</div>";
  }

  /* ---------- Primary action lookup (keyboard) ---------- */
  function govPrimary() {
    var b = GOV.panel ? GOV.panel.querySelector("button.gbtn.primary:not(:disabled)") : null;
    return b;
  }

  /* ---------- Renderers ---------- */
  function govRender() {
    if (!GOV.panel) return;
    var html = "";
    if (GOV.mode === "idle") return;

    if (GOV.mode === "authorities") {
      html = govRenderShell(0, '<p class="gint">Who would you like to submit your grievance to?</p><p class="gload">Loading authorities…</p>');
    } else if (GOV.mode === "authority_select") {
      var items = "";
      GOV.authorities.forEach(function (a) {
        items +=
          '<button type="button" class="gauth" data-gov="authority" data-id="' + escapeHtml(a.authority_id) + '">' +
            '<span class="gauth-name">' + escapeHtml(a.authority_name) + "</span>" +
            (a.department_name ? '<span class="gauth-dept">' + escapeHtml(a.department_name) + "</span>" : "") +
            (a.email ? '<span class="gauth-mail">' + escapeHtml(a.email) + "</span>" : "") +
          "</button>";
      });
      html = govRenderShell(0,
        '<p class="gint">Who would you like to submit your grievance to?</p>' +
        (items ? '<div class="gauth-list">' + items + "</div>"
               : '<p class="gerr">No authorities are currently available for grievance submission.</p>'),
        '<button type="button" class="gbtn ghost" data-gov="cancel">Cancel Grievance</button>');
    } else if (GOV.mode === "composing") {
      html = govRenderShell(1,
        '<p class="gint">Please describe your grievance. Type it yourself, or give a few key points and ask the AI to write a formal version.</p>' +
        '<label class="gfield" for="gv-in"><span>Your grievance / key points *</span>' +
          '<textarea id="gv-in" rows="4" placeholder="e.g. exam form is not showing for semester 3 and the last date is tomorrow">' +
            escapeHtml(GOV.original) + "</textarea></label>" +
        '<p class="ghint">Enter = new line &nbsp;·&nbsp; Ctrl+Enter = continue</p>',
        '<button type="button" class="gbtn primary" data-gov="ai-write">✨ Write formal grievance with AI</button>' +
        '<button type="button" class="gbtn" data-gov="manual">Continue with my text</button>' +
        '<button type="button" class="gbtn ghost" data-gov="back">← Back</button>' +
        '<button type="button" class="gbtn ghost" data-gov="cancel">Cancel Grievance</button>');
    } else if (GOV.mode === "generating") {
      html = govRenderShell(1,
        '<p class="gint">Writing your formal grievance…</p>' +
        '<p class="gload">The AI is drafting a respectful, professional version of your key points. It will not invent facts.</p>');
    } else if (GOV.mode === "review") {
      var note = GOV.draft.generated
        ? "💡 Here is a formal version of your grievance. You can edit it below before continuing."
        : "⚠️ The AI draft service is unavailable — please review your text below before continuing.";
      html = govRenderShell(1,
        '<p class="gint">' + note + "</p>" +
        (GOV.draft.subject ? '<p class="gsub">Subject: <strong>' + escapeHtml(GOV.draft.subject) + "</strong></p>" : "") +
        '<label class="gfield" for="gv-txt"><span>Grievance text *</span>' +
          '<textarea id="gv-txt" rows="7">' + escapeHtml(GOV.draft.text) + "</textarea></label>" +
        '<p class="ghint">Ctrl+Enter = accept &amp; continue</p>',
        '<button type="button" class="gbtn primary" data-gov="accept-draft">✔ Accept &amp; Continue</button>' +
        '<button type="button" class="gbtn ghost" data-gov="back">← Back</button>' +
        '<button type="button" class="gbtn ghost" data-gov="cancel">Cancel Grievance</button>');
    } else if (GOV.mode === "details") {
      html = govRenderShell(2,
        '<p class="gint">Almost done — we need a few details so we can reach you about this grievance. These are kept private.</p>' +
        '<div class="ggrid">' +
          '<label class="gfield" for="gv-name"><span>First name *</span><input id="gv-name" maxlength="200" value="' + escapeHtml(GOV.student.name) + '"></label>' +
          '<label class="gfield" for="gv-roll"><span>Roll number *</span><input id="gv-roll" maxlength="50" value="' + escapeHtml(GOV.student.roll_number) + '"></label>' +
          '<label class="gfield" for="gv-college"><span>College *</span>' + govCollegeSelect() + "</label>" +
          '<label class="gfield" for="gv-sem"><span>Semester *</span><select id="gv-sem">' + govSemOptions() + "</select></label>" +
          '<label class="gfield gfull" for="gv-email"><span>Email *</span><input id="gv-email" type="email" maxlength="200" value="' + escapeHtml(GOV.student.email) + '"></label>' +
        "</div>",
        '<button type="button" class="gbtn primary" data-gov="to-review">Continue</button>' +
        '<button type="button" class="gbtn ghost" data-gov="back">← Back</button>' +
        '<button type="button" class="gbtn ghost" data-gov="cancel">Cancel Grievance</button>');
    } else if (GOV.mode === "final_review") {
      html = govRenderShell(3,
        '<p class="gint">Review your grievance before submitting.</p>' +
        '<div class="grev">' +
          '<div class="grev-row"><span class="grev-l">Authority</span><span class="grev-v">' + escapeHtml(GOV.authority.authority_name) +
            (GOV.authority.email ? " &lt;" + escapeHtml(GOV.authority.email) + "&gt;" : "") + "</span></div>" +
          '<div class="grev-row"><span class="grev-l">Name</span><span class="grev-v">' + escapeHtml(GOV.student.name) + "</span></div>" +
          '<div class="grev-row"><span class="grev-l">Roll number</span><span class="grev-v">' + escapeHtml(GOV.student.roll_number) + "</span></div>" +
          '<div class="grev-row"><span class="grev-l">College</span><span class="grev-v">' + escapeHtml(GOV.student.college) + "</span></div>" +
          '<div class="grev-row"><span class="grev-l">Semester</span><span class="grev-v">' + escapeHtml(GOV.student.semester) + "</span></div>" +
          '<div class="grev-row"><span class="grev-l">Email</span><span class="grev-v">' + escapeHtml(GOV.student.email) + "</span></div>" +
          '<div class="grev-txt"><span class="grev-l">Grievance</span><p>' + escapeHtml(GOV.draft.text) + "</p></div>" +
        "</div>",
        '<button type="button" class="gbtn primary" data-gov="submit">Submit Grievance</button>' +
        '<button type="button" class="gbtn" data-gov="edit-details">Edit Details</button>' +
        '<button type="button" class="gbtn" data-gov="edit-grievance">Edit Grievance</button>' +
        '<button type="button" class="gbtn ghost" data-gov="back">← Back</button>' +
        '<button type="button" class="gbtn ghost" data-gov="cancel">Cancel Grievance</button>');
    } else if (GOV.mode === "submitting") {
      html = govRenderShell(0, '<p class="gint">Submitting your grievance…</p><p class="gload">Please wait — this can take a few seconds.</p>');
    } else if (GOV.mode === "success") {
      html = govRenderSuccess();
    } else if (GOV.mode === "error") {
      html = govRenderShell(0,
        '<p class="gint">⚠️ ' + escapeHtml(GOV.errorMsg) + "</p>",
        '<button type="button" class="gbtn primary" data-gov="retry">Retry</button>' +
        '<button type="button" class="gbtn ghost" data-gov="back">← Back</button>' +
        '<button type="button" class="gbtn ghost" data-gov="cancel">Cancel Grievance</button>');
    }
    GOV.panel.innerHTML = html;
    body.scrollTop = body.scrollHeight;
    setTimeout(function () {
      var f = GOV.panel ? GOV.panel.querySelector("textarea") : null;
      if (f) f.focus();
    }, 40);
  }

  function govCollegeSelect() {
    if (!GOV.colleges.length) {
      return '<select id="gv-college" disabled><option value="">Loading colleges…</option></select>';
    }
    var html = '<select id="gv-college"><option value="">-- Select college --</option>';
    GOV.colleges.forEach(function (c) {
      var id = c.id || c.college_id || "";
      var name = c.name || c.title || id;
      html += '<option value="' + escapeHtml(name) + '"' + (GOV.student.college === name ? " selected" : "") + ">" + escapeHtml(name) + "</option>";
    });
    return html + "</select>";
  }

  function govSemOptions() {
    var html = '<option value="">-- Select --</option>';
    for (var i = 1; i <= 8; i++) {
      html += '<option value="' + i + '"' + (String(GOV.student.semester) === String(i) ? " selected" : "") + ">" + i + "</option>";
    }
    return html;
  }

  function govRenderShell(step, inner, actions) {
    return (
      '<div class="gtitle">📋 File a Grievance</div>' +
      govStepper() +
      inner +
      (actions ? '<div class="gactions">' + actions + "</div>" : "") +
      '<button type="button" class="gbtn ghost g-cancel-x" data-gov="cancel">✕ Cancel</button>'
    );
  }

  function govRenderSuccess() {
    var d = GOV.receipt || {};
    var auth = GOV.authority ? GOV.authority.authority_name : "the concerned office";
    var studentEmail = escapeHtml((GOV.student && GOV.student.email) || "");
    var mailState = [];
    if (d.authority_email_status === "sent") mailState.push("A copy has been emailed to <strong>" + escapeHtml(auth) + "</strong>.");
    else if (d.authority_email_status === "failed") mailState.push("⚠️ Your grievance is saved, but the copy to <strong>" + escapeHtml(auth) + "</strong> could not be delivered right now and has been logged for retry. Keep your reference number safe.");
    else mailState.push("ℹ️ The copy to the office could not be emailed yet — your grievance is saved and will be routed.");
    if (d.email_confirmed) mailState.push("A confirmation email has been sent to <strong>" + (studentEmail || "your address") + "</strong>.");
    else mailState.push("⚠️ Your grievance is saved, but the confirmation email could not be delivered right now and has been logged for retry — keep your reference number and tracking token safe.");
    var html =
      '<div class="gsucc">' +
        '<div class="gs-ok">✅</div>' +
        '<h3 class="gs-title">Grievance Submitted Successfully</h3>' +
        '<p class="gs-line">Your grievance has been submitted to</p>' +
        '<p class="gs-auth">' + escapeHtml(auth) + "</p>" +
        '<p class="gs-ref-l">Reference Number</p>' +
        '<div class="gs-ref">' + escapeHtml(d.reference || "") +
          '<button type="button" class="gs-copy" data-gov="copy" data-copy="' + escapeHtml(d.reference || "") + '">Copy</button></div>' +
        '<p class="gs-tokenl">One-time tracking token (shown only once)</p>' +
        '<div class="gs-token">' + escapeHtml(d.tracking_token || "") +
          '<button type="button" class="gs-copy" data-gov="copy" data-copy="' + escapeHtml(d.tracking_token || "") + '">Copy</button></div>' +
        '<p class="gs-msg">Status: <strong>' + escapeHtml(d.status || "submitted") + "</strong></p>" +
        '<p class="gs-mail">' + mailState.join(" ") + "</p>" +
        '<div class="gactions">' +
          '<button type="button" class="gbtn primary" data-gov="track">🔎 Track Grievance</button>' +
          '<button type="button" class="gbtn" data-gov="done">Return to Chat</button>' +
        "</div>" +
      "</div>";
    return html;
  }

  /* ---------- Actions ---------- */
  function govOnClick(e) {
    var btn = e.target.closest("button[data-gov]");
    if (!btn) return;
    var act = btn.getAttribute("data-gov");
    if (act === "copy") { if (copyText2(btn.getAttribute("data-copy"))) toast("Copied to clipboard"); return; }
    if (act === "cancel") { govAskCancel(); return; }
    if (act === "cancel-yes") {
      GOV.mode = "idle";
      if (GOV.panel) { GOV.panel.remove(); GOV.panel = null; }
      toast("Grievance cancelled");
      return;
    }
    if (act === "cancel-no") { govRender(); return; }
    if (act === "authority") {
      var id = btn.getAttribute("data-id");
      for (var i = 0; i < GOV.authorities.length; i++) {
        if (GOV.authorities[i].authority_id === id) {
          GOV.authority = GOV.authorities[i];
          break;
        }
      }
      if (!GOV.authority) { GOV.mode = "error"; GOV.errorMsg = "That authority is no longer available. Please choose another."; govRender(); return; }
      addMsg("user", "<p>Authority: <strong>" + escapeHtml(GOV.authority.authority_name) + "</strong></p>");
      GOV.mode = "composing";
      govRender();
      return;
    }
    if (act === "back") {
      if (GOV.mode === "review" || GOV.mode === "composing") { GOV.mode = "authority_select"; govRender(); return; }
      if (GOV.mode === "details") { GOV.mode = "review"; govRender(); return; }
      if (GOV.mode === "final_review") { GOV.mode = setFinalReviewBack(); govRender(); return; }
      if (GOV.mode === "error") { GOV.mode = "authority_select"; govRender(); return; }
      return;
    }
    if (act === "ai-write") { govAIDraft(); return; }
    if (act === "manual") { govManualDraft(); return; }
    if (act === "accept-draft") { govAcceptDraft(); return; }
    if (act === "to-review") { govValidateDetails(); return; }
    if (act === "edit-details") { GOV.mode = "details"; govRender(); return; }
    if (act === "edit-grievance") { GOV.mode = "review"; govRender(); return; }
    if (act === "submit") { govSubmit(); return; }
    if (act === "retry") { govLoadAuthorities(); return; }
    if (act === "done") { GOV.mode = "idle"; if (GOV.panel) { GOV.panel.remove(); GOV.panel = null; } addMsg("bot", "<p>You can start another grievance anytime — just say <strong>grievance</strong> or tap <strong>File a Grievance</strong>.</p>"); return; }
    if (act === "track") { govTrack(); return; }
  }

  function setFinalReviewBack() { return "details"; }

  function govAskCancel() {
    if (!GOV.panel) return;
    var old = GOV.panel.querySelector(".gconf");
    if (old) old.remove();
    var box = document.createElement("div");
    box.className = "gconf";
    box.innerHTML =
      '<p>Are you sure you want to cancel? Your entered information will be lost.</p>' +
      '<div class="gactions"><button type="button" class="gbtn primary" data-gov="cancel-yes">Yes, Cancel</button>' +
      '<button type="button" class="gbtn ghost" data-gov="cancel-no">Keep Editing</button></div>';
    GOV.panel.appendChild(box);
    body.scrollTop = body.scrollHeight;
  }

  function govAIDraft() {
    var raw = govField("gv-in");
    if (raw.length < 3) { toast("Please describe the problem first (a few words are enough)."); return; }
    GOV.original = raw;
    GOV.mode = "generating";
    govRender();
    fetch(API + "/api/grievances/draft/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: raw })
    }).then(function (r) { return r.json(); }).then(function (d) {
      GOV.draft.generated = !!d.generated;
      GOV.draft.subject = d.subject || "";
      GOV.draft.text = (d.text && d.text.trim()) ? d.text : raw;
      GOV.mode = "review";
      govRender();
    }).catch(function () {
      GOV.draft.generated = false; GOV.draft.subject = ""; GOV.draft.text = raw;
      GOV.mode = "review";
      govRender();
    });
  }

  function govManualDraft() {
    var raw = govField("gv-in");
    if (raw.length < 10) { toast("Please write at least 10 characters."); return; }
    GOV.original = raw;
    GOV.draft = { generated: false, subject: "", text: raw };
    GOV.mode = "review";
    govRender();
  }

  function govAcceptDraft() {
    var txt = govField("gv-txt");
    if (txt.length < 10) { toast("Your grievance text must be at least 10 characters."); return; }
    GOV.draft.text = txt;
    GOV.mode = "details";
    govRender();
  }

  function govValidateDetails() {
    var name = govField("gv-name");
    var roll = govField("gv-roll");
    var college = govField("gv-college");
    var sem = govField("gv-sem");
    var email = govField("gv-email");
    var nameRe = /^[A-Za-z][A-Za-z .'-]{1,199}$/;
    if (!nameRe.test(name)) { toast("Please enter a valid first name (2+ letters)."); return; }
    if (!roll) { toast("Please enter your roll number."); return; }
    if (!college) { toast("Please select your college."); return; }
    if (!sem) { toast("Please select your semester."); return; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { toast("Please enter a valid email address."); return; }
    GOV.student.name = name;
    GOV.student.roll_number = roll;
    GOV.student.college = college;
    GOV.student.semester = sem;
    GOV.student.email = email;
    GOV.mode = "final_review";
    govRender();
  }

  function govSubmit() {
    if (GOV.submitting) { toast("Please wait — submitting…"); return; }
    if (!GOV.authority) { toast("Please select an authority first."); return; }
    var txt = GOV.draft.text;
    if (!txt || txt.length < 10) { toast("Please finalise your grievance text first."); return; }
    GOV.submitting = true;
    GOV.mode = "submitting";
    govRender();
    if (!GOV.idemKey) GOV.idemKey = govUid();
    fetch(API + "/api/grievances", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        student: {
          name: GOV.student.name, email: GOV.student.email,
          roll_number: GOV.student.roll_number || null,
          semester: GOV.student.semester || null,
          college: GOV.student.college || null
        },
        original_input: GOV.original || null,
        final_text: txt,
        category: GOV.category || "Other",
        authority_id: GOV.authority.authority_id,
        idempotency_key: GOV.idemKey
      })
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) {
          var msg = "";
          if (d && d.detail) {
            if (Array.isArray(d.detail) && d.detail.length) msg = d.detail[0].msg || "";
            else msg = String(d.detail);
          } else if (d && d.error) msg = d.error.message || String(d.error);
          if (!msg) msg = "HTTP " + r.status;
          throw new Error(msg);
        }
        return d;
      });
    }).then(function (d) {
      GOV.receipt = d;
      GOV.idemKey = null; // success → next submission is a fresh one
      GOV.mode = "success";
      govRender();
      toast("Grievance submitted ✅");
    }).catch(function (err) {
      GOV.submitting = false;
      GOV.mode = "error";
      GOV.errorMsg = err && err.message ? err.message : "Submission failed — please try again.";
      govRender();
    });
  }

  function govTrack() {
    var d = GOV.receipt || {};
    if (!d.reference) return;
    addMsg("user", "<p>Track grievance <strong>" + escapeHtml(d.reference) + "</strong></p>");
    fetch(API + "/api/grievances/" + encodeURIComponent(d.reference) + "/verify?token=" + encodeURIComponent(d.tracking_token || ""))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (v) {
        addMsg("bot",
          "<p>📋 <strong>" + escapeHtml(v.reference) + "</strong></p>" +
          "<p>Status: <strong>" + escapeHtml(v.status || "submitted") + "</strong></p>" +
          "<p>Route: " + escapeHtml(v.authority_name || "Pending assignment") +
          (v.department_name ? " (" + escapeHtml(v.department_name) + ")" : "") + "</p>" +
          (v.submitted_at ? "<p>Submitted: " + escapeHtml(String(v.submitted_at).slice(0, 19).replace("T", " ")) + "</p>" : ""));
      })
      .catch(function () {
        addMsg("bot", "<p>⚠️ Unable to check the status right now. Please try again later.</p>");
      });
  }

  /* ---------- Keyboard behaviour ---------- */
  function govKeydown(e) {
    if (GOV.mode === "idle" || !GOV.panel) return;
    var el = e.target;
    if (el.nodeType !== 1 || !GOV.panel.contains(el)) return;
    var tag = el.tagName;
    var isTextarea = tag === "TEXTAREA";
    var isInput = tag === "INPUT" || tag === "SELECT";
    if (e.key !== "Enter") return;
    if (isTextarea) {
      // Enter → newline · Ctrl/Cmd+Enter → primary action
      if (!(e.ctrlKey || e.metaKey)) return; // default newline behaviour
      e.preventDefault();
      var p = govPrimary();
      if (p) p.click();
      return;
    }
    if (isInput) {
      // Single-line inputs: Enter submits the step
      if (e.shiftKey) return;
      e.preventDefault();
      var pri = govPrimary();
      if (pri) pri.click();
    }
  }

  function copyText2(t) {
    if (!t) return false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).catch(function () { fallbackCopy(t); });
    } else fallbackCopy(t);
    return true;
  }
  function fallbackCopy(t) {
    var ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    ta.remove();
  }

  /* ---------- Suggest chips ---------- */
  suggest.addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    if (btn.getAttribute("data-chip") === "File a Grievance") {
      govStart("", "");
      return;
    }
    input.value = btn.textContent.trim();
    autosize();
    sendClick();
  });

  /* ---------- Public API ---------- */
  window.CUS.openChat = function () {
    panel.classList.add("open"); backdrop.classList.add("open");
    bubble.style.display = "none"; badge.classList.remove("show");
    panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "CUS AI Assistant");
    setTimeout(function () { input.focus(); }, 100);
    if (!body.children.length) addGreeting();
  };
  function closeChat() {
    panel.classList.add("closing");
    setTimeout(function () { panel.classList.remove("open", "closing"); backdrop.classList.remove("open"); }, 250);
    bubble.style.display = "grid"; bubble.focus();
  }

  /* ---------- Events ---------- */
  bubble.addEventListener("click", function () { window.CUS.openChat(); });
  backdrop.addEventListener("click", closeChat);
  root.querySelector(".act-close").addEventListener("click", closeChat);
  root.querySelector(".act-clear").addEventListener("click", function () {
    if (state.streaming) return;
    body.innerHTML = ""; state.chatId = null; state.firstMsg = true;
    suggest.classList.remove("hidden");
    addGreeting(); toast("Chat cleared");
  });
  sendBtn.addEventListener("click", sendClick);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey || !e.shiftKey)) { e.preventDefault(); sendClick(); }
  });
  input.addEventListener("input", autosize);
  // Grievance panel keyboard: Enter=newline in textareas, Ctrl+Enter=primary
  // action, Enter=primary action in single-line inputs. Never hijacks the
  // main chat input (that keeps its own Enter-to-send behaviour above).
  document.addEventListener("keydown", govKeydown);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (state.streaming && state.controller) { state.controller.abort(); removeTyping(); hideSpinner(); addMsg("bot", "<p>⏹ Generation stopped.</p>"); state.streaming = false; sendBtn.disabled = false; }
      else if (panel.classList.contains("open")) { closeChat(); }
    }
  });

  /* ---------- Init ---------- */
  console.log("[CUS] Initializing on " + API + (state.token ? " (token found)" : " (no token)"));
  // Pre-auth at init so first message is fast
  if (!state.token) {
    ensureAuth().then(function () {
      console.log("[CUS] Pre-auth complete");
    }).catch(function (err) {
      console.warn("[CUS] Pre-auth failed — will retry on first message:", err.message || err);
    }).finally(function () { _authPromise = null; });
  }
  addGreeting();
  setTimeout(function () { badge.classList.add("show"); }, 1200);
})();
