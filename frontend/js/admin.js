(function () {
  "use strict";

  if (!window.CUS_API_BASE) throw new Error("CUS_API_BASE not defined");
  var API = window.CUS_API_BASE;

  // ===== Auth =====
  var token = localStorage.getItem("cus_admin_token") || null;
  function authHeaders() { var h = {}; if (token) h.Authorization = "Bearer " + token; return h; }
  function setToken(t) { token = t; if (t) localStorage.setItem("cus_admin_token", t); else localStorage.removeItem("cus_admin_token"); }
  function handleUnauthorized() { console.warn("[Admin] Unauthorized"); setToken(null); disconnectSSE(); showLogin(); }

  // ===== Helpers =====
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { if (typeof s !== "string") return String(s || ""); return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]; }); };
  var fmtSize = function (b) { if (!b) return "0 B"; if (b < 1024) return b + " B"; if (b < 1048576) return (b / 1024).toFixed(1) + " KB"; return (b / 1048576).toFixed(1) + " MB"; };
  var fmtPct = function (p) { return Math.round(p) + "%"; };
  var delay = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var now = function () { return Date.now(); };
  var stageOrder = ["queued","saving","extracting","chunking","embedding","indexing","completed"];

  // ===== Toast notifications =====
  var toastContainer = $("toastContainer");
  function toast(msg, type) {
    type = type || "info";
    var icons = { success: "\u2705", error: "\u274c", info: "\u2139\ufe0f", warning: "\u26a0\ufe0f" };
    var el = document.createElement("div");
    el.className = "toast toast-" + type;
    el.innerHTML = '<span class="toast-icon">' + (icons[type] || "") + '</span><span>' + esc(msg) + '</span><button class="toast-close">&times;</button>';
    el.querySelector(".toast-close").onclick = function () { removeToast(el); };
    toastContainer.appendChild(el);
    setTimeout(function () { removeToast(el); }, type === "error" ? 6000 : 3500);
  }
  function removeToast(el) {
    if (el.classList.contains("removing")) return;
    el.classList.add("removing");
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
  }

  // ===== Views =====
  var loginView = $("loginView"), dashView = $("dashView");
  function showDash() {
    loginView.style.display = "none"; dashView.style.display = "block";
    $("userLabel").style.display = "inline"; $("logoutBtn").style.display = "inline"; $("profileBtn").style.display = "inline";
    $("userLabel").textContent = "Admin";
    loadProfile();
    loadHealth();
    loadDocs();
    connectSSE();
  }
  function showLogin() {
    setToken(null);
    disconnectSSE();
    dashView.style.display = "none"; loginView.style.display = "block";
    $("userLabel").style.display = "none"; $("logoutBtn").style.display = "none"; $("profileBtn").style.display = "none";
  }

  // ===== Login =====
  $("loginForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var f = e.target;
    var url = API + "/api/auth/login";
    fetch(url, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "username=" + encodeURIComponent(f.username.value) + "&password=" + encodeURIComponent(f.password.value),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (res.ok && res.d.access_token) {
          setToken(res.d.access_token);
          showDash();
        } else {
          toast("Login failed: " + (res.d.detail || "invalid credentials"), "error");
        }
      })
      .catch(function () { toast("Cannot reach backend (" + API + ")", "error"); });
  });
  $("logoutBtn").addEventListener("click", showLogin);

  // ===== My Profile =====
  var currentProfile = null;

  function apiJson(url, method, body, extraHeaders) {
    var opts = { method: method || "GET", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders(), extraHeaders || {}) };
    if (body) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
    }).catch(function () { return { ok: false, status: 0, data: { detail: "Network error" } }; });
  }

  // ===== Human-readable API errors (never [object Object]) =====
  function extractApiError(res, fallback) {
    var d = res && res.data;
    fallback = fallback || "Request failed. Please try again.";
    if (typeof d !== "object" || d === null) return fallback;
    var det = d.detail;
    if (typeof det === "string") return det;
    if (Array.isArray(det)) {
      var msgs = [];
      for (var i = 0; i < det.length; i++) { if (det[i] && typeof det[i].msg === "string") msgs.push(det[i].msg); }
      if (msgs.length) return msgs.join("; ");
    }
    if (det) { try { return JSON.stringify(det); } catch (_) { return fallback; } }
    if (d.error && d.error.message) return String(d.error.message);
    if (d.message) return String(d.message);
    return fallback;
  }

  function applyProfile(p) {
    currentProfile = p;
    var display = (p.full_name && p.full_name.trim()) || p.username;
    $("userLabel").textContent = display;
    $("pfFullName").value = p.full_name || "";
    $("pfDesignation").value = p.designation || "";
    $("pfEmail").value = p.email || "";
    $("pfPhone").value = p.phone || "";
    $("pfUsername").value = p.username;
    $("pfRole").value = p.role;
    $("newUsername").value = p.username;
    if (p.avatar_url) {
      var av = API + p.avatar_url + "?v=" + Date.now();
      $("profileAvatarImg").onerror = function () {
        this.style.display = "none";
        $("profileAvatar").querySelector(".profile-avatar-fallback").style.display = "block";
        $("topAvatar").style.display = "none";
      };
      $("profileAvatarImg").src = av;
      $("profileAvatarImg").style.display = "block";
      $("profileAvatar").querySelector(".profile-avatar-fallback").style.display = "none";
      $("topAvatar").src = av;
      $("topAvatar").style.display = "inline-block";
      $("removeAvatarBtn").style.display = "inline-block";
    } else {
      $("profileAvatarImg").style.display = "none";
      $("profileAvatar").querySelector(".profile-avatar-fallback").style.display = "block";
      $("topAvatar").style.display = "none";
      $("removeAvatarBtn").style.display = "none";
    }
  }

  function loadProfile() {
    if (!token) return;
    apiJson(API + "/api/admin/profile").then(function (res) {
      if (res.ok) { applyProfile(res.data); }
      else if (res.status === 401) { handleUnauthorized(); }
    });
  }

  $("saveProfileBtn").addEventListener("click", function () {
    var btn = $("saveProfileBtn"), msg = $("profileMsg");
    btn.disabled = true; msg.textContent = "";
    apiJson(API + "/api/admin/profile", "PUT", {
      full_name: $("pfFullName").value,
      designation: $("pfDesignation").value,
      email: $("pfEmail").value,
      phone: $("pfPhone").value,
    }).then(function (res) {
      if (res.ok) {
        applyProfile(res.data);
        msg.textContent = "Profile saved.";
        msg.style.color = "var(--green)";
        toast("Profile updated", "success");
      } else {
        msg.textContent = res.data.detail || "Failed to save";
        msg.style.color = "#ef4444";
      }
    }).finally(function () { btn.disabled = false; });
  });

  $("avatarBtn").addEventListener("click", function () { $("avatarInput").click(); });
  $("avatarInput").addEventListener("change", function () {
    var file = $("avatarInput").files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { toast("Image must be 2 MB or smaller", "error"); return; }
    var fd = new FormData();
    fd.append("file", file);
    fetch(API + "/api/admin/profile/avatar", { method: "POST", headers: authHeaders(), body: fd })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (res.ok) {
          applyProfile(res.data);
          toast("Profile photo updated", "success");
        } else {
          toast(res.data.detail || "Upload failed", "error");
        }
      })
      .catch(function () { toast("Network error uploading photo", "error"); })
      .finally(function () { $("avatarInput").value = ""; });
  });

  $("removeAvatarBtn").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    fetch(API + "/api/admin/profile/avatar", { method: "DELETE", headers: authHeaders() })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (res.ok) {
          applyProfile(res.data);
          toast("Profile photo removed", "success");
        } else {
          toast(res.data.detail || "Could not clear photo", "error");
        }
      })
      .catch(function () { toast("Network error", "error"); })
      .finally(function () { btn.disabled = false; });
  });

  $("saveUsernameBtn").addEventListener("click", function () {
    var btn = $("saveUsernameBtn"), msg = $("usernameMsg");
    var nu = $("newUsername").value.trim();
    var pw = $("usernamePassword").value;
    if (!nu) { msg.textContent = "Enter a new username"; msg.style.color = "#ef4444"; return; }
    if (!pw) { msg.textContent = "Enter your current password"; msg.style.color = "#ef4444"; return; }
    btn.disabled = true; msg.textContent = "";
    apiJson(API + "/api/admin/profile/username", "PUT", { new_username: nu, password: pw }).then(function (res) {
      if (res.ok) {
        applyProfile(res.data);
        msg.textContent = "Username updated.";
        msg.style.color = "var(--green)";
        toast("Username changed to '" + nu + "'", "success");
      } else {
        msg.textContent = res.data.detail || "Failed to change username";
        msg.style.color = "#ef4444";
      }
    }).finally(function () { btn.disabled = false; });
  });

  $("changePwdBtn").addEventListener("click", function () {
    var btn = $("changePwdBtn"), msg = $("passwordMsg");
    var cur = $("currentPassword").value;
    var neu = $("newPassword").value;
    var con = $("confirmPassword").value;
    if (!cur) { msg.textContent = "Enter your current password"; msg.style.color = "#ef4444"; return; }
    if (neu.length < 6) { msg.textContent = "New password must be at least 6 characters"; msg.style.color = "#ef4444"; return; }
    if (neu !== con) { msg.textContent = "Passwords do not match"; msg.style.color = "#ef4444"; return; }
    btn.disabled = true; msg.textContent = "";
    apiJson(API + "/api/admin/profile/password", "PUT", { current_password: cur, new_password: neu }).then(function (res) {
      if (res.ok) {
        msg.textContent = "Password updated.";
        msg.style.color = "var(--green)";
        $("currentPassword").value = ""; $("newPassword").value = ""; $("confirmPassword").value = "";
        toast("Password updated", "success");
      } else {
        msg.textContent = res.data.detail || "Failed to update password";
        msg.style.color = "#ef4444";
      }
    }).finally(function () { btn.disabled = false; });
  });

  // ===== SSE Client (fetch-based, supports auth headers) =====
  var sseClient = null;
  var sseReconnectTimer = 0;
  var sseReconnectAttempts = 0;
  var sseAbortController = null;

  function updateSSEStatus(state) {
    var dot = $("sseDot"), txt = $("sseText");
    dot.className = "sse-dot " + state;
    if (state === "connected") { txt.textContent = "Live"; sseReconnectAttempts = 0; }
    else if (state === "reconnecting") { txt.textContent = "Reconnecting" + (sseReconnectAttempts > 0 ? " (" + sseReconnectAttempts + ")" : "") + "..."; }
    else { txt.textContent = "Disconnected"; }
  }

  function connectSSE() {
    if (sseClient) return;
    sseAbortController = new AbortController();
    updateSSEStatus("reconnecting");

    var handlers = {};
    var url = API + "/api/admin/jobs/events";
    var buffer = "";
    var eventName = "";
    var eventData = "";

    function processLine(line) {
      if (line.startsWith("event: ")) { eventName = line.slice(7).trim(); }
      else if (line.startsWith("data: ")) { eventData = line.slice(6); }
      else if (line === "") {
        if (eventName === "connected") { updateSSEStatus("connected"); }
        else if (eventName === "keepalive") { /* no-op */ }
        else if (eventName && handlers[eventName]) {
          try { handlers[eventName](JSON.parse(eventData)); } catch (e) { /* ignore parse errors */ }
        }
        eventName = ""; eventData = "";
      }
    }

    handlers.completed = function (data) {
      if (data && data.document_id) {
        loadDocs();
        loadHealth();
        refreshInsightsIfVisible();
        var fileCard = document.querySelector('.file-card[data-upload-id="' + (data.upload_id || "") + '"]');
        var fname = "";
        if (fileCard) { updateFileCardDone(fileCard, data); fname = fileCard.querySelector(".fc-name").textContent; }
        updateJobDashboard();
        toast("Indexing completed" + (fname ? ": " + fname : ""), "success");
      }
    };
    handlers.failed = function (data) {
      var fileCard = document.querySelector('.file-card[data-upload-id="' + (data.upload_id || "") + '"]');
      if (fileCard) { updateFileCardFailed(fileCard, data); }
      updateJobDashboard();
      if (data && data.upload_id) {
        toast("Job failed: " + (data.error || "unknown error"), "error");
      }
    };
    handlers.cancelled = function (data) {
      var fileCard = document.querySelector('.file-card[data-upload-id="' + (data.upload_id || "") + '"]');
      if (fileCard) { updateFileCardCancelled(fileCard); }
      updateJobDashboard();
    };
    handlers.processing = function (data) { updateFileCardStage(data, "saving", 10); };
    handlers.saved = function (data) { updateFileCardStage(data, "saving", 15); };
    handlers.extracting = function (data) { updateFileCardStage(data, "extracting", data.progress || 20); };
    handlers.extracted = function (data) { updateFileCardStage(data, "extracting", 30); };
    handlers.chunking = function (data) { updateFileCardStage(data, "chunking", 35); };
    handlers.chunked = function (data) { updateFileCardStage(data, "chunking", data.progress || 50); };
    handlers.embedding = function (data) { updateFileCardStage(data, "embedding", data.progress || 55); };
    handlers.embedded = function (data) { updateFileCardStage(data, "embedding", data.progress || 75); };
    handlers.indexing = function (data) { updateFileCardStage(data, "indexing", data.progress || 80); };
    handlers.indexed = function (data) { updateFileCardStage(data, "indexing", data.progress || 95); };
    handlers.retrying = function (data) {
      var fileCard = document.querySelector('.file-card[data-upload-id="' + (data.upload_id || "") + '"]');
      if (fileCard) { fileCard.querySelector(".fc-status").textContent = "Retrying..."; fileCard.querySelector(".fc-status").className = "fc-status queued"; }
    };

    sseClient = {
      close: function () {
        if (sseAbortController) { sseAbortController.abort(); sseAbortController = null; }
        sseClient = null;
        updateSSEStatus("disconnected");
      }
    };

    (function run() {
      if (!sseAbortController) return;
      var signal = sseAbortController.signal;
      fetch(url, { headers: authHeaders(), signal: signal })
        .then(function (resp) {
          if (resp.status === 401) { handleUnauthorized(); return; }
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          updateSSEStatus("connected");
          var reader = resp.body.getReader();
          var decoder = new TextDecoder();
          function read() {
            if (!sseAbortController) return;
            reader.read().then(function (result) {
              if (result.done) { reconnect(); return; }
              buffer += decoder.decode(result.value, { stream: true });
              var idx;
              while ((idx = buffer.indexOf("\n")) !== -1) {
                var line = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 1);
                if (line.endsWith("\r")) line = line.slice(0, -1);
                processLine(line);
              }
              read();
            }).catch(function (err) {
              if (err.name !== "AbortError") reconnect();
            });
          }
          read();
        })
        .catch(function (err) {
          if (err.name !== "AbortError") reconnect();
        });
    })();

    function reconnect() {
      if (!sseAbortController || !sseClient) return;
      sseReconnectAttempts++;
      var wait = Math.min(1000 * Math.pow(2, Math.min(sseReconnectAttempts, 5)), 30000);
      updateSSEStatus("reconnecting");
      clearTimeout(sseReconnectTimer);
      sseReconnectTimer = setTimeout(function () {
        if (sseClient) run();
      }, wait);
    }
  }

  function disconnectSSE() {
    clearTimeout(sseReconnectTimer);
    if (sseClient) { sseClient.close(); sseClient = null; }
    if (sseAbortController) { sseAbortController.abort(); sseAbortController = null; }
    updateSSEStatus("disconnected");
  }

  // ===== File card stage updates from SSE =====
  var stageLabels = {
    queued: "Queued", saving: "Saving", extracting: "Extracting", extracted: "Extracted",
    chunking: "Chunking", chunked: "Chunked", embedding: "Embedding", embedded: "Embedded",
    indexing: "Indexing", indexed: "Indexed", completed: "Completed", failed: "Failed", cancelled: "Cancelled"
  };

  function updateFileCardStage(data, stage, progress) {
    var uid = data.upload_id;
    if (!uid) return;
    var card = document.querySelector('.file-card[data-upload-id="' + uid + '"]');
    if (!card) return;
    card.querySelector(".fc-status").textContent = stageLabels[stage] || stage;
    card.querySelector(".fc-status").className = "fc-status";
    if (progress != null) {
      var fill = card.querySelector(".fc-progress-fill");
      if (fill) fill.style.width = Math.min(progress, 95) + "%";
    }
    var stages = card.querySelectorAll(".fc-stage");
    var found = false;
    stages.forEach(function (s) {
      var st = s.getAttribute("data-stage");
      if (st === stage) { s.className = "fc-stage active"; found = true; }
      else if (stageOrder.indexOf(st) < stageOrder.indexOf(stage)) { s.className = "fc-stage done"; }
      else { s.className = "fc-stage"; }
    });
  }

  function updateFileCardDone(card, data) {
    card.querySelector(".fc-status").textContent = "Completed";
    card.querySelector(".fc-status").className = "fc-status ready";
    var fill = card.querySelector(".fc-progress-fill");
    if (fill) fill.style.width = "100%";
    card.querySelectorAll(".fc-stage").forEach(function (s) {
      if (s.getAttribute("data-stage") === "completed") s.className = "fc-stage done";
      else s.className = "fc-stage done";
    });
    var actions = card.querySelector(".fc-actions");
    if (actions) actions.innerHTML = '<button class="fc-remove" title="Remove">\u2716</button>';
  }

  function updateFileCardFailed(card, data) {
    card.querySelector(".fc-status").textContent = "Failed: " + (data.error || "error");
    card.querySelector(".fc-status").className = "fc-status failed";
    var fill = card.querySelector(".fc-progress-fill");
    if (fill) { fill.style.width = "100%"; fill.style.background = "linear-gradient(90deg, #ef4444, #dc2626)"; }
    var actions = card.querySelector(".fc-actions");
    if (actions) {
      actions.innerHTML = '<button class="fc-retry" title="Retry">\u21bb Retry</button><button class="fc-remove" title="Remove">\u2716</button>';
      actions.querySelector(".fc-retry").onclick = function () { retryJob(data.upload_id); };
      actions.querySelector(".fc-remove").onclick = function () { removeFileCard(card, data.upload_id); };
    }
  }

  function updateFileCardCancelled(card, data) {
    card.querySelector(".fc-status").textContent = "Cancelled";
    card.querySelector(".fc-status").className = "fc-status cancelled";
    var fill = card.querySelector(".fc-progress-fill");
    if (fill) { fill.style.width = "100%"; fill.style.background = "#9ca3af"; }
    var actions = card.querySelector(".fc-actions");
    if (actions) {
      actions.innerHTML = '<button class="fc-retry" title="Retry">\u21bb Retry</button><button class="fc-remove" title="Remove">\u2716</button>';
      actions.querySelector(".fc-retry").onclick = function () { retryJob(data.upload_id); };
      actions.querySelector(".fc-remove").onclick = function () { removeFileCard(card, data.upload_id); };
    }
  }

  function removeFileCard(card, uploadId) {
    if (card && card.parentNode) card.parentNode.removeChild(card);
  }

  // ===== Upload Queue =====
  var dz = $("dropzone"), fi = $("fileInput");
  var activeUploads = {};

  dz.addEventListener("click", function () { fi.click(); });
  ["dragover", "dragenter"].forEach(function (ev) { dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("drag"); }); });
  ["dragleave", "drop"].forEach(function (ev) { dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("drag"); }); });
  dz.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files) handleFiles(e.dataTransfer.files); });
  fi.addEventListener("change", function () { handleFiles(fi.files); fi.value = ""; });

  function handleFiles(files) {
    var list = Array.prototype.slice.call(files);
    list.forEach(function (file) {
      addFileCard(file);
      uploadFile(file);
    });
  }

  function addFileCard(file) {
    var queue = $("uploadQueue");
    var ext = (file.name.split(".").pop() || "").toUpperCase();
    var card = document.createElement("div");
    card.className = "file-card";
    card.setAttribute("data-upload-id", "");
    card.setAttribute("data-filename", file.name);
    card.innerHTML =
      '<div class="fc-top">' +
        '<div class="fc-icon">\u{1f4c4}</div>' +
        '<div class="fc-meta"><div class="fc-name">' + esc(file.name) + '</div><div class="fc-info"><span>' + ext + '</span><span>' + fmtSize(file.size) + '</span></div></div>' +
        '<div class="fc-status queued">Queued...</div>' +
        '<div class="fc-actions"><button class="fc-cancel" title="Cancel">\u2716</button></div>' +
      '</div>' +
      '<div class="fc-progress">' +
        '<div class="fc-progress-bar indeterminate"><div class="fc-progress-fill"></div></div>' +
        '<div class="fc-stages">' +
          '<span class="fc-stage" data-stage="saving">\u{1f4be} Save</span>' +
          '<span class="fc-stage" data-stage="extracting">\u{1f50d} Extract</span>' +
          '<span class="fc-stage" data-stage="chunking">\u{1f9f1} Chunk</span>' +
          '<span class="fc-stage" data-stage="embedding">\u{1f9e9} Embed</span>' +
          '<span class="fc-stage" data-stage="indexing">\u{1f4c1} Index</span>' +
        '</div>' +
      '</div>';
    queue.appendChild(card);
    var cancelBtn = card.querySelector(".fc-cancel");
    cancelBtn.onclick = function () {
      var uid = card.getAttribute("data-upload-id");
      if (uid) cancelJob(uid);
      else removeFileCard(card, null);
    };
    return card;
  }

  function uploadFile(file) {
    dz.classList.add("disabled");
    var fd = new FormData();
    fd.append("file", file);
    var metaIds = { metaScheme: "academic_scheme", metaProgramme: "programme", metaDept: "department", metaBatch: "batch", metaSemester: "semester", metaDocType: "document_type", metaCategory: "category" };
    Object.keys(metaIds).forEach(function (id) {
      var el = document.getElementById(id);
      if (el && el.value) fd.append(metaIds[id], el.value);
    });
    fetch(API + "/api/documents/upload", { method: "POST", headers: authHeaders(), body: fd })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .then(function (res) {
        dz.classList.remove("disabled");
        if (!res) return;
        var card = findFileCard(file.name);
        if (!card) return;
        if (res.data.upload_id) {
          card.setAttribute("data-upload-id", res.data.upload_id);
          card.querySelector(".fc-status").textContent = "Queued";
          card.querySelector(".fc-status").className = "fc-status queued";
          card.querySelector(".fc-actions").innerHTML = '<button class="fc-cancel" title="Cancel">\u2716</button>';
          card.querySelector(".fc-cancel").onclick = function () { cancelJob(res.data.upload_id); };
          activeUploads[res.data.upload_id] = card;
          updateJobDashboard();
        } else if (res.data.status === "duplicate") {
          card.querySelector(".fc-status").textContent = "Already Indexed";
          card.querySelector(".fc-status").className = "fc-status ready";
          card.querySelector(".fc-actions").innerHTML = '<button class="fc-remove" title="Remove">\u2716</button>';
          card.querySelector(".fc-remove").onclick = function () { removeFileCard(card, null); };
          toast("Duplicate: " + file.name + " was already indexed", "info");
        } else {
          card.querySelector(".fc-status").textContent = "Upload failed";
          card.querySelector(".fc-status").className = "fc-status failed";
          card.querySelector(".fc-actions").innerHTML = '<button class="fc-remove" title="Remove">\u2716</button>';
          toast("Upload failed for " + file.name, "error");
        }
      })
      .catch(function () {
        dz.classList.remove("disabled");
        var card = findFileCard(file.name);
        if (card) {
          card.querySelector(".fc-status").textContent = "Network error";
          card.querySelector(".fc-status").className = "fc-status failed";
          toast("Network error uploading " + file.name, "error");
        }
      });
  }

  function escAttr(s) {
    if (typeof s !== "string") return String(s || "");
    return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]; }).replace(/"/g, '\\"');
  }

  function findFileCard(name) {
    var cards = document.querySelectorAll("#uploadQueue .file-card");
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].getAttribute("data-filename") === name) return cards[i];
    }
    return null;
  }

  // ===== Job Dashboard =====
  function updateJobDashboard() {
    fetch(API + "/api/admin/jobs?limit=20", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (jobs) {
        if (!jobs) return;
        var panel = $("jobDashboard");
        var hasActive = jobs.some(function (j) { return j.status !== "completed" && j.status !== "failed" && j.status !== "cancelled"; });
        if (!hasActive && !jobs.length) { panel.style.display = "none"; return; }
        panel.style.display = "block";
        var queued = 0, running = 0, completed = 0, failed = 0;
        jobs.forEach(function (j) {
          if (j.status === "queued") queued++;
          else if (["saving","extracting","chunking","embedding","indexing"].indexOf(j.status) !== -1) running++;
          else if (j.status === "completed") completed++;
          else if (j.status === "failed") failed++;
        });
        $("jdQueued").textContent = queued;
        $("jdRunning").textContent = running;
        $("jdCompleted").textContent = completed;
        $("jdFailed").textContent = failed;
        $("kQueueLen").textContent = queued + running;

        var list = $("jobList");
        list.innerHTML = "";
        jobs.slice(0, 20).forEach(function (j) {
          var statusClass = j.status;
          if (["saving","extracting","chunking","embedding","indexing"].indexOf(j.status) !== -1) statusClass = "running";
          else if (j.status === "completed") statusClass = "done";
          var eta = "";
          if (j.status === "queued") eta = "waiting...";
          else if (j.status === "completed" && j.total_time_ms) eta = (j.total_time_ms / 1000).toFixed(1) + "s";
          else if (j.status === "failed") eta = "failed";
          else eta = j.progress ? Math.round(j.progress) + "%" : "...";
          var item = document.createElement("div");
          item.className = "job-item";
          item.innerHTML =
            '<span class="ji-status ' + statusClass + '"></span>' +
            '<span class="ji-name">' + esc(j.filename || j.upload_id) + '</span>' +
            '<span class="ji-progress">' + (j.current_stage || j.status) + '</span>' +
            '<span class="ji-eta">' + eta + '</span>' +
            (j.total_time_ms ? '<span class="ji-time">' + (j.total_time_ms / 1000).toFixed(1) + 's</span>' : '');
          list.appendChild(item);
        });
      })
      .catch(function () {});
  }

  // ===== Job Control =====
  function cancelJob(uploadId) {
    fetch(API + "/api/admin/jobs/" + uploadId + "/cancel", { method: "POST", headers: authHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.status === "cancelled") toast("Job cancelled", "info");
      })
      .catch(function () { toast("Cancel failed", "error"); });
  }

  function retryJob(uploadId) {
    fetch(API + "/api/admin/jobs/" + uploadId + "/retry", { method: "POST", headers: authHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.status === "queued") toast("Job queued for retry", "info");
        else toast("Retry failed: " + (res.error || res.status), "error");
      })
      .catch(function () { toast("Retry request failed", "error"); });
  }

  // ===== Health =====
  function loadHealth() {
    fetch(API + "/api/admin/kb-health", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (h) {
        if (!h) return;
        var dot = $("hbDot"), txt = $("hbText");
        dot.className = "hb-dot " + h.status;
        txt.textContent = h.status === "ok" ? "All systems operational" : "Degraded - Ollama unavailable";
        $("kDocs").textContent = h.documents ? h.documents.total : "-";
        $("kChunk").textContent = h.chunks || "-";
        $("kConvs").textContent = h.conversations || "-";
        $("kModel").textContent = (h.ollama && h.ollama.llm) || "-";
        $("kEmbed").textContent = (h.ollama && h.ollama.embed) || "-";
        $("kStatus").textContent = h.status || "-";
        $("kKB").textContent = (h.knowledge_base && h.knowledge_base.total_files) || "-";
        if (h.db_size_bytes != null) {
          var sz = h.db_size_bytes;
          $("kDB").textContent = sz > 1048576 ? (sz / 1048576).toFixed(1) + " MB" : (sz / 1024).toFixed(0) + " KB";
        }
        // Fetch ingestion metrics for avg time
        fetch(API + "/api/admin/metrics/ingestion", { headers: authHeaders() })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (m) {
            if (m && m.avg_total_time_ms > 0) $("kAvgIndexTime").textContent = (m.avg_total_time_ms / 1000).toFixed(1) + "s";
          })
          .catch(function () {});
      })
      .catch(function () { $("hbText").textContent = "Health check unavailable"; });
  }

  // ===== Documents =====
  var _docRefreshPending = false;
  function loadDocs() {
    if (_docRefreshPending) return;
    _docRefreshPending = true;
    fetch(API + "/api/documents", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return []; }
        return r.ok ? r.json() : [];
      })
      .then(function (docs) {
        _docRefreshPending = false;
        var list = $("docList");
        list.innerHTML = "";
        if (!docs.length) { list.innerHTML = '<p class="muted">No documents yet. Upload a source above.</p>'; return; }
        docs.forEach(function (d) {
          var row = document.createElement("div");
          row.className = "doc-row";
          row.setAttribute("data-doc-id", d.id);
          var statusClass = d.status === "ready" || d.status === "indexed" ? "ok" : d.status === "failed" ? "err" : "proc";
          var tags = [];
          if (d.academic_scheme) tags.push(esc(d.academic_scheme.toLowerCase() === "nep2020" ? "NEP 2020" : d.academic_scheme.toUpperCase()));
          if (d.programme) tags.push(esc(d.programme));
          if (d.semester) tags.push("Sem " + esc(d.semester));
          if (d.document_type) tags.push(esc(d.document_type.replace(/_/g, " ")));
          if (d.batch) tags.push(esc(d.batch));
          var tagHtml = tags.length ? '<div class="doc-tags">' + tags.map(function (t) { return '<span class="doc-tag">' + t + '</span>'; }).join("") + '</div>' : "";
          row.innerHTML =
            '<div class="fi">\u{1f4c4}</div>' +
            '<div class="meta"><div class="nm">' + esc(d.filename || d.title || "Document") + '</div>' +
            '<div class="st ' + statusClass + '">' + esc(d.status || "processing") + (d.chunks ? " \u00b7 " + d.chunks + " chunks" : "") + '</div>' +
            tagHtml + '</div>' +
            '<button class="reindex">Re-index</button>' +
            '<button class="del">Delete</button>';
          row.querySelector(".del").addEventListener("click", function () { deleteDoc(d.id, row); });
          row.querySelector(".reindex").addEventListener("click", function () { reindexDoc(d.id, row); });
          list.appendChild(row);
        });
        $("kDocs").textContent = docs.length;
      })
      .catch(function () { _docRefreshPending = false; $("docList").innerHTML = '<p class="muted">Could not load documents.</p>'; });
  }

  function deleteDoc(id, row) {
    if (!confirm("Delete this document and its chunks?")) return;
    fetch(API + "/api/documents/" + id, { method: "DELETE", headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return; }
        row.remove();
        loadDocs();
        loadHealth();
        toast("Document deleted", "info");
      })
      .catch(function () { toast("Delete failed", "error"); });
  }

  function reindexDoc(id, row) {
    row.querySelector(".st").textContent = "Re-indexing...";
    row.querySelector(".st").className = "st proc";
    fetch(API + "/api/documents/" + id + "/reindex", { method: "POST", headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return; }
        if (r.status === 202) {
          toast("Re-index queued", "info");
        }
        r.json().then(function (data) {
          if (data && data.upload_id) {
            updateJobDashboard();
          }
        }).catch(function () {});
      })
      .catch(function () { toast("Re-index failed", "error"); });
  }

  $("refreshBtn").addEventListener("click", function () { loadHealth(); loadDocs(); updateJobDashboard(); });

  // ===== Tab Switching =====
  var tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabBtns.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.querySelectorAll(".tab-content").forEach(function (t) { t.style.display = "none"; });
      var tabId = "tab" + btn.dataset.tab.charAt(0).toUpperCase() + btn.dataset.tab.slice(1);
      var tab = document.getElementById(tabId);
      if (tab) tab.style.display = "block";
      if (btn.dataset.tab === "websiteSync") { loadWebsiteSync(); }
      if (btn.dataset.tab === "colleges") { loadColleges(); }
      if (btn.dataset.tab === "insights") {
        if (window.CUS && window.CUS.insightsInit) window.CUS.insightsInit();
      }
      if (btn.dataset.tab === "catalogue") {
        if (window.CUS && window.CUS.catalogueInit) window.CUS.catalogueInit();
      }
      if (btn.dataset.tab === "authorities") {
        setTimeout(function () { loadAuthorities(); loadCategories(); }, 50);
      }
      if (btn.dataset.tab === "authorityAdmins") {
        setTimeout(function () { loadAASection(); }, 50);
      }
      if (btn.dataset.tab === "profile") { loadProfile(); }
    });
  });

  // Profile shortcut in the top bar -> switch to the profile tab
  $("profileBtn").addEventListener("click", function () {
    document.querySelectorAll(".tab-btn").forEach(function (b) { b.classList.remove("active"); });
    document.querySelectorAll(".tab-content").forEach(function (t) { t.style.display = "none"; });
    var pb = document.querySelector('.tab-btn[data-tab="profile"]');
    if (pb) pb.classList.add("active");
    var tab = document.getElementById("tabProfile");
    if (tab) tab.style.display = "block";
    loadProfile();
  });

  // ===== AI Insights auto-refresh =====
  var _insightsRefreshTimer = 0;
  function refreshInsightsIfVisible() {
    var insightsTab = document.getElementById("tabInsights");
    if (!insightsTab || insightsTab.style.display === "none") return;
    clearTimeout(_insightsRefreshTimer);
    _insightsRefreshTimer = setTimeout(function () {
      var activeNav = document.querySelector(".insight-nav-btn.active");
      if (activeNav && window.CUS && window.CUS.insightsRefresh) {
        window.CUS.insightsRefresh(activeNav.dataset.section);
      }
    }, 500);
  }

  // Expose refresh for admin_insights
  window.CUS = window.CUS || {};
  window.CUS.insightsRefresh = function (section) {
    var period = $("insightPeriod");
    var p = period ? period.value : "month";
    var map = {
      overview: "loadOverview",
      trending: "loadTrending",
      courses: "loadCourses",
      colleges: "loadColleges",
      services: "loadServices",
      knowledge: "loadKnowledge",
      gaps: "loadGaps",
      conversations: "loadConversations",
      queries: "loadQueries",
      performance: "loadPerformance",
      insights: "loadInsights",
    };
    if (window._insightLoaders && window._insightLoaders[section]) {
      window._insightLoaders[section](p);
    }
  };

  // ===== Colleges =====
  function loadColleges() {
    fetch(API + "/api/college/list", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { handleUnauthorized(); return []; } return r.ok ? r.json() : []; })
      .then(function (colleges) {
        var statsEl = $("collegeStats");
        var total = colleges.length;
        var districts = {};
        colleges.forEach(function (c) { districts[c.district] = (districts[c.district] || 0) + 1; });
        statsEl.innerHTML = '<span><strong>' + total + '</strong> colleges</span><span><strong>' + Object.keys(districts).length + '</strong> districts</span>';
        var list = $("collegeList"); list.innerHTML = "";
        colleges.forEach(function (c) {
          var card = document.createElement("div"); card.className = "doc-row";
          var naacClass = c.naac && c.naac !== "N/A" ? "" : "muted";
          card.innerHTML =
            '<div class="fi">\u{1f3db}</div>' +
            '<div class="meta"><div class="nm">' + esc(c.name) + '</div>' +
            '<div class="st ok">' + esc(c.type) + ' \u00b7 ' + esc(c.district) + ' \u00b7 <span class="' + naacClass + '">NAAC ' + esc(c.naac || "N/A") + '</span></div></div>' +
            '<button class="view-college" data-action="view">View</button>' +
            '<button class="view-college manage-info" data-action="manage">Manage Info</button>';
          card.querySelector('[data-action="view"]').addEventListener("click", function () { showCollegeDetail(c.id); });
          card.querySelector('[data-action="manage"]').addEventListener("click", function () { openCollegeKnowledge(c.id); });
          list.appendChild(card);
        });
      })
      .catch(function () { $("collegeList").innerHTML = '<p class="muted">Could not load colleges.</p>'; });
  }

  function showCollegeDetail(collegeId) {
    fetch(API + "/api/college/" + encodeURIComponent(collegeId), { headers: authHeaders() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (c) {
        if (!c) { toast("Could not load college", "error"); return; }
        $("collegeList").parentElement.style.display = "none";
        $("collegeDetail").style.display = "block";
        var html = '<h2>' + esc(c.name) + '</h2>';
        html += '<p class="sub">' + esc(c.type) + ' \u00b7 Established ' + c.established + ' \u00b7 NAAC ' + (c.naac || "N/A") + '</p>';
        if (c.about) html += '<p>' + esc(c.about) + '</p>';
        html += '<h3 style="margin-top:20px;">Details</h3><table style="width:100%;font-size:14px;">';
        html += '<tr><td style="padding:6px 12px 6px 0;font-weight:600;color:var(--muted);">Principal</td><td>' + esc(c.principal || "N/A") + '</td></tr>';
        html += '<tr><td style="padding:6px 12px 6px 0;font-weight:600;color:var(--muted);">Address</td><td>' + esc(c.address || "N/A") + '</td></tr>';
        html += '<tr><td style="padding:6px 12px 6px 0;font-weight:600;color:var(--muted);">District</td><td>' + esc(c.district || "N/A") + '</td></tr>';
        if (c.phone) html += '<tr><td style="padding:6px 12px 6px 0;font-weight:600;color:var(--muted);">Phone</td><td>' + esc(c.phone) + '</td></tr>';
        if (c.email) html += '<tr><td style="padding:6px 12px 6px 0;font-weight:600;color:var(--muted);">Email</td><td>' + esc(c.email) + '</td></tr>';
        if (c.website) html += '<tr><td style="padding:6px 12px 6px 0;font-weight:600;color:var(--muted);">Website</td><td><a href="' + esc(c.website) + '" target="_blank">' + esc(c.website) + '</a></td></tr>';
        html += '</table>';
        if (c.departments && c.departments.length) {
          html += '<h3 style="margin-top:20px;">Departments (' + c.departments.length + ')</h3><div style="display:flex;flex-wrap:wrap;gap:6px;">';
          c.departments.forEach(function (d) { html += '<span class="tag">' + esc(d) + '</span>'; });
          html += '</div>';
        }
        if (c.programmes && c.programmes.length) {
          html += '<h3 style="margin-top:20px;">Programmes</h3><table style="width:100%;font-size:14px;">';
          c.programmes.forEach(function (p) { html += '<tr><td style="padding:4px 12px 4px 0;font-weight:600;color:var(--muted);text-transform:uppercase;">' + esc(p.level) + '</td><td>' + esc(p.name) + '</td></tr>'; });
          html += '</table>';
        }
        if (c.facilities && c.facilities.length) {
          html += '<h3 style="margin-top:20px;">Facilities</h3><ul style="columns:2;list-style:disc;padding-left:20px;">';
          c.facilities.forEach(function (f) { html += '<li>' + esc(f) + '</li>'; });
          html += '</ul>';
        }
        $("collegeDetailContent").innerHTML = html;
      })
      .catch(function () { toast("Failed to load college details", "error"); });
  }

  $("collegeBackBtn").addEventListener("click", function () {
    $("collegeDetail").style.display = "none";
    $("collegeList").parentElement.style.display = "block";
  });
  $("collegeRefreshBtn").addEventListener("click", loadColleges);

  // ===== Colleges: Knowledge Base Management =====
  var _knowledgeCollegeId = null;

  function openCollegeKnowledge(collegeId) {
    _knowledgeCollegeId = collegeId;
    $("collegeDetail").style.display = "none";
    $("collegeList").parentElement.style.display = "none";
    $("collegeKnowledge").style.display = "block";
    loadCollegeKnowledge();
  }

  $("collegeKnowledgeBackBtn").addEventListener("click", function () {
    $("collegeKnowledge").style.display = "none";
    $("collegeList").parentElement.style.display = "block";
    _knowledgeCollegeId = null;
  });

  function loadCollegeKnowledge() {
    if (!_knowledgeCollegeId) return;
    var el = $("collegeKnowledgeContent");
    el.innerHTML = '<p class="muted">Loading knowledge base...</p>';
    fetch(API + "/api/college/admin/" + encodeURIComponent(_knowledgeCollegeId) + "/knowledge", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { handleUnauthorized(); return null; } return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) { el.innerHTML = '<p class="muted">Could not load knowledge base.</p>'; return; }
        renderCollegeKnowledge(el, data);
      })
      .catch(function () { el.innerHTML = '<p class="muted">Failed to load knowledge base.</p>'; });
  }

  function renderCollegeKnowledge(el, data) {
    var html = '<div class="admin-card">';
    html += '<h2>' + esc(data.college_name) + ' — Knowledge Base</h2>';
    html += '<p class="sub">Sources are indexed into the chatbot corpus and retrieved only for this college.</p>';
    if (data.backfill && data.backfill.created) html += '<p class="st ok" style="margin:6px 0;">Auto digest queued for indexing...</p>';
    var s = data.summary || {};
    html += '<div style="display:flex;gap:16px;flex-wrap:wrap;margin:12px 0;">' +
      '<span class="tag">' + (s.sources || 0) + ' sources</span>' +
      '<span class="tag">' + (s.chunks || 0) + ' indexed chunks</span>' +
      '<span class="tag">Last updated: ' + (s.last_updated ? new Date(s.last_updated).toLocaleString() : "never") + '</span></div>';
    html += '<div class="admin-card" style="margin-top:14px;">';
    html += '<h3>Add content</h3><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:10px;">';

    html += '<div class="kb-add-box"><h4>&#x1f4c4; Upload File</h4>' +
      '<input type="file" id="kbUploadFile" accept=".pdf,.docx,.txt,.md" />' +
      '<input type="text" id="kbUploadTitle" placeholder="Title (optional)" />' +
      '<input type="text" id="kbUploadCategory" placeholder="Category (optional)" />' +
      '<button class="btn sm light" id="kbUploadBtn">Upload</button><span class="kb-msg" id="kbUploadMsg"></span></div>';

    html += '<div class="sub-add-box"><h4>&#x270d; Manual entry</h4>' +
      '<input type="text" id="kbManualTitle" placeholder="Title *" />' +
      '<textarea id="kbManualContent" rows="4" placeholder="Content (min 20 characters) *"></textarea>' +
      '<input type="text" id="kbManualCategory" placeholder="Category (optional)" />' +
      '<button class="btn sm green" id="kbManualBtn">Add</button><span class="kb-msg" id="kbManualMsg"></span></div>';

    html += '<div class="kb-url-box"><h4>&#x1f310; URL</h4>' +
      '<input type="url" id="kbUrlInput" placeholder="https://... *" />' +
      '<input type="text" id="kbUrlTitle" placeholder="Title (optional)" />' +
      '<button class="btn sm gold" id="kbUrlBtn">Import</button><span class="kb-msg" id="kbUrlMsg"></span></div>';

    html += '</div></div>';

    var sources = (data.sources || []).slice();
    html += '<h3 style="margin-top:18px;">Sources (' + sources.length + ')</h3>';
    html += '<div style="display:flex;gap:6px;margin:8px 0;"><button class="btn sm ghost" id="kbRefreshBtn">&#x21bb; Refresh</button></div>';
    if (!sources.length) {
      html += '<p class="muted">No sources yet. Add a file, manual entry or URL, or ask in the chatbot — the college digest is generated automatically.</p>';
    } else {
      html += '<table style="width:100%;font-size:14px;border-collapse:collapse;">';
      html += '<tr style="color:var(--muted);"><td style="padding:6px 10px;">Source</td><td>Type</td><td>Status</td><td>Chunks</td><td>Added</td><td></td></tr>';
      sources.forEach(function (src) {
        var stMap = { ready: "ok", queued: "warn", processing: "warn", indexing: "warn", failed: "err", archived: "muted" };
        var stCls = stMap[src.status] || "muted";
        var kindMap = { upload: "File", manual: "Manual", url: "URL", backfill: "Auto digest" };
        var kind = kindMap[src.source_kind] || src.source_kind;
        var actions = '';
        if (src.status === "archived") {
            actions += '<button class="btn sm ghost" data-kb="restore" data-id="' + src.id + '">Restore</button>';
        } else if (src.status !== "failed") {
            actions += '<button class="btn sm ghost" data-kb="archive" data-id="' + src.id + '">Archive</button>';
        }
        if (src.status === "failed" || src.status === "ready" || src.status === "archived") {
            actions += '<button class="btn sm ghost" data-kb="reindex" data-id="' + src.id + '">Re-index</button>';
        }
        actions += '<button class="btn sm ghost" data-kb="delete" data-id="' + src.id + '">Delete</button>';
        var errNote = src.status === "failed" && src.error ? '<div class="muted st err">' + esc(src.error) + '</div>' : '';
        html += '<tr><td style="padding:8px 10px;"><strong>' + esc(src.title) + '</strong>' + errNote + '</td>' +
          '<td>' + esc(kind) + '</td>' +
          '<td><span class="st ' + stCls + '">' + esc(src.status) + '</span></td>' +
          '<td>' + src.chunks + '</td>' +
          '<td>' + (src.created_at ? new Date(src.created_at).toLocaleDateString() : "—") + '</td><td>' + actions + '</td></tr>';
      });
      html += '</table>';
    }
    html += '</div>';
    el.innerHTML = html;

    bindKbActions(el);
  }

  function bindKbActions(el) {
    var cid = _knowledgeCollegeId;

    var upBtn = el.querySelector("#kbUploadBtn");
    if (upBtn) upBtn.addEventListener("click", function () {
      var fileEl = el.querySelector("#kbUploadFile");
      var titleEl = el.querySelector("#kbUploadTitle");
      var catEl = el.querySelector("#kbUploadCategory");
      var f = fileEl.files[0];
      if (!f) { kbMsg(el.querySelector("#kbUploadMsg"), "Choose a file first", false); return; }
      var fd = new FormData();
      fd.append("file", f);
      if (titleEl.value.trim()) fd.append("title", titleEl.value.trim());
      if (catEl.value.trim()) fd.append("category", catEl.value.trim());
      fetch(API + "/api/college/admin/" + encodeURIComponent(cid) + "/knowledge/upload", {
        method: "POST", headers: authHeaders(), body: fd,
      }).then(function (r) { return r.json(); }).then(function () {
        kbMsg(el.querySelector("#kbUploadMsg"), "Upload queued", true);
        loadCollegeKnowledge();
      }).catch(function () { kbMsg(el.querySelector("#kbUploadMsg"), "Upload failed", false); });
    });

    var manualBtn = el.querySelector("#kbManualBtn");
    if (manualBtn) manualBtn.addEventListener("click", function () {
      var t = el.querySelector("#kbManualTitle").value.trim();
      var c = el.querySelector("#kbManualContent").value.trim();
      var cat = el.querySelector("#kbManualCategory").value.trim();
      if (!t || !c) { kbMsg(el.querySelector("#kbManualMsg"), "Title and content are required", false); return; }
      fetch(API + "/api/college/admin/" + encodeURIComponent(cid) + "/knowledge/manual", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ title: t, content: c, category: cat || null }),
      }).then(function (r) { return r.json(); }).then(function () {
        kbMsg(el.querySelector("#kbManualMsg"), "Added", true);
        el.querySelector("#kbManualContent").value = "";
        loadCollegeKnowledge();
      }).catch(function () { kbMsg(el.querySelector("#kbManualMsg"), "Could not add entry", false); });
    });

    var urlBtn = el.querySelector("#kbUrlBtn");
    if (urlBtn) urlBtn.addEventListener("click", function () {
      var u = el.querySelector("#kbUrlInput").value.trim();
      var t = el.querySelector("#kbUrlTitle").value.trim();
      if (!u) { kbMsg(el.querySelector("#kbUrlMsg"), "Enter a URL", false); return; }
      fetch(API + "/api/college/admin/" + encodeURIComponent(cid) + "/knowledge/url", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ url: u, title: t || null }),
      }).then(function (r) { return r.json(); }).then(function () {
        kbMsg(el.querySelector("#kbUrlMsg"), "Import queued", true);
        loadCollegeKnowledge();
      }).catch(function () { kbMsg(el.querySelector("#kbUrlMsg"), "Could not import URL", false); });
    });

    var refresh = el.querySelector("#kbRefreshBtn");
    if (refresh) refresh.addEventListener("click", loadCollegeKnowledge);

    el.querySelectorAll("[data-kb]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.dataset.id, action = btn.dataset.kb;
        if (action === "delete" && !confirm("Permanently delete this source and its vectors?")) return;
        var method = action === "delete" ? "DELETE" : "POST";
        if (action === "reindex") method = "POST";
        fetch(API + "/api/college/admin/knowledge/" + encodeURIComponent(id) + "/" + action, {
          method: method, headers: authHeaders(),
        }).then(function (r) { return r.json(); }).then(function () {
          toast(action.charAt(0).toUpperCase() + action.slice(1) + " submitted", "info");
          loadCollegeKnowledge();
        }).catch(function () { toast("Action failed", "error"); });
      });
    });
  }
  function kbMsg(el, msg, ok) {
    if (el) { el.textContent = msg; el.style.color = ok ? "#1e8449" : "#c0392b"; }
  }

  // ===== Authority Management =====
  var _authorities = [];
  var _categories = [];
  var _authSearchTimer = 0;

  // Load grievance categories into the filter + modal so the UI stays
  // DB-driven (no hardcoded authority/category data in frontend JS).
  function loadCategories() {
    return fetch(API + "/api/admin/authorities/categories", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { handleUnauthorized(); return []; } return r.ok ? r.json() : []; })
      .then(function (data) {
        var list = data.categories || data.results || data;
        if (!Array.isArray(list)) list = [];
        _categories = list;
        populateCategorySelects();
        return list;
      })
      .catch(function () { _categories = []; return []; });
  }

  function populateCategorySelects() {
    var filter = $("authCategoryFilter");
    var current = filter.value;
    var opts = '<option value="">All Categories (' + _authorities.length + ')</option>';
    var modalSel = $("authForm").querySelector('[name="category_id"]');
    var mcurrent = modalSel ? modalSel.value : "";
    var mopts = '<option value="">-- Uncategorized --</option>';
    _categories.forEach(function (c) {
      opts += '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>';
      mopts += '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>';
    });
    filter.innerHTML = opts;
    if (current) filter.value = current;
    if (modalSel) { modalSel.innerHTML = mopts; if (mcurrent) modalSel.value = mcurrent; }
    var catList = $("catList");
    if (catList) {
      if (!_categories.length) {
        catList.style.display = "block";
        catList.innerHTML = '<div class="auth-empty-icon">&#x1f3db;</div>' +
          '<div class="auth-empty-text">No categories yet</div>' +
          '<p class="muted">Add the first category to organize authorities.</p>';
      } else {
        catList.style.display = "block";
        catList.innerHTML = _categories.map(function (c) {
          var count = _authorities.filter(function (a) { return a.category_id === c.id; }).length;
          return '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;">' +
            '<div><strong>' + esc(c.name) + '</strong>' +
            (c.description ? '<div class="muted" style="font-size:12px;">' + esc(c.description) + '</div>' : '') + '</div>' +
            '<span class="auth-status-badge active"><span class="dot"></span>' + count + ' office' + (count === 1 ? "" : "s") + '</span>' +
            '</div>';
        }).join("");
      }
    }
  }

  // Load authorities from API
  function loadAuthorities() {
    var loadingEl = $("authorityLoading");
    var tableEl = $("authorityTable");
    var cardsEl = $("authorityCards");
    var emptyEl = $("authorityEmpty");
    loadingEl.style.display = "block";
    tableEl.style.display = "none";
    cardsEl.style.display = "none";
    emptyEl.style.display = "none";

    fetch(API + "/api/admin/authorities", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        loadingEl.style.display = "none";
        if (!data) { emptyEl.style.display = "block"; return; }
        var list = data.authorities || data.results || data;
        if (!Array.isArray(list)) list = [];
        _authorities = list;
        renderAuthorities();
        populateDeptFilter(list);
      })
      .catch(function () {
        loadingEl.style.display = "none";
        tableEl.innerHTML = '<p class="muted" style="text-align:center;padding:40px;">Failed to load authorities.</p>';
        tableEl.style.display = "block";
      });
  }

  // Populate department filter dropdown
  function populateDeptFilter(list) {
    var sel = $("authDeptFilter");
    var current = sel.value;
    var depts = {};
    list.forEach(function (a) { if (a.department_name) depts[a.department_name] = (depts[a.department_name] || 0) + 1; });
    var keys = Object.keys(depts).sort();
    sel.innerHTML = '<option value="">All Departments (' + list.length + ')</option>';
    keys.forEach(function (d) {
      sel.innerHTML += '<option value="' + esc(d) + '">' + esc(d) + ' (' + depts[d] + ')</option>';
    });
    if (current) sel.value = current;
  }

  // Render current view (table or card)
  function renderAuthorities() {
    var filtered = getFilteredAuthorities();
    var tableView = $("authorityTable");
    var cardsView = $("authorityCards");
    var emptyEl = $("authorityEmpty");

    if (!filtered.length) {
      tableView.style.display = "none";
      cardsView.style.display = "none";
      emptyEl.style.display = "block";
      return;
    }
    emptyEl.style.display = "none";

    var view = document.querySelector(".auth-view-btn.active");
    var isCard = view && view.dataset.view === "card";

    if (isCard) {
      tableView.style.display = "none";
      cardsView.style.display = "grid";
      cardsView.innerHTML = buildAuthorityCards(filtered);
    } else {
      tableView.style.display = "block";
      cardsView.style.display = "none";
      tableView.innerHTML = buildAuthorityTable(filtered);
    }
  }

  // Build table HTML
  function buildAuthorityTable(list) {
    var html = '<table class="auth-table"><thead><tr>' +
      '<th>Office</th><th>Department</th><th>Contact</th><th>Status</th><th>Actions</th>' +
      '</tr></thead><tbody>';
    list.forEach(function (a) {
      var statusClass = a.active !== false ? "active" : "inactive";
      var phone = a.phone || "";
      var email = a.email || "";
      html += '<tr>' +
        '<td><div class="auth-cell-name">' + esc(a.authority_name || "Unnamed") + '</div>' +
        (a.designation ? '<div class="auth-cell-dept">' + esc(a.designation) + '</div>' : '') + '</td>' +
        '<td><div class="auth-cell-dept">' + esc(a.department_name || "-") + '</div>' +
        (a.category_name ? '<div class="auth-cell-dept" style="opacity:.7;">' + esc(a.category_name) + '</div>' : '') + '</td>' +
        '<td class="auth-cell-dept">' + (phone ? esc(phone) + '<br/>' : "") + (email ? esc(email) : "") + '</td>' +
        '<td><span class="auth-status-badge ' + statusClass + '"><span class="dot"></span>' + statusClass + '</span></td>' +
        '<td><div class="auth-table-actions">' +
          '<button class="auth-act-edit" data-id="' + a.id + '">Edit</button>' +
          '<button class="auth-act-toggle" data-id="' + a.id + '">' + (a.active !== false ? 'Deactivate' : 'Activate') + '</button>' +
          '<button class="auth-act-delete" data-id="' + a.id + '">Delete</button>' +
        '</div></td></tr>';
    });
    html += '</tbody></table>';
    return html;
  }

  // Build card grid HTML
  function buildAuthorityCards(list) {
    var html = "";
    list.forEach(function (a) {
      var statusClass = a.active !== false ? "active" : "inactive";
      var phone = a.phone || "";
      var email = a.email || "";
      var svcs = a.services_offered || [];
      if (typeof svcs === "string") { try { svcs = JSON.parse(svcs); } catch (e) { svcs = []; } }
      if (!Array.isArray(svcs)) svcs = [];
      var tags = svcs.slice(0, 4);
      html += '<div class="auth-card-item">' +
        (a.priority > 0 ? '<span class="auth-card-priority">P' + a.priority + '</span>' : '') +
        '<div class="auth-card-top">' +
          '<div class="auth-card-icon">&#x1f3db;</div>' +
          '<div class="auth-card-meta">' +
            '<div class="auth-card-name">' + esc(a.authority_name || "Unnamed") + '</div>' +
            '<div class="auth-card-dept">' + esc(a.department_name || "") + ' &middot; <span class="auth-status-badge ' + statusClass + '"><span class="dot"></span>' + statusClass + '</span></div>' +
          '</div>' +
        '</div>' +
        (a.description ? '<div class="auth-card-body">' + esc(a.description.slice(0, 120)) + (a.description.length > 120 ? "..." : "") + '</div>' : '') +
        '<div class="auth-card-detail">' +
          (phone ? '<span>&#x1f4de; ' + esc(phone) + '</span>' : '') +
          (email ? '<span>&#x2709; ' + esc(email) + '</span>' : '') +
          (a.designation ? '<span>&#x1f464; ' + esc(a.designation) + '</span>' : '') +
        '</div>' +
        (tags.length ? '<div class="auth-card-tags">' + tags.map(function (t) { return '<span class="auth-card-tag">' + esc(t) + '</span>'; }).join("") + '</div>' : '') +
        '<div class="auth-card-actions">' +
          '<button class="card-edit" data-id="' + a.id + '">Edit</button>' +
          '<button class="card-toggle" data-id="' + a.id + '">' + (a.active !== false ? 'Deactivate' : 'Activate') + '</button>' +
          '<button class="card-delete" data-id="' + a.id + '" style="margin-left:auto;">Delete</button>' +
        '</div></div>';
    });
    return html;
  }

  // Filter and search authorities
  function getFilteredAuthorities() {
    var search = ($("authSearchInput").value || "").toLowerCase().trim();
    var dept = $("authDeptFilter").value;
    var status = $("authStatusFilter").value;
    var cat = $("authCategoryFilter").value;

    return _authorities.filter(function (a) {
      if (dept && a.department_name !== dept) return false;
      if (cat && (a.category_id || "") !== cat) return false;
      if (status === "active" && a.active === false) return false;
      if (status === "inactive" && a.active !== false) return false;
      if (search) {
        var haystack = ((a.authority_name || "") + " " + (a.department_name || "") + " " + (a.description || "") + " " + (a.phone || "") + " " + (a.email || "") + " " + (a.designation || "") + " " + (a.category_name || "")).toLowerCase();
        var kws = a.keywords || [];
        if (typeof kws === "string") { try { kws = JSON.parse(kws); } catch (e) { kws = []; } }
        if (Array.isArray(kws)) haystack += " " + kws.join(" ");
        if (haystack.indexOf(search) === -1) return false;
      }
      return true;
    });
  }

  // Re-apply filters (debounced for search input)
  function applyAuthFilters() {
    clearTimeout(_authSearchTimer);
    _authSearchTimer = setTimeout(function () { renderAuthorities(); }, 200);
  }

  // === Authority Modal ===
  function openAuthModal(authority) {
    var modal = $("authModal");
    var form = $("authForm");
    form.reset();
    form.querySelector('[name="active"]').checked = true;
    form.querySelector('[name="authority_id"]').value = "";
    $("authModalTitle").textContent = "Add Authority";

    if (authority) {
      $("authModalTitle").textContent = "Edit Authority";
      form.querySelector('[name="authority_id"]').value = authority.id;
      form.querySelector('[name="department_name"]').value = authority.department_name || "";
      form.querySelector('[name="authority_name"]').value = authority.authority_name || "";
      form.querySelector('[name="designation"]').value = authority.designation || "";
      form.querySelector('[name="priority"]').value = authority.priority || 10;
      form.querySelector('[name="description"]').value = authority.description || "";
      form.querySelector('[name="office_address"]').value = authority.office_address || "";
      form.querySelector('[name="office_location"]').value = authority.office_location || "";
      form.querySelector('[name="phone"]').value = authority.phone || "";
      form.querySelector('[name="email"]').value = authority.email || "";
      form.querySelector('[name="alternate_phone"]').value = authority.alternate_phone || "";
      form.querySelector('[name="website"]').value = authority.website || "";
      form.querySelector('[name="office_timings"]').value = authority.office_timings || "";
      form.querySelector('[name="working_days"]').value = authority.working_days || "";
      form.querySelector('[name="emergency_contact"]').value = authority.emergency_contact || "";
      form.querySelector('[name="active"]').checked = authority.active !== false;

      var svcs = authority.services_offered || [];
      if (typeof svcs === "string") { try { svcs = JSON.parse(svcs); } catch (e) { svcs = []; } }
      form.querySelector('[name="services_offered"]').value = Array.isArray(svcs) ? svcs.join(", ") : "";

      var kws = authority.keywords || [];
      if (typeof kws === "string") { try { kws = JSON.parse(kws); } catch (e) { kws = []; } }
      form.querySelector('[name="keywords"]').value = Array.isArray(kws) ? kws.join(", ") : "";

      form.querySelector('[name="category_id"]').value = authority.category_id || "";
      form.querySelector('[name="source_kind"]').value = authority.source_kind || "manual";
      if (!_categories.length) loadCategories();
    }

    modal.style.display = "flex";
  }

  function closeAuthModal() {
    $("authModal").style.display = "none";
  }

  // Save authority (create or update)
  function saveAuthority(form) {
    var id = form.querySelector('[name="authority_id"]').value;
    var data = {
      department_name: form.querySelector('[name="department_name"]').value.trim(),
      authority_name: form.querySelector('[name="authority_name"]').value.trim(),
      designation: form.querySelector('[name="designation"]').value.trim() || null,
      priority: parseInt(form.querySelector('[name="priority"]').value) || 10,
      description: form.querySelector('[name="description"]').value.trim() || null,
      office_address: form.querySelector('[name="office_address"]').value.trim() || null,
      office_location: form.querySelector('[name="office_location"]').value.trim() || null,
      phone: form.querySelector('[name="phone"]').value.trim(),
      email: form.querySelector('[name="email"]').value.trim(),
      alternate_phone: form.querySelector('[name="alternate_phone"]').value.trim() || null,
      website: form.querySelector('[name="website"]').value.trim() || null,
      office_timings: form.querySelector('[name="office_timings"]').value.trim() || null,
      working_days: form.querySelector('[name="working_days"]').value.trim() || null,
      emergency_contact: form.querySelector('[name="emergency_contact"]').value.trim() || null,
      active: form.querySelector('[name="active"]').checked,
    };

    // Parse comma-separated lists
    var svcsStr = form.querySelector('[name="services_offered"]').value.trim();
    data.services_offered = svcsStr ? svcsStr.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];

    var kwsStr = form.querySelector('[name="keywords"]').value.trim();
    data.keywords = kwsStr ? kwsStr.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];

    data.category_id = form.querySelector('[name="category_id"]').value || null;
    data.source_kind = form.querySelector('[name="source_kind"]').value || "manual";

    var btn = $("authModalSave");
    btn.disabled = true;
    btn.textContent = "\u23f3 Saving...";

    var method = id ? "PUT" : "POST";
    var url = id ? API + "/api/admin/authorities/" + id : API + "/api/admin/authorities";

    fetch(url, {
      method: method,
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify(data),
    })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.json().then(function (d) { return { ok: r.ok, data: d }; });
      })
      .then(function (res) {
        btn.disabled = false;
        btn.textContent = "Save Authority";
        if (!res) return;
        if (res.ok) {
          toast(id ? "Authority updated" : "Authority created", "success");
          closeAuthModal();
          loadAuthorities();
        } else {
          toast("Error: " + (res.data.detail || JSON.stringify(res.data)), "error");
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "Save Authority";
        toast("Network error saving authority", "error");
      });
  }

  // Delete authority
  function deleteAuthority(id) {
    if (!confirm("Permanently delete this authority record?")) return;
    fetch(API + "/api/admin/authorities/" + id, {
      method: "DELETE",
      headers: authHeaders(),
    })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (res) {
        if (res) {
          toast("Authority deleted", "info");
          loadAuthorities();
        }
      })
      .catch(function () { toast("Delete failed", "error"); });
  }

  // Toggle authority active status (with deactivation confirmation)
  function toggleAuthority(id) {
    var auth = _authorities.find(function (a) { return String(a.id) === id; });
    var deactivating = auth ? auth.active !== false : false;
    if (deactivating && !confirm("Deactivate this authority?\n\nIt will remain in the database (history is kept), but will not be offered as a new grievance destination.")) return;
    fetch(API + "/api/admin/authorities/" + id + "/toggle", {
      method: "POST",
      headers: authHeaders(),
    })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (res) {
        if (res) {
          toast(res.active ? "Authority activated" : "Authority deactivated", "success");
          loadAuthorities();
        }
      })
      .catch(function () { toast("Toggle failed", "error"); });
  }

  // Import the verified official Cluster University directory (idempotent).
  function importOfficialAuthorities() {
    if (!confirm("Import the verified official Cluster University authority directory?\n\nAdds missing authorities and fills placeholder contact data. Existing records are never duplicated or deleted.")) return;
    var btn = $("authOfficialBtn");
    var old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Importing...";
    fetch(API + "/api/admin/authorities/import-official", { method: "POST", headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.json().then(function (d) { return { ok: r.ok, data: d }; });
      })
      .then(function (res) {
        if (!res) return;
        if (res.ok) {
          var d = res.data || {};
          toast("Official import done: " + (d.created || 0) + " created, " + (d.updated || 0) + " updated", "success");
          loadAuthorities();
          loadCategories();
        } else {
          toast("Import failed: " + (res.data.detail || "Unknown error"), "error");
        }
      })
      .catch(function () { toast("Import network error", "error"); })
      .finally(function () { btn.disabled = false; btn.textContent = old; });
  }

  // === Grievance Categories management ===
  function openCatModal() {
    $("authCatModal").style.display = "flex";
    $("catError").style.display = "none";
    $("catNewName").value = "";
    populateCategorySelects();
  }

  function closeCatModal() {
    $("authCatModal").style.display = "none";
  }

  function addCategory() {
    var name = ($("catNewName").value || "").trim();
    var err = $("catError");
    if (name.length < 2) { err.textContent = "Enter a category name (min 2 characters)."; err.style.display = "block"; return; }
    fetch(API + "/api/admin/authorities/categories", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ name: name }),
    })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.json().then(function (d) { return { ok: r.ok, data: d }; });
      })
      .then(function (res) {
        if (!res) return;
        if (res.ok) {
          toast("Category created", "success");
          $("catNewName").value = "";
          err.style.display = "none";
          loadCategories();
        } else {
          err.textContent = res.data.detail || "Could not create category.";
          err.style.display = "block";
        }
      })
      .catch(function () { err.textContent = "Network error."; err.style.display = "block"; });
  }

  // Export authorities
  function exportAuthorities() {
    var fmt = "json";
    var url = API + "/api/admin/authorities/export?fmt=" + fmt;
    fetch(url, { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.blob() : null;
      })
      .then(function (blob) {
        if (!blob) return;
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "authorities_export." + fmt;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
        toast("Export downloaded", "success");
      })
      .catch(function () { toast("Export failed", "error"); });
  }

  // === Bulk Import ===
  function openImportModal() {
    $("authImportModal").style.display = "flex";
    $("authImportResult").innerHTML = "";
  }

  function closeImportModal() {
    $("authImportModal").style.display = "none";
    $("authImportResult").innerHTML = "";
  }

  function handleImportFile(file) {
    var resultEl = $("authImportResult");
    resultEl.innerHTML = '<div class="auth-loading">Reading ' + esc(file.name) + '...</div>';

    var reader = new FileReader();
    reader.onload = function () {
      var text = String(reader.result || "");
      var rows = null;
      try {
        if (/\.json$/i.test(file.name)) {
          rows = JSON.parse(text);
        } else {
          rows = parseCsvRows(text);
        }
      } catch (e) {
        resultEl.innerHTML = '<p class="muted" style="text-align:center;padding:20px;color:#ef4444;">Could not parse ' + esc(file.name) + ': ' + esc(e.message) + '</p>';
        return;
      }
      if (!Array.isArray(rows) || !rows.length) {
        resultEl.innerHTML = '<p class="muted" style="text-align:center;padding:20px;color:#ef4444;">No authority rows found in the file.</p>';
        return;
      }
      resultEl.innerHTML = '<div class="auth-loading">Uploading ' + rows.length + ' authorities...</div>';
      fetch(API + "/api/admin/authorities/bulk-import", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify(rows),
      })
        .then(function (r) {
          if (r.status === 401) { handleUnauthorized(); return null; }
          return r.json().then(function (d) { return { ok: r.ok, data: d }; });
        })
        .then(function (res) {
          if (!res) { resultEl.innerHTML = ""; return; }
          if (res.ok) {
            var d = res.data;
            var count = d && typeof d.count === "number" ? d.count : (d.imported || 0);
            var errors = (d && (d.errors || d.failed)) || [];
            var html = '<div class="import-summary">' +
              '<div class="is-box"><div class="is-num">' + count + '</div><div class="is-label">Imported</div></div>' +
              '<div class="is-box is-err"><div class="is-num">' + errors.length + '</div><div class="is-label">Errors</div></div>' +
              '</div>';
            if (errors.length) {
              html += '<div class="import-errors">';
              errors.forEach(function (e) { html += '<div>' + esc(typeof e === "string" ? e : (e.error || JSON.stringify(e))) + '</div>'; });
              html += '</div>';
            }
            resultEl.innerHTML = html;
            toast(count + " authorities imported", "success");
            loadAuthorities();
          } else {
            resultEl.innerHTML = '<p class="muted" style="text-align:center;padding:20px;color:#ef4444;">Import failed: ' + esc(res.data.detail || "Unknown error") + '</p>';
            toast("Import failed", "error");
          }
        })
        .catch(function () {
          resultEl.innerHTML = '<p class="muted" style="text-align:center;padding:20px;color:#ef4444;">Network error during import</p>';
          toast("Import network error", "error");
        });
    };
    reader.onerror = function () {
      resultEl.innerHTML = '<p class="muted" style="text-align:center;padding:20px;color:#ef4444;">Could not read the file.</p>';
    };
    reader.readAsText(file);
  }

  // Minimal CSV -> array-of-objects parser (handles quoted fields with commas).
  function parseCsvRows(text) {
    function cellsOf(line) {
      var out = [], cur = "", inQ = false;
      for (var i = 0; i < line.length; i++) {
        var ch = line[i];
        if (inQ) {
          if (ch === '"') {
            if (line[i + 1] === '"') { cur += '"'; i++; } else { inQ = false; }
          } else { cur += ch; }
        } else if (ch === '"') {
          inQ = true;
        } else if (ch === ",") {
          out.push(cur); cur = "";
        } else { cur += ch; }
      }
      out.push(cur);
      return out;
    }
    var rawLines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    var header = null;
    var rows = [];
    var n = 0;
    rawLines.forEach(function (ln) {
      var cells = cellsOf(ln);
      if (cells.length === 1 && cells[0].trim() === "") return;
      if (!header) {
        header = cells.map(function (h) { return h.trim(); });
        n = header.length;
        return;
      }
      var row = {};
      for (var j = 0; j < n; j++) row[header[j]] = (cells[j] || "").trim();
      if (Object.keys(row).some(function (k) { return row[k] !== ""; })) rows.push(row);
    });
    return rows;
  }

  // === Wire up authority events (delegated on parent) ===
  function initAuthorityEvents() {
    // Add button
    $("authAddBtn").addEventListener("click", function () { openAuthModal(null); });

    // Modal close
    $("authModalClose").addEventListener("click", closeAuthModal);
    $("authModalCancel").addEventListener("click", closeAuthModal);
    $("authModal").addEventListener("click", function (e) { if (e.target === this) closeAuthModal(); });

    // Form submit
    $("authForm").addEventListener("submit", function (e) { e.preventDefault(); saveAuthority(e.target); });

    // Search
    $("authSearchInput").addEventListener("input", function () {
      var clear = $("authSearchClear");
      clear.classList.toggle("visible", this.value.length > 0);
      applyAuthFilters();
    });
    $("authSearchClear").addEventListener("click", function () {
      $("authSearchInput").value = "";
      this.classList.remove("visible");
      applyAuthFilters();
    });

    // Filters
    $("authDeptFilter").addEventListener("change", applyAuthFilters);
    $("authCategoryFilter").addEventListener("change", applyAuthFilters);
    $("authStatusFilter").addEventListener("change", applyAuthFilters);

    // View toggle
    document.querySelectorAll(".auth-view-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".auth-view-btn").forEach(function (b) { b.classList.remove("active"); });
        this.classList.add("active");
        renderAuthorities();
      });
    });

    // Click delegation for table/card actions
    var cardContainer = $("authorityCards");
    var tableContainer = $("authorityTable");

    function handleAuthClick(e) {
      var target = e.target;
      var id = target.getAttribute("data-id");
      if (!id) return;

      if (target.classList.contains("auth-act-edit") || target.classList.contains("card-edit")) {
        var auth = _authorities.find(function (a) { return String(a.id) === id; });
        if (auth) openAuthModal(auth);
      } else if (target.classList.contains("auth-act-toggle") || target.classList.contains("card-toggle")) {
        toggleAuthority(id);
      } else if (target.classList.contains("auth-act-delete") || target.classList.contains("card-delete")) {
        deleteAuthority(id);
      }
    }

    cardContainer.addEventListener("click", handleAuthClick);
    tableContainer.addEventListener("click", handleAuthClick);

    // Export
    $("authExportBtn").addEventListener("click", exportAuthorities);

    // Import
    $("authImportBtn").addEventListener("click", openImportModal);
    $("authImportModalClose").addEventListener("click", closeImportModal);
    $("authImportCancel").addEventListener("click", closeImportModal);
    $("authImportModal").addEventListener("click", function (e) { if (e.target === this) closeImportModal(); });

    // Official import + categories
    $("authOfficialBtn").addEventListener("click", importOfficialAuthorities);
    $("authCatBtn").addEventListener("click", openCatModal);
    $("authCatModalClose").addEventListener("click", closeCatModal);
    $("authCatModalCancel").addEventListener("click", closeCatModal);
    $("authCatModal").addEventListener("click", function (e) { if (e.target === this) closeCatModal(); });
    $("catAddBtn").addEventListener("click", addCategory);
    $("catNewName").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); addCategory(); } });

    // Import dropzone
    var importDz = $("authImportDropzone");
    var importFi = $("authImportFileInput");
    importDz.addEventListener("click", function () { importFi.click(); });
    ["dragover", "dragenter"].forEach(function (ev) { importDz.addEventListener(ev, function (e) { e.preventDefault(); importDz.classList.add("drag"); }); });
    ["dragleave", "drop"].forEach(function (ev) { importDz.addEventListener(ev, function (e) { e.preventDefault(); importDz.classList.remove("drag"); }); });
    importDz.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) handleImportFile(e.dataTransfer.files[0]); });
    importFi.addEventListener("change", function () { if (importFi.files.length) handleImportFile(importFi.files[0]); importFi.value = ""; });
  }

  // Modify existing tab-switching to add authority handler
  // (tab visibility is handled by original click logic above)

  // Expose for insights refresh
  window.CUS = window.CUS || {};
  window.CUS.loadAuthorities = loadAuthorities;

  // ===== Website Sync dashboard =====
  var WS_CATEGORIES = ["admissions","examinations","departments","programmes","news","notices","faculty","scholarships","hostels","transport","administration","research","academic-calendar","events","student-services","policies","downloads","unknown"];
  var _wsStatus = null;
  var _wsPollTimer = 0;
  var WS_TERMINAL_STATES = ["Ready", "Warning", "Error"];

  function wsApi(path, method, body) {
    return apiJson(API + path, method || "GET", body);
  }

  function wsSetStatus(html) {
    var el = $("wsMessage");
    if (html) { el.style.display = "block"; el.innerHTML = html; }
    else { el.style.display = "none"; el.innerHTML = ""; }
  }

  function wsRenderRuntime() {
    var s = _wsStatus || {};
    var r = s.runtime || {};
    var state = r.state || "Disconnected";
    var bar = $("wsProgress");
    if (!bar) return;
    if (state === "Disconnected" || state === "Idle") { bar.style.display = "none"; return; }
    bar.style.display = "block";
    var labels = {
      Connecting: "Connecting\u2026", Connected: "Connected",
      Discovering: "Discovering pages\u2026", Syncing: "Syncing\u2026",
      Processing: "Processing\u2026", Ready: "\ud83d\udfe2 Ready",
      Warning: "\u26a0\ufe0f Warning", Error: "\ud83d\udd34 Error"
    };
    var dot = $("wsProgressDot");
    dot.className = "hb-dot " +
      (state === "Ready" ? "ok" : (state === "Warning" || state === "Error") ? "err" : "proc");
    $("wsProgressState").textContent = labels[state] || state;
    $("wsProgressMsg").textContent = r.message || "";
    var prog = r.progress || {};
    var lc = s.last_counts || {};
    var meta = "";
    if (state === "Ready" && lc) {
      meta = " \u2014 " + (lc.pages_found != null ? lc.pages_found : 0) + " pages" +
        " (" + (lc.new_pages || 0) + " new, " + (lc.updated_pages || 0) + " updated, " +
        (lc.unchanged_pages || 0) + " unchanged, " + (lc.failed_pages || 0) + " failed)";
    } else if (state === "Warning" && lc) {
      meta = " \u2014 " + (lc.pages_found != null ? lc.pages_found : 0) + " pages, " + (lc.failed_pages || 0) + " failed";
    } else if (prog.current != null && prog.total != null) {
      meta = " \u2014 " + prog.current + "/" + prog.total;
    } else if (prog.current != null) {
      meta = " \u2014 " + prog.current;
    }
    $("wsProgressCounts").textContent = meta;
  }

  function loadWebsiteSync() {
    loadWSStatus();
    loadWSPages();
    loadWSRuns();
  }

  function loadWSStatus() {
    wsApi("/api/admin/website-sync/status").then(function (res) {
      if (!res.ok) { toast("Status load failed", "error"); return; }
      var s = res.data || {};
      _wsStatus = s;
      wsRenderRuntime();
      $("wsBaseUrl").textContent = s.base_url || ".";
      $("wsTotal").textContent = s.total_pages != null ? s.total_pages : "-";
      $("wsIndexed").textContent = s.indexed_pages != null ? s.indexed_pages : "-";
      var breakdown = s.status_breakdown || {};
      $("wsFailed").textContent = breakdown.failed || 0;
      $("wsDupe").textContent = s.duplicate_pages || 0;
      $("wsToggle").checked = !!s.enabled;
      var schEl = $("wsSchedule");
      if (s.schedule && schEl.querySelector('option[value="' + s.schedule + '"]')) schEl.value = s.schedule;
      var lr = s.last_run;
      $("wsLastRun").textContent = lr ? "Last run: " + (lr.finished_at || lr.started_at || "running") + " — " + (lr.status || "") : "Never synced";
      var chips = $("wsStatusChips");
      chips.innerHTML = "";
      var keys = ["new","updated","unchanged","archived","failed"];
      keys.forEach(function (k) {
        var n = breakdown[k] || 0;
        var chip = document.createElement("span");
        chip.className = "status-badge";
        chip.style.margin = "0";
        chip.textContent = k + ": " + n;
        chips.appendChild(chip);
      });
      // populate category filter
      var catSel = $("wsFilterCategory");
      var cur = catSel.value;
      var cats = Object.keys(s.categories || {}).sort().concat(WS_CATEGORIES);
      cats = cats.filter(function (c, i) { return cats.indexOf(c) === i; });
      catSel.innerHTML = '<option value="">All categories</option>' + cats.map(function (c) { return '<option value="' + esc(c) + '">' + esc(c) + "</option>"; }).join("");
      if (cur) catSel.value = cur;
    });
  }

  function loadWSPages() {
    var q = $("wsFilterQ").value.trim();
    var cat = $("wsFilterCategory").value;
    var st = $("wsFilterStatus").value;
    var params = [];
    if (q) params.push("q=" + encodeURIComponent(q));
    if (cat) params.push("category=" + encodeURIComponent(cat));
    if (st) params.push("status=" + encodeURIComponent(st));
    var url = "/api/admin/website-sync/pages?" + params.join("&");
    wsApi(url).then(function (res) {
      var box = $("wsPageList");
      if (!res.ok) { box.innerHTML = '<p class="muted">Failed to load pages.</p>'; return; }
      var list = res.data || [];
      if (!list.length) { box.innerHTML = '<p class="muted">No pages crawled yet. Run sync to begin.</p>'; return; }
      var html = '<table style="width:100%;border-collapse:collapse;font-size:13px;">' +
        "<thead><tr>" +
        '<th style="text-align:left;padding:6px 8px;">Title / URL</th>' +
        '<th style="text-align:left;padding:6px 8px;">Category</th>' +
        '<th style="text-align:left;padding:6px 8px;">Status</th>' +
        '<th style="text-align:left;padding:6px 8px;">Version</th>' +
        '<th style="text-align:left;padding:6px 8px;">Chars</th>' +
        '<th style="text-align:left;padding:6px 8px;">Last sync</th>' +
        '<th style="text-align:left;padding:6px 8px;">Actions</th>' +
        "</tr></thead><tbody>";
      list.forEach(function (p) {
        var stBadge = esc(p.status || "");
        html += "<tr>" +
          '<td style="padding:6px 8px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
          (p.title ? "<div><b>" + esc(p.title) + "</b></div>" : "") +
          '<a href="' + esc(p.url) + '" target="_blank" rel="noopener" style="color:var(--accent);">' + esc(p.url) + "</a></td>" +
          '<td style="padding:6px 8px;">' + esc(p.category || "-") + "</td>" +
          '<td style="padding:6px 8px;">' + esc(stBadge) + "</td>" +
          '<td style="padding:6px 8px;">v' + esc(p.version || 1) + "</td>" +
          '<td style="padding:6px 8px;">' + (p.char_len || 0) + "</td>" +
          '<td style="padding:6px 8px;">' + esc(p.last_synced ? String(p.last_synced).slice(0, 19).replace("T", " ") : "-") + "</td>" +
          '<td style="padding:6px 8px;white-space:nowrap;">' +
          '<button class="btn sm ghost" data-ws-reindex="' + esc(p.id) + '">Reindex</button> ' +
          '<button class="btn sm ghost danger" data-ws-archive="' + esc(p.id) + '" ' + (p.status === "archived" ? "disabled" : "") + '>Archive</button>' +
          "</td></tr>";
      });
      html += "</tbody></table>";
      box.innerHTML = html;
    });
  }

  function loadWSRuns() {
    wsApi("/api/admin/website-sync/runs").then(function (res) {
      var box = $("wsRunList");
      if (!res.ok || !(res.data || []).length) { box.innerHTML = '<p class="muted">No sync runs recorded yet.</p>'; return; }
      var html = '<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr>' +
        '<th style="text-align:left;padding:6px 8px;">Trigger</th>' +
        '<th style="text-align:left;padding:6px 8px;">Status</th>' +
        '<th style="text-align:left;padding:6px 8px;">New</th>' +
        '<th style="text-align:left;padding:6px 8px;">Updated</th>' +
        '<th style="text-align:left;padding:6px 8px;">Unchanged</th>' +
        '<th style="text-align:left;padding:6px 8px;">Archived</th>' +
        '<th style="text-align:left;padding:6px 8px;">Failed</th>' +
        '<th style="text-align:left;padding:6px 8px;">Duration</th>' +
        '<th style="text-align:left;padding:6px 8px;">Started</th>' +
        "</tr></thead><tbody>";
      (res.data || []).forEach(function (r) {
        html += "<tr>" +
          '<td style="padding:6px 8px;">' + esc(r.trigger) + "</td>" +
          '<td style="padding:6px 8px;">' + esc(r.status) + "</td>" +
          '<td style="padding:6px 8px;">' + (r.new_pages || 0) + "</td>" +
          '<td style="padding:6px 8px;">' + (r.updated_pages || 0) + "</td>" +
          '<td style="padding:6px 8px;">' + (r.unchanged_pages || 0) + "</td>" +
          '<td style="padding:6px 8px;">' + (r.archived_pages || 0) + "</td>" +
          '<td style="padding:6px 8px;">' + (r.failed_pages || 0) + "</td>" +
          '<td style="padding:6px 8px;">' + (r.duration_seconds != null ? r.duration_seconds + "s" : "-") + "</td>" +
          '<td style="padding:6px 8px;">' + esc(String(r.started_at || "").slice(0, 19).replace("T", " ") || "-") + "</td>" +
          "</tr>";
      });
      html += "</tbody></table>";
      box.innerHTML = html;
    });
  }

function wsRunNow() {
    var btn = $("wsRunBtn");
    btn.disabled = true; btn.textContent = "\u23f3 Syncing...";
    wsSetStatus(null);
    // Live progress: the run is backgrounded server-side, so poll the
    // runtime state machine until the POST resolves (terminal phase).
    if (_wsPollTimer) clearInterval(_wsPollTimer);
    _wsPollTimer = setInterval(function () { loadWSStatus(); }, 1500);
    loadWSStatus();
    wsApi("/api/admin/website-sync/run", "POST", { urls: null, trigger: "manual" }).then(function (res) {
      if (res.ok) {
        toast("Website sync complete", "success");
        wsSetStatus(null);
      } else {
        toast("Website sync failed: " + (res.data.detail || "unknown"), "error");
        wsSetStatus("<b>Sync error:</b> " + esc((res.data && res.data.detail) || "unknown"));
      }
    }).catch(function () { toast("Network error during sync", "error"); })
      .finally(function () {
        if (_wsPollTimer) { clearInterval(_wsPollTimer); _wsPollTimer = 0; }
        btn.disabled = false; btn.textContent = "\u25b6 Sync Now";
        setTimeout(function () { loadWebsiteSync(); }, 300);
      });
  }

  function wsToggleSync() {
    var schedule = $("wsSchedule").value;
    wsApi("/api/admin/website-sync/toggle", "POST", { enabled: $("wsToggle").checked, schedule: schedule })
      .then(function (res) { if (res.ok) toast("Sync settings saved", "success"); });
  }

  $("wsRunBtn").addEventListener("click", wsRunNow);
  $("wsRefreshBtn").addEventListener("click", loadWebsiteSync);
  $("wsToggle").addEventListener("change", wsToggleSync);
  $("wsSchedule").addEventListener("change", wsToggleSync);
  $("wsApplyFilter").addEventListener("click", loadWebsiteSync);
  $("wsFilterQ").addEventListener("keydown", function (e) { if (e.key === "Enter") loadWebsiteSync(); });
  $("wsDupesBtn").addEventListener("click", function () {
    wsApi("/api/admin/website-sync/duplicates").then(function (res) {
      if (!res.ok) { toast("Duplicate scan failed", "error"); return; }
      var d = res.data || {};
      toast("Duplicates: " + (d.duplicate_groups || 0) + " groups, " + (d.duplicate_pages || 0) + " pages", d.duplicate_groups ? "warning" : "success");
    });
  });
  $("wsPageList").addEventListener("click", function (e) {
    var t = e.target;
    var re = t.getAttribute("data-ws-reindex");
    var ar = t.getAttribute("data-ws-archive");
    if (re) {
      apiJson(API + "/api/admin/website-sync/pages/" + encodeURIComponent(re) + "/reindex", "POST").then(function (r) {
        toast(r.ok ? "Reindexed" : "Reindex failed", r.ok ? "success" : "error");
        if (r.ok) loadWSPages();
      });
    }
    if (ar) {
      if (!confirm("Archive this page? Previous content stays in version history.")) return;
      apiJson(API + "/api/admin/website-sync/pages/" + encodeURIComponent(ar), "DELETE").then(function (r) {
        toast(r.ok ? "Archived" : "Archive failed", r.ok ? "success" : "error");
        if (r.ok) loadWSPages();
      });
    }
  });

  // ===== Authority Admins (Super Admin) =====
  var _aaRows = [];
  var _aaAuthorities = [];
  var _aaDetail = null;

  function aaFmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(iso);
    return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function aaFindAuthority(id) {
    for (var i = 0; i < _aaAuthorities.length; i++) { if (String(_aaAuthorities[i].id) === String(id)) return _aaAuthorities[i]; }
    return null;
  }

  function aaFetchAuthorities() {
    return apiJson(API + "/api/admin/authorities").then(function (res) {
      if (!res.ok || !Array.isArray(res.data)) return [];
      _aaAuthorities = res.data;
      var f = $("aaAuthorityFilter");
      if (f) {
        var cur = f.value;
        var opts = '<option value="">All Authorities</option>';
        _aaAuthorities.forEach(function (a) {
          opts += '<option value="' + esc(a.id) + '"' + (String(a.id) === String(cur) ? " selected" : "") + '>' +
            esc(a.authority_name) + '</option>';
        });
        f.innerHTML = opts;
      }
      return _aaAuthorities;
    });
  }

  function loadAASection() {
    aaFetchAuthorities().then(function () {
      apiJson(API + "/api/admin/authority-admins?query=&status=").then(function (res) {
        if (res.ok) {
          var all = res.data.authority_admins || [];
          var active = 0, inactive = 0, adminAuthIds = {};
          all.forEach(function (a) {
            if (a.is_active) active++; else inactive++;
            if (a.authority_id) adminAuthIds[a.authority_id] = true;
          });
          var noAdmin = 0;
          _aaAuthorities.forEach(function (a) { if (!adminAuthIds[a.id]) noAdmin++; });
          if ($("aaStatsTotalAuth")) $("aaStatsTotalAuth").textContent = _aaAuthorities.length;
          if ($("aaStatsActive")) $("aaStatsActive").textContent = active;
          if ($("aaStatsInactive")) $("aaStatsInactive").textContent = inactive;
          if ($("aaStatsNoAdmin")) $("aaStatsNoAdmin").textContent = noAdmin;
        }
        loadAuthorityAdmins();
      });
    });
  }

  function loadAuthorityAdmins() {
    var box = $("aaLoading"), list = $("aaCardList"), empty = $("aaEmpty"), err = $("aaError");
    if (box) box.style.display = "block";
    if (list) list.style.display = "none";
    if (err) err.style.display = "none";
    if (empty) empty.style.display = "none";
    var q = encodeURIComponent(($("aaSearchInput") ? $("aaSearchInput").value : "") || "");
    var status = ($("aaStatusFilter") ? $("aaStatusFilter").value : "") || "";
    var authId = ($("aaAuthorityFilter") ? $("aaAuthorityFilter").value : "") || "";
    var url = API + "/api/admin/authority-admins?query=" + q + "&status=" + status;
    if (authId) url += "&authority_id=" + encodeURIComponent(authId);
    apiJson(url).then(function (res) {
      if (box) box.style.display = "none";
      if (!res.ok) {
        if (err) { err.style.display = "block"; err.textContent = "Failed to load authority administrators. " + extractApiError(res, "Please refresh and try again."); }
        console.error("[Admin] authority-admins request failed:", res.status, res.data);
        return;
      }
      _aaRows = res.data.authority_admins || [];
      renderAuthorityAdmins();
    });
  }

  function aaInitials(a) {
    var s = (a.full_name || a.username || "?").trim();
    var parts = s.split(/\s+/);
    return esc((parts.length > 1 ? (parts[0].charAt(0) + parts[1].charAt(0)) : s.charAt(0)).toUpperCase());
  }

  function renderAuthorityAdmins() {
    var list = $("aaCardList"), empty = $("aaEmpty");
    if (!list) return;
    if (!_aaRows.length) {
      list.innerHTML = "";
      list.style.display = "none";
      if (empty) empty.style.display = "block";
      return;
    }
    if (empty) empty.style.display = "none";
    list.style.display = "grid";
    list.innerHTML = _aaRows.map(function (a) {
      var auth = a.authority || aaFindAuthority(a.authority_id) || null;
      var title = (auth && auth.authority_name) ? esc(auth.authority_name) : esc(a.authority_name || "Unassigned");
      var dept = auth && auth.department_name ? esc(auth.department_name) : "";
      var desig = esc(a.designation || (auth && auth.designation) || "");
      var name = esc(a.full_name || a.username);
      var statusBadge = a.is_active
        ? '<span class="auth-status-badge active"><span class="dot"></span>ACTIVE</span>'
        : '<span class="auth-status-badge inactive"><span class="dot"></span>INACTIVE</span>';
      return '<div class="aa-card" role="button" tabindex="0" data-aa-open="' + esc(a.id) + '" aria-label="View details for ' + name + '">' +
        '<div class="aa-card-top">' +
          '<div class="aa-card-avatar">' + aaInitials(a) + '</div>' +
          '<div class="aa-card-meta">' +
            '<div class="aa-card-title">' + title + '</div>' +
            (dept ? '<div class="aa-card-dept">' + dept + '</div>' : "") +
          '</div>' +
          statusBadge +
        '</div>' +
        '<div class="aa-card-name">' + name + '</div>' +
        (desig ? '<div class="aa-card-role">' + desig + '</div>' : '<div class="aa-card-role">Authority Administrator</div>') +
        '<div class="aa-card-detail">' +
          '<span>' + esc(a.username) + '</span>' +
          '<span>' + esc(a.email || "") + '</span>' +
        '</div>' +
        '<div class="aa-card-foot">' +
          '<span class="aa-card-authority-badge">' + title + '</span>' +
          '<span class="aa-card-view">View Details &rarr;</span>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function aaCardRow(label, value) {
    var v = (value === null || value === undefined || value === "") ? '<span class="muted">&mdash;</span>' : value;
    return '<div class="aa-detail-row"><dt>' + esc(label) + '</dt><dd>' + v + '</dd></div>';
  }

  function openAADetail(id) {
    _aaDetail = null;
    var modal = $("aaDetailModal"), body = $("aaDetailBody");
    modal.style.display = "flex";
    body.innerHTML = '<div class="auth-loading">Loading administrator details...</div>';
    apiJson(API + "/api/admin/authority-admins/" + encodeURIComponent(id)).then(function (res) {
      if (!res.ok) {
        body.innerHTML = '<div class="aa-error">' + esc(extractApiError(res, "Could not load administrator details.")) + '</div>';
        $("aaDetailEdit").style.display = "none";
        $("aaDetailReassign").style.display = "none";
        $("aaDetailToggle").style.display = "none";
        return;
      }
      _aaDetail = res.data;
      var a = res.data, auth = a.authority || null;
      var statusHtml = a.is_active
        ? '<span class="auth-status-badge active"><span class="dot"></span>Active</span>'
        : '<span class="auth-status-badge inactive"><span class="dot"></span>Inactive</span>';
      var bodyHtml = "";
      bodyHtml += '<div class="aa-detail-hero">' +
        '<div class="aa-card-avatar lg">' + aaInitials(a) + '</div>' +
        '<div class="aa-detail-hero-meta">' +
          '<div class="aa-detail-hero-name">' + esc(a.full_name || a.username) + '</div>' +
          '<div class="aa-detail-hero-role">' + esc(a.designation || "Authority Administrator") +
            (auth && auth.authority_name ? " &middot; " + esc(auth.authority_name) : "") + '</div>' +
        '</div>' + statusHtml + '</div>';
      bodyHtml += '<div class="aa-detail-section"><div class="aa-detail-section-title">Personal / Account Information</div><div class="aa-detail-grid">' +
        aaCardRow("Name", esc(a.full_name || a.username)) +
        aaCardRow("Username", esc(a.username)) +
        aaCardRow("Email", esc(a.email || "")) +
        aaCardRow("Account Status", statusHtml) +
        aaCardRow("Role", esc(a.role || "authority_admin")) +
        aaCardRow("Joined", aaFmtDate(a.created_at)) +
        aaCardRow("Last Updated", aaFmtDate(a.updated_at)) +
        aaCardRow("Last Login", aaFmtDate(a.last_login)) +
        '</div></div>';
      bodyHtml += '<div class="aa-detail-section"><div class="aa-detail-section-title">Authority Information</div><div class="aa-detail-grid">' +
        aaCardRow("Authority", esc((auth && auth.authority_name) || a.authority_name || "")) +
        aaCardRow("Department", esc((auth && auth.department_name) || "")) +
        aaCardRow("Designation", esc((auth && auth.designation) || "")) +
        aaCardRow("Category", esc((auth && auth.category_name) || "")) +
        aaCardRow("Official Email", esc((auth && auth.email) || "")) +
        aaCardRow("Official Phone", esc((auth && auth.phone) || "")) +
        aaCardRow("Website", auth && auth.website ? '<a href="' + esc(auth.website) + '" target="_blank" rel="noopener noreferrer">' + esc(auth.website) + '</a>' : "") +
        aaCardRow("Location", esc((auth && auth.office_location) || "")) +
        aaCardRow("Address", esc((auth && auth.office_address) || "")) +
        aaCardRow("Office Hours", esc((auth && auth.office_timings) || "")) +
        aaCardRow("Description", esc((auth && auth.description) || "")) +
        '</div></div>';
      bodyHtml += '<div class="aa-detail-section"><div class="aa-detail-section-title">System Information</div><div class="aa-detail-grid">' +
        aaCardRow("Authority Admin ID", esc(a.id)) +
        aaCardRow("Authority ID", esc(a.authority_id || "")) +
        aaCardRow("Assigned Authority", esc(a.authority_name || "Unassigned")) +
        aaCardRow("Account Created", aaFmtDate(a.created_at)) +
        aaCardRow("Account Status", statusHtml) +
        '</div></div>';
      body.innerHTML = bodyHtml;
      var toggleBtn = $("aaDetailToggle");
      toggleBtn.textContent = a.is_active ? "Deactivate" : "Activate";
      toggleBtn.className = a.is_active ? "btn ghost danger" : "btn green";
      toggleBtn.style.display = "";
      $("aaDetailEdit").style.display = "";
      $("aaDetailReassign").style.display = "";
    });
  }

  function closeAADetail() {
    _aaDetail = null;
    $("aaDetailModal").style.display = "none";
  }

  function populateAASelect() {
    var sel = $("aaAuthoritySelect");
    if (!sel) return;
    if (_aaAuthorities.length) {
      renderAASelectOptions(sel);
      return;
    }
    apiJson(API + "/api/admin/authorities?active_only=true").then(function (res) {
      if (res.ok && Array.isArray(res.data)) {
        _aaAuthorities = res.data;
        renderAASelectOptions(sel);
      }
    });
  }

  function renderAASelectOptions(sel) {
    var opts = '<option value="">-- Select Authority --</option>';
    _aaAuthorities.forEach(function (a) {
      if (a.active === false) return;
      opts += '<option value="' + esc(a.id) + '"' + (String(sel.dataset.value) === String(a.id) ? " selected" : "") + '>' +
        esc(a.authority_name) + " &mdash; " + esc(a.department_name || "") + '</option>';
    });
    sel.innerHTML = opts;
    updateAAPreview();
  }

  function updateAAPreview() {
    var sel = $("aaAuthoritySelect"), prev = $("aaAuthPreview");
    if (!sel || !prev) return;
    var a = aaFindAuthority(sel.value);
    if (!a) { prev.style.display = "none"; return; }
    $("aaPrevName").textContent = a.authority_name || "—";
    $("aaPrevDesignation").textContent = a.designation || "—";
    $("aaPrevEmail").textContent = a.email || "—";
    $("aaPrevCategory").textContent = a.category_name || "—";
    prev.style.display = "block";
  }

  var _aaEditingId = null;
  function openAAModalForm(admin) {
    _aaEditingId = admin ? admin.id : null;
    $("aaModal").style.display = "flex";
    $("aaModalTitle").textContent = admin ? "Edit Authority Admin" : "Add Authority Admin";
    $("aaFormError").style.display = "none";
    var f = $("aaForm");
    f.reset();
    var sel = $("aaAuthoritySelect");
    if (admin) {
      f.username.value = admin.username;
      f.email.value = admin.email || "";
      f.full_name.value = admin.full_name || "";
      f.designation.value = admin.designation || "";
      f.is_active.value = String(admin.is_active);
      sel.dataset.value = admin.authority_id || "";
      f.username.disabled = true;
    } else {
      sel.dataset.value = "";
      f.username.disabled = false;
    }
    $("aaPasswordField").style.display = admin ? "none" : "block";
    populateAASelect();
  }
  function closeAAModal() {
    $("aaModal").style.display = "none";
    _aaEditingId = null;
  }
  function saveAAModal() {
    var f = $("aaForm");
    if (f.checkValidity && !f.checkValidity()) { f.reportValidity(); return; }
    var err = $("aaFormError");
    err.style.display = "none";
    var finish = function () { closeAAModal(); loadAASection(); };
    var fail = function (msg) { err.textContent = msg || "Save failed"; err.style.display = "block"; };
    if (_aaEditingId) {
      var sel = $("aaAuthoritySelect");
      var authChanged = sel.dataset.value !== sel.value;
      var patch = function () {
        apiJson(API + "/api/admin/authority-admins/" + _aaEditingId, "PATCH", {
          full_name: f.full_name.value.trim() || null,
          designation: f.designation.value.trim() || null,
          email: f.email.value.trim(),
        }).then(function (res) {
          if (res.ok) { toast("Authority Admin updated", "success"); finish(); }
          else fail(extractApiError(res));
        });
      };
      if (authChanged) {
        if (!confirm("Changing the assigned authority will change which grievances this administrator can access. Continue?")) return;
        apiJson(API + "/api/admin/authority-admins/" + _aaEditingId + "/assign", "POST", { authority_id: sel.value })
          .then(function (res) {
            if (res.ok) { toast("Authority assignment updated", "success"); patch(); }
            else { fail(extractApiError(res)); }
          });
      } else {
        patch();
      }
    } else {
      apiJson(API + "/api/admin/authority-admins", "POST", {
        username: f.username.value.trim(),
        email: f.email.value.trim(),
        password: f.password.value,
        full_name: f.full_name.value.trim() || null,
        designation: f.designation.value.trim() || null,
        authority_id: f.authority_id.value,
        is_active: f.is_active.value === "true",
      }).then(function (res) {
        if (res.ok) { toast("Authority administrator created successfully", "success"); finish(); }
        else fail(extractApiError(res));
      });
    }
  }
  function wireAAEvents() {
    if ($("aaAddBtn")) $("aaAddBtn").addEventListener("click", function () { openAAModalForm(null); });
    if ($("aaEmptyAddBtn")) $("aaEmptyAddBtn").addEventListener("click", function () { openAAModalForm(null); });
    if ($("aaModalClose")) $("aaModalClose").addEventListener("click", closeAAModal);
    if ($("aaModalCancel")) $("aaModalCancel").addEventListener("click", closeAAModal);
    var modal = $("aaModal");
    if (modal) modal.addEventListener("click", function (e) { if (e.target === modal) closeAAModal(); });
    if ($("aaForm")) $("aaForm").addEventListener("submit", function (e) { e.preventDefault(); saveAAModal(); });
    if ($("aaStatusFilter")) $("aaStatusFilter").addEventListener("change", loadAuthorityAdmins);
    if ($("aaAuthorityFilter")) $("aaAuthorityFilter").addEventListener("change", loadAuthorityAdmins);
    if ($("aaSearchClear")) $("aaSearchClear").addEventListener("click", function () { $("aaSearchInput").value = ""; loadAuthorityAdmins(); });
    var debounceTimer = 0;
    if ($("aaSearchInput")) $("aaSearchInput").addEventListener("input", function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(loadAuthorityAdmins, 300);
    });
    if ($("aaCardList")) $("aaCardList").addEventListener("click", function (e) {
      var t = e.target;
      while (t && t !== $("aaCardList")) {
        var id = t.getAttribute && t.getAttribute("data-aa-open");
        if (id) { openAADetail(id); return; }
        t = t.parentNode;
      }
    });
    if ($("aaCardList")) $("aaCardList").addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var t = e.target;
      while (t && t !== $("aaCardList")) {
        var id = t.getAttribute && t.getAttribute("data-aa-open");
        if (id) { e.preventDefault(); openAADetail(id); return; }
        t = t.parentNode;
      }
    });
    if ($("aaDetailClose")) $("aaDetailClose").addEventListener("click", closeAADetail);
    if ($("aaDetailCloseBtn")) $("aaDetailCloseBtn").addEventListener("click", closeAADetail);
    var dModal = $("aaDetailModal");
    if (dModal) dModal.addEventListener("click", function (e) { if (e.target === dModal) closeAADetail(); });
    if ($("aaDetailEdit")) $("aaDetailEdit").addEventListener("click", function () {
      if (!_aaDetail) return;
      closeAADetail();
      openAAModalForm(_aaDetail);
    });
    if ($("aaDetailReassign")) $("aaDetailReassign").addEventListener("click", function () {
      if (!_aaDetail) return;
      closeAADetail();
      openAAModalForm(_aaDetail);
      $("aaAuthoritySelect").focus();
    });
    if ($("aaDetailToggle")) $("aaDetailToggle").addEventListener("click", function () {
      if (!_aaDetail) return;
      var a = _aaDetail;
      if (a.is_active) {
        if (!confirm("Are you sure you want to deactivate this authority administrator?\n\nDeactivated admins can no longer log in and existing tokens are rejected immediately.")) return;
      } else {
        if (!confirm("Activate this authority administrator account?")) return;
      }
      apiJson(API + "/api/admin/authority-admins/" + encodeURIComponent(a.id) + "/toggle", "POST").then(function (res) {
        if (res.ok) { toast("Account " + (a.is_active ? "deactivated" : "activated"), "success"); openAADetail(a.id); loadAuthorityAdmins(); }
        else toast("Failed: " + extractApiError(res), "error");
      });
    });
    if ($("aaAuthoritySelect")) $("aaAuthoritySelect").addEventListener("change", updateAAPreview);
  }
  setTimeout(function () { wireAAEvents(); }, 0);

  // ===== Init =====
  console.log("[Admin] Initializing on " + API);
  if (token) showDash();

  // Init authority events after DOM ready
  initAuthorityEvents();
})();
