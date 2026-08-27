/* Authority Admin Portal controller — Cluster University of Srinagar.
   Reuses the login endpoint (/api/auth/login) and the CUS admin design system.
   Role routing: authority_admin -> this portal; superadmin -> admin.html;
   student -> rejected with a clear message. */
(function () {
  "use strict";

  if (!window.CUS_API_BASE) throw new Error("CUS_API_BASE not defined");
  var API = window.CUS_API_BASE;

  var token = localStorage.getItem("cus_authority_token") || null;
  function authHeaders() { var h = {}; if (token) h.Authorization = "Bearer " + token; return h; }
  function setToken(t) {
    token = t;
    if (t) localStorage.setItem("cus_authority_token", t); else localStorage.removeItem("cus_authority_token");
  }
  function handleUnauthorized() { console.warn("[AuthorityAdmin] Unauthorized"); setToken(null); showLogin(); }

  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    if (typeof s !== "string") s = String(s == null ? "" : s);
    return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]; });
  };
  var fmtDate = function (iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(iso);
    return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  var fmtDateShort = function (iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(iso);
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  };

  // ===== Toast =====
  var toastContainer = $("toastContainer");
  function toast(msg, type) {
    type = type || "info";
    var icons = { success: "\u2705", error: "\u274c", info: "\u2139\ufe0f", warning: "\u26a0\ufe0f" };
    var el = document.createElement("div");
    el.className = "toast toast-" + type;
    el.innerHTML = '<span class="toast-icon">' + (icons[type] || "") + "</span><span>" + esc(msg) + "</span><button class=\"toast-close\">&times;</button>";
    el.querySelector(".toast-close").onclick = function () { el.classList.add("removing"); setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 300); };
    toastContainer.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, type === "error" ? 6000 : 3500);
  }

  // ===== Human-readable API errors (never [object Object]) =====
  function apiError(res, fallback) {
    var d = res && res.data;
    if (typeof d !== "object" || d === null) return fallback || "Request failed. Please try again.";
    if (d.detail) return typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    if (d.error && d.error.message) return String(d.error.message);
    if (d.message) return String(d.message);
    if (Array.isArray(d.detail) && d.detail[0] && d.detail[0].msg) return String(d.detail[0].msg);
    return fallback || "Request failed. Please try again.";
  }

  function apiJson(url, method, body) {
    var opts = { method: method || "GET", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()) };
    if (body) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
    }).catch(function () { return { ok: false, status: 0, data: { detail: "Cannot reach the server. Please try again." } }; });
  }

  // ===== Status + read badges =====
  var STATUS_LABELS = { submitted: "Submitted", acknowledged: "Acknowledged", in_progress: "In Progress", resolved: "Resolved", closed: "Closed", rejected: "Rejected" };
  function statusBadge(s) { return '<span class="aa-badge st-' + esc(s || "submitted") + '">' + esc(STATUS_LABELS[s] || s || "Submitted") + "</span>"; }
  function readBadge(r) { return r ? '<span class="aa-badge rd-read">Read</span>' : '<span class="aa-badge rd-unread">● New</span>'; }

  // ===== Views =====
  var loginView = $("loginView"), dashView = $("dashView");
  var views = { dashboard: $("viewDashboard"), grievances: $("viewGrievances"), detail: $("viewDetail"), profile: $("viewProfile") };
  function showView(name) {
    Object.keys(views).forEach(function (k) { views[k].style.display = (k === name) ? "block" : "none"; });
    document.querySelectorAll(".aa-nav-btn").forEach(function (b) { b.classList.toggle("active", b.dataset.view === name); });
    if (name === "dashboard") loadDashboard();
    if (name === "grievances") loadGrievances();
    if (name === "profile") loadProfile();
    if (name === "detail" && !currentDetail) { showView("grievances"); }
  }

  function showDash() {
    loginView.style.display = "none"; dashView.style.display = "block";
    $("userLabel").style.display = "inline"; $("logoutBtn").style.display = "inline";
    $("userLabel").textContent = currentUser ? (currentUser.full_name || currentUser.username) : "Authority Admin";
    loadProfileMeta();
  }
  function showLogin() {
    setToken(null);
    currentUser = null;
    dashView.style.display = "none"; loginView.style.display = "block";
    $("userLabel").style.display = "none"; $("logoutBtn").style.display = "none";
  }

  // ===== Login (existing endpoint; role-based routing) =====
  var currentUser = null;
  var loginBusy = false;
  $("loginForm").addEventListener("submit", function (e) {
    e.preventDefault();
    if (loginBusy) return;
    var f = e.target;
    $("loginError").style.display = "none";
    loginBusy = true; $("loginBtn").disabled = true; $("loginBtn").textContent = "Signing in…";
    fetch(API + "/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "username=" + encodeURIComponent(f.username.value.trim()) + "&password=" + encodeURIComponent(f.password.value),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); })
      .then(function (res) {
        loginBusy = false; $("loginBtn").disabled = false; $("loginBtn").textContent = "Login";
        if (!res.ok || !res.data.access_token) {
          var msg = apiError(res, "Invalid username or password.") || "Invalid username or password.";
          $("loginError").textContent = msg; $("loginError").style.display = "block";
          return;
        }
        var identity = res.data.user || {};
        var role = identity.role;
        if (role === "superadmin") {
          // Keep the existing Super Admin panel behavior completely unchanged.
          localStorage.setItem("cus_admin_token", res.data.access_token);
          window.location.href = "admin.html";
          return;
        }
        if (role !== "authority_admin" || !identity.authority_id) {
          $("loginError").textContent = "This account is not an Authority Admin. Student accounts cannot access this portal.";
          $("loginError").style.display = "block";
          return;
        }
        setToken(res.data.access_token);
        currentUser = identity;
        showDash();
      loadDashboard();
      })
      .catch(function () {
        loginBusy = false; $("loginBtn").disabled = false; $("loginBtn").textContent = "Sign In";
        $("loginError").style.display = "block";
      });
  });

  $("logoutBtn").addEventListener("click", function () { showLogin(); });

  // ===== Profile / authority meta =====
  function loadProfileMeta() {
    apiJson(API + "/api/authority-admin/profile").then(function (res) {
      if (!res.ok) { if (res.status === 401) handleUnauthorized(); return; }
      var p = res.data;
      currentUser = p;
      $("userLabel").textContent = (p.full_name || p.username) + " · " + (p.authority && p.authority.authority_name || "");
      $("aaAuthName").textContent = (p.authority && p.authority.authority_name) || "Authority Administration Portal";
      var sub = "Welcome, " + esc((p.full_name || p.username)) + " — " + esc((p.authority && p.authority.department_name) || "Authority Admin");
      if (p.last_login) sub += "<br><span class='muted'>Last login: " + fmtDate(p.last_login) + "</span>";
      $("aaSub").innerHTML = sub;
    });
  }

  // ===== Dashboard =====
  function loadDashboard() {
    $("aaKpis").innerHTML = '<p class="aa-loading">Loading dashboard…</p>';
    $("dashRecentRows").innerHTML = '<tr><td colspan="7" class="aa-loading">Loading…</td></tr>';
    apiJson(API + "/api/authority-admin/dashboard").then(function (res) {
      if (!res.ok) {
        if (res.status === 401) { handleUnauthorized(); return; }
        $("aaKpis").innerHTML = '<p class="aa-loading">' + esc(apiError(res, "Failed to load the dashboard. Please try again.")) + "</p>";
        return;
      }
      var d = res.data;
      renderKpis(d);
      renderRecent(d.recent || []);
      $("navUnread").style.display = d.unread > 0 ? "inline-block" : "none";
      $("navUnread").textContent = d.unread;
    });
  }

  function renderKpis(d) {
    var cards = [
      { n: d.total, l: "Total", icon: "📥" },
      { n: d.unread, l: "Unread", icon: "🔔" },
      { n: d.in_progress, l: "In Progress", icon: "⚙️" },
      { n: d.resolved, l: "Resolved", icon: "✅" },
      { n: d.closed, l: "Closed", icon: "🔒" },
    ];
    $("aaKpis").innerHTML = cards.map(function (c) {
      return '<div class="aa-kpi"><div class="aa-kpi-icon">' + c.icon + "</div><div><div class='aa-kpi-n'>" + esc(String(c.n)) + "</div><div class='aa-kpi-l'>" + esc(c.l) + "</div></div></div>";
    }).join("");
  }

  function renderRecent(rows) {
    if (!rows.length) { $("dashRecentRows").innerHTML = '<tr><td colspan="7" class="aa-loading">No grievances yet.</td></tr>'; return; }
    $("dashRecentRows").innerHTML = rows.map(function (g) {
      return "<tr>" +
        "<td class='auth-cell-name'>" + esc(g.reference) + "</td>" +
        "<td>" + esc(g.student_name || "—") + "</td>" +
        "<td class='auth-cell-dept'>" + esc(g.category || "Grievance") + "</td>" +
        "<td>" + statusBadge(g.status) + "</td>" +
        "<td>" + readBadge(g.is_read) + "</td>" +
        "<td>" + fmtDateShort(g.created_at) + "</td>" +
        "<td><button class='btn sm ghost' data-open='" + esc(g.id) + "'>Open</button></td>" +
        "</tr>";
    }).join("");
  }

  // ===== Grievance list (backend-enforced search + filters + pagination) =====
  var gState = { page: 1, pageSize: 15, q: "", status: "", read: "", from: "", to: "", total: 0 };
  var gBusy = false;

  $("gfApply").addEventListener("click", function () {
    gState.q = $("gfQ").value.trim();
    gState.status = $("gfStatus").value;
    gState.read = $("gfRead").value;
    gState.from = $("gfFrom").value;
    gState.to = $("gfTo").value;
    gState.page = 1;
    loadGrievances();
  });
  $("gfReset").addEventListener("click", function () {
    $("gfQ").value = ""; $("gfStatus").value = ""; $("gfRead").value = ""; $("gfFrom").value = ""; $("gfTo").value = "";
    gState = { page: 1, pageSize: 15, q: "", status: "", read: "", from: "", to: "", total: 0 };
    loadGrievances();
  });
  $("gfQ").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); $("gfApply").click(); } });

  function loadGrievances() {
    if (gBusy) return;
    gBusy = true;
    $("grievanceRows").innerHTML = '<tr><td colspan="10" class="aa-loading">Loading grievances…</td></tr>';
    var params = ["page=" + gState.page, "page_size=" + gState.pageSize];
    if (gState.q) params.push("q=" + encodeURIComponent(gState.q));
    if (gState.status) params.push("status=" + encodeURIComponent(gState.status));
    if (gState.read) params.push("read=" + encodeURIComponent(gState.read));
    if (gState.from) params.push("date_from=" + encodeURIComponent(gState.from + "T00:00:00"));
    if (gState.to) params.push("date_to=" + encodeURIComponent(gState.to + "T23:59:59"));
    apiJson(API + "/api/authority-admin/grievances?" + params.join("&")).then(function (res) {
      gBusy = false;
      if (!res.ok) {
        if (res.status === 401) { handleUnauthorized(); return; }
        $("grievanceRows").innerHTML = '<tr><td colspan="10" class="aa-loading">' + esc(apiError(res, "Failed to load grievances. Please try again.")) + "</td></tr>";
        $("aaPager").innerHTML = "";
        return;
      }
      var d = res.data;
      gState.total = d.total || 0;
      gState.unreadTotal = d.unread_total || 0;
      $("navUnread").style.display = gState.unreadTotal > 0 ? "inline-block" : "none";
      $("navUnread").textContent = gState.unreadTotal;
      renderGrievanceRows(d.items || []);
      renderPager();
    });
  }

  function renderGrievanceRows(rows) {
    if (!rows.length) { $("grievanceRows").innerHTML = '<tr><td colspan="10" class="aa-loading">No grievances match your filters.</td></tr>'; return; }
    $("grievanceRows").innerHTML = rows.map(function (g) {
      return "<tr>" +
        "<td class='auth-cell-name'>" + esc(g.reference) + "</td>" +
        "<td>" + esc(g.student_name || "—") + "</td>" +
        "<td>" + esc(g.roll_number || "—") + "</td>" +
        "<td class='auth-cell-dept'>" + esc(g.college || "—") + "</td>" +
        "<td>" + esc(g.semester || "—") + "</td>" +
        "<td class='auth-cell-dept'>" + esc(g.category || "Grievance") + "</td>" +
        "<td>" + statusBadge(g.status) + "</td>" +
        "<td>" + readBadge(g.is_read) + "</td>" +
        "<td>" + fmtDateShort(g.created_at) + "</td>" +
        "<td><button class='btn sm green' data-open='" + esc(g.id) + "'>Open</button></td>" +
        "</tr>";
    }).join("");
  }

  function renderPager() {
    var pages = Math.max(1, Math.ceil(gState.total / gState.pageSize));
    if (pages <= 1) { $("aaPager").innerHTML = '<span class="muted">' + gState.total + " grievance(s)</span>"; return; }
    $("aaPager").innerHTML =
      '<button class="btn sm ghost" id="pgPrev"' + (gState.page <= 1 ? " disabled" : "") + ">← Prev</button> " +
      '<span style="margin:0 10px;font-size:13px;color:var(--navy);font-weight:600;">Page ' + gState.page + " of " + pages + "</span>" +
      '<button class="btn sm ghost" id="pgNext"' + (gState.page >= pages ? " disabled" : "") + ">Next →</button>";
    $("pgPrev").onclick = function () { if (gState.page > 1) { gState.page--; loadGrievances(); } };
    $("pgNext").onclick = function () { if (gState.page < pages) { gState.page++; loadGrievances(); } };
  }

  // ===== Detail =====
  var currentDetail = null;
  var detailBusy = false;
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-open]");
    if (btn) openDetail(btn.dataset.open);
  });

  $("detailBack").addEventListener("click", function () {
    currentDetail = null;
    showView("grievances");
    loadGrievances();
    loadDashboard(); // update unread counts
  });

  function openDetail(id) {
    currentDetail = { id: id };
    showView("detail");
    $("detailBody").innerHTML = '<p class="aa-loading">Loading grievance…</p>';
    apiJson(API + "/api/authority-admin/grievances/" + encodeURIComponent(id)).then(function (res) {
      if (!res.ok) {
        if (res.status === 401) { handleUnauthorized(); return; }
        $("detailBody").innerHTML = '<div class="admin-card"><p>' + esc(apiError(res, "Failed to load the grievance. Please try again.")) + "</p></div>";
        return;
      }
      currentDetail = res.data;
      renderDetail(res.data);
      refreshDashQuietly();
    });
  }

  function renderDetail(g) {
    var history = (g.history || []).slice().reverse().map(function (h) {
      var icon = h.is_internal ? "🛠" : "📨";
      return "<li><span class='aa-hist-ic'>" + icon + "</span><div><div>" +
        "<b>" + esc(h.new_status === h.previous_status ? "Response recorded" : (STATUS_LABELS[h.new_status] || h.new_status)) + "</b>" +
        " — <span class='muted'>" + esc(h.changed_by || "system") + " (" + esc((h.changed_by_role || "system").replace("_", " ")) + ")</span>" +
        "</div><div class='muted'>" + fmtDate(h.created_at) + "</div>" +
        (h.comment ? "<div class='aa-hist-comment'>" + esc(h.comment) + "</div>" : "") +
        "</div></li>";
    }).join("");

    var responseBox = g.authority_response
      ? '<div class="aa-block"><h3>Authority Response' +
        '<span class="muted" style="font-weight:400;font-size:12px;margin-left:8px;">' + fmtDate(g.authority_response_at) +
        (g.response_email_status ? " · email: " + esc(g.response_email_status) : "") + "</span></h3>" +
        '<div class="aa-response">' + esc(g.authority_response) + "</div></div>"
      : '<div class="aa-block aa-response-form" id="responseForm"><h3>Authority Response</h3>' +
        "<p class='muted' style='margin-bottom:8px;font-size:13px;'>Send the official outcome to the student by email.</p>" +
        "<textarea id='respText' class='aa-input' rows='4' maxlength='5000' placeholder='Write the official response sent to the student…'></textarea>" +
        "<div class='aa-actions'><button class='btn green' id='respSend' style='margin-top:8px;'>✉️ Send Response</button></div></div>";

    $("detailBody").innerHTML =
      '<div class="admin-card"><div class="aa-detail-head">' +
      '<div><h2>Grievance ' + esc(g.reference) + "</h2><p class='sub'>" + readBadge(g.is_read) +
      (g.read_at ? " · read " + fmtDate(g.read_at) + " by " + esc(g.read_by || "—") : "") + "</p></div>" +
      "<div class='aa-actions'>" +
      (g.is_read
        ? '<button class="btn sm ghost" id="btnUnread">Mark Unread</button>'
        : '<button class="btn sm green" id="btnRead">✓ Mark as Read</button>') +
      "</div></div>" +

      '<div class="aa-grid">' +
      "<div class='aa-block'><h3>Student Information</h3><table class='aa-kv'>" +
      "<tr><td>Name</td><td>" + esc(g.student_name || "—") + "</td></tr>" +
      "<tr><td>Roll Number</td><td>" + esc(g.roll_number || "—") + "</td></tr>" +
      "<tr><td>College</td><td>" + esc(g.college || "—") + "</td></tr>" +
      "<tr><td>Semester</td><td>" + esc(g.semester || "—") + "</td></tr>" +
      "<tr><td>Email</td><td>" + esc(g.student_email || "—") + "</td></tr>" +
      "</table></div>" +
      "<div class='aa-block'><h3>Grievance</h3><div class='aa-quote'>" + esc(g.final_grievance_text || g.original_student_input || "—") + "</div>" +
      (g.original_student_input && g.original_student_input !== g.final_grievance_text
        ? "<p class='muted' style='font-size:12px;margin-top:8px;'><b>Original student text:</b> " + esc(g.original_student_input) + "</p>" : "") +
      "</div>" +
      "</div>" +

      '<div class="aa-block"><h3>Status</h3><div class="aa-status-row">' + statusBadge(g.status) +
      '<select id="newStatus" class="aa-input" style="max-width:220px;">' +
      ["submitted", "acknowledged", "in_progress", "resolved", "closed", "rejected"]
        .map(function (s) { return '<option value="' + s + '"' + (s === g.status ? " selected" : "") + ">" + (STATUS_LABELS[s] || s) + "</option>"; }).join("") +
      "</select>" +
      '<button class="btn green" id="btnStatus">Update Status</button></div>' +
      "<p class='muted' style='font-size:12px;margin-top:6px;'>Every change is recorded in the immutable status history.</p></div>" +

      '<div class="aa-block"><h3>History</h3><ul class="aa-history">' + history + "</ul></div>" +

      (g.original_student_input ? "" : "") +
      responseBox +
      "</div>";

    // status update
    $("btnStatus").addEventListener("click", function () {
      if (detailBusy) return;
      var nv = $("newStatus").value;
      if (nv === g.status) { toast("Status is already " + (STATUS_LABELS[nv] || nv) + ".", "warning"); return; }
      detailBusy = true; $("btnStatus").disabled = true; $("btnStatus").textContent = "Updating…";
      apiJson(API + "/api/authority-admin/grievances/" + encodeURIComponent(g.id) + "/status", "POST", { new_status: nv }).then(function (res) {
        detailBusy = false; $("btnStatus").disabled = false; $("btnStatus").textContent = "Update Status";
        if (res.ok) {
          var notif = res.data && res.data.notification;
          var msg = "Status updated to " + (STATUS_LABELS[nv] || nv) + ".";
          if (notif && notif.status === "sent") msg += " The student has been notified by email.";
          else if (notif && (notif.status === "failed" || notif.status === "skipped")) msg += " The email notification could not be delivered right now and has been logged for retry.";
          toast(msg, "success");
          openDetail(g.id); refreshDashQuietly();
        }
        else {
          if (res.status === 401) { handleUnauthorized(); return; }
          toast(apiError(res, "Failed to update the status. Please try again."), "error");
        }
      });
    });

    // read toggle
    var readBtn = $("btnRead"), unreadBtn = $("btnUnread");
    if (readBtn) readBtn.addEventListener("click", function () {
      readBtn.disabled = true;
      apiJson(API + "/api/authority-admin/grievances/" + encodeURIComponent(g.id) + "/read", "POST").then(function (res) {
        if (res.ok) { toast("Marked as read.", "success"); openDetail(g.id); refreshDashQuietly(); }
        else { readBtn.disabled = false; toast(apiError(res, "Could not mark as read. Please try again."), "error"); }
      });
    });
    if (unreadBtn) unreadBtn.addEventListener("click", function () {
      unreadBtn.disabled = true;
      apiJson(API + "/api/authority-admin/grievances/" + encodeURIComponent(g.id) + "/unread", "POST").then(function (res) {
        if (res.ok) { toast("Marked as unread.", "success"); openDetail(g.id); refreshDashQuietly(); }
        else { unreadBtn.disabled = false; toast(apiError(res, "Could not mark as unread. Please try again."), "error"); }
      });
    });

    // response
    var respSend = $("respSend");
    if (respSend) respSend.addEventListener("click", function () {
      if (detailBusy) return;
      var text = ($("respText").value || "").trim();
      if (text.length < 2) { toast("Please write a response first.", "warning"); return; }
      detailBusy = true; respSend.disabled = true; respSend.textContent = "Sending…";
      apiJson(API + "/api/authority-admin/grievances/" + encodeURIComponent(g.id) + "/response", "POST", { response: text }).then(function (res) {
        detailBusy = false; respSend.disabled = false; respSend.textContent = "✉️ Send Response";
        if (res.ok) {
          var st = res.data.response_email_status;
          if (st === "sent") toast("Response recorded. The student has been notified by email.", "success");
          else if (st === "failed") toast("Response recorded. The email notification could not be delivered right now and has been logged for retry.", "warning");
          else toast("Response recorded. No student email on file.", "warning");
          openDetail(g.id);
        } else {
          toast(apiError(res, "Failed to send the response. Please try again."), "error");
        }
      });
    });
  }

  function refreshDashQuietly() {
    apiJson(API + "/api/authority-admin/dashboard").then(function (res) {
      if (!res.ok) return;
      var d = res.data;
      $("navUnread").style.display = d.unread > 0 ? "inline-block" : "none";
      $("navUnread").textContent = d.unread;
    });
  }

  // ===== Profile =====
  function loadProfile() {
    $("profileInfo").innerHTML = '<p class="aa-loading">Loading profile…</p>';
    apiJson(API + "/api/authority-admin/profile").then(function (res) {
      if (!res.ok) { if (res.status === 401) handleUnauthorized(); return; }
      var p = res.data, a = p.authority || {};
      $("pfFullName").value = p.full_name || "";
      $("pfDesignation").value = p.designation || "";
      $("pfPhone").value = p.phone || "";
      $("profileInfo").innerHTML =
        '<table class="aa-kv">' +
        "<tr><td>Authority</td><td>" + esc(a.authority_name || "—") + "</td></tr>" +
        "<tr><td>Category</td><td>" + esc(a.category_name || "—") + "</td></tr>" +
        "<tr><td>Description</td><td>" + esc(a.description || "—") + "</td></tr>" +
        "<tr><td>Official email</td><td>" + esc(a.email || "—") + "</td></tr>" +
        "<tr><td>Phone</td><td>" + esc(a.phone || "—") + "</td></tr>" +
        "<tr><td>Website</td><td>" + esc(a.website || "—") + "</td></tr>" +
        "<tr><td>Office</td><td>" + esc(a.office_address || a.office_location || "—") + "</td></tr>" +
        "<tr><td>Office timings</td><td>" + esc(a.office_timings || "—") + "</td></tr>" +
        "<tr><td>Services</td><td>" + esc(a.services_offered || "—") + "</td></tr>" +
        "<tr><td>Account status</td><td>" + (p.is_active ? '<span class="aa-badge st-open">Active</span>' : '<span class="aa-badge rd-unread">Disabled</span>') + "</td></tr>" +
        "<tr><td>Admin username</td><td>" + esc(p.username) + "</td></tr>" +
        "<tr><td>Admin email</td><td>" + esc(p.email || "—") + "</td></tr>" +
        "<tr><td>Last login</td><td>" + fmtDate(p.last_login) + "</td></tr>" +
        "</table>";
    });
  }

  $("profileForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var btn = $("saveProfileBtn");
    if (btn.disabled) return;
    btn.disabled = true; btn.textContent = "Saving…";
    apiJson(API + "/api/authority-admin/profile", "PUT", {
      full_name: $("pfFullName").value.trim() || null,
      designation: $("pfDesignation").value.trim() || null,
      phone: $("pfPhone").value.trim() || null,
    }).then(function (res) {
      btn.disabled = false; btn.textContent = "💾 Save Profile";
      if (res.ok) { toast("Profile updated.", "success"); loadProfileMeta(); loadProfile(); }
      else toast(apiError(res, "Failed to update the profile. Please try again."), "error");
    });
  });

  $("pwdForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var btn = $("changePwdBtn");
    if (btn.disabled) return;
    if ($("pwNew").value !== $("pwNew2").value) { toast("New passwords do not match.", "error"); return; }
    btn.disabled = true; btn.textContent = "Updating…";
    apiJson(API + "/api/authority-admin/password", "PUT", {
      current_password: $("pwCurrent").value,
      new_password: $("pwNew").value,
    }).then(function (res) {
      btn.disabled = false; btn.textContent = "🔑 Update Password";
      if (res.ok) {
        toast("Password updated. Please log in again.", "success");
        $("pwdForm").reset();
        setTimeout(showLogin, 900);
      } else {
        toast(apiError(res, "Failed to update the password. Please try again."), "error");
      }
    });
  });

  // ===== Navigation =====
  document.querySelectorAll(".aa-nav-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      if (b.dataset.view === "detail") return;
      showView(b.dataset.view);
    });
  });

  // ===== Refresh button =====
  $("refreshBtn").addEventListener("click", function () { location.reload(); });

  // ===== Boot: restore session =====
  if (token) {
    apiJson(API + "/api/authority-admin/profile").then(function (res) {
      if (res.ok) { currentUser = res.data; showDash(); loadDashboard(); }
      else if (res.status === 403) {
        // Token belongs to another role (student/superadmin): never use it here.
        setTimeout(showLogin, 0);
      }
      else if (res.status === 401) { showLogin(); }
      else { showLogin(); }
    });
  } else {
    showLogin();
  }
})();