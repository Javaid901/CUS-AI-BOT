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

  /* ---------- Build DOM ---------- */
  var root = document.createElement("div");
  root.id = "cusw";
  root.innerHTML =
    '<button class="bubble" title="Chat with CUS AI" aria-label="Open chat">CUS<span class="badge">AI</span></button>' +
    '<div class="backdrop"></div>' +
    '<div class="panel" role="dialog" aria-label="CUS AI Assistant">' +
      '<div class="head">' +
        '<div class="head-brand">' +
          '<div class="head-logo">CUS</div>' +
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
    html += '<div class="opts">';
    items.forEach(function (o) {
      html += '<button class="chip" data-role="option" data-value="' + escapeHtml(o.id) + '">' + escapeHtml(o.label) + '</button>';
    });
    html += '<button class="chip back" data-role="option" data-value="back">← Back</button>';
    html += "</div>";
    addMsg("bot", html, null, context, queryMeta);
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
            if (r.done) return;
            buf += dec.decode(r.value, { stream: true });
            var blocks = buf.split("\n\n"); buf = blocks.pop();
            for (var i = 0; i < blocks.length; i++) {
              var block = blocks[i].trim(); if (!block) continue;
              var lines = block.split("\n"); var ev = ""; var dataLines = [];
              lines.forEach(function (ln) {
                if (ln.indexOf("event:") === 0) ev = ln.slice(6).trim();
                else if (ln.indexOf("data:") === 0) dataLines.push(ln.slice(5).trim());
              });
              var data = dataLines.join("\n");
              if (ev === "done") {
                try { var p = JSON.parse(data); if (p.chat_id) state.chatId = p.chat_id; if (p.cited_chunks) cites = p.cited_chunks; } catch (e) {}
                console.log("[CUS] Chat done in " + (Date.now() - t0) + "ms | citations: " + (cites.length || 0));
                finish(); return;
              } else if (ev === "error") {
                removeTyping(); hideSpinner(); addMsg("bot", "<p>⚠️ Generation failed.</p>"); state.streaming = false; return;
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

  /* ---------- Suggest chips ---------- */
  suggest.addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
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
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendClick(); }
  });
  input.addEventListener("input", autosize);
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
