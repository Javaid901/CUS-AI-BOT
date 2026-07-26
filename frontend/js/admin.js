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
    $("userLabel").style.display = "inline"; $("logoutBtn").style.display = "inline";
    $("userLabel").textContent = "Admin";
    loadHealth();
    loadDocs();
    connectSSE();
  }
  function showLogin() {
    setToken(null);
    disconnectSSE();
    dashView.style.display = "none"; loginView.style.display = "block";
    $("userLabel").style.display = "none"; $("logoutBtn").style.display = "none";
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
    fetch(API + "/api/documents/upload", { method: "POST", headers: authHeaders(), body: fd })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .then(function (res) {
        dz.classList.remove("disabled");
        if (!res) return;
        var card = document.querySelector('.file-card[data-filename="' + escAttr(file.name) + '"]');
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
        var card = document.querySelector('.file-card[data-filename="' + escAttr(file.name) + '"]');
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
          row.innerHTML =
            '<div class="fi">\u{1f4c4}</div>' +
            '<div class="meta"><div class="nm">' + esc(d.filename || d.title || "Document") + '</div>' +
            '<div class="st ' + statusClass + '">' + esc(d.status || "processing") + (d.chunks ? " \u00b7 " + d.chunks + " chunks" : "") + '</div></div>' +
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

  // ===== Sync =====
  $("syncBtn").addEventListener("click", function () {
    var btn = this; btn.disabled = true; btn.textContent = "\u23f3 Syncing...";
    fetch(API + "/api/admin/sync-website", { method: "POST", headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (res) {
        if (res) {
          toast("Sync complete: " + (res.downloaded || 0) + " new files", "success");
          loadHealth();
        }
      })
      .catch(function () { toast("Sync failed", "error"); })
      .finally(function () { btn.disabled = false; btn.textContent = "\u{1f504} Sync Official Website"; });
  });

  $("refreshBtn").addEventListener("click", function () { loadHealth(); loadDocs(); updateJobDashboard(); });

  // ===== Knowledge Sync Tab =====
  function syncLog(msg) {
    var el = $("syncLog");
    var t = new Date().toLocaleTimeString();
    el.innerHTML = '<div>[' + t + '] ' + esc(msg) + '</div>' + el.innerHTML;
  }

  function loadSyncStatus() {
    fetch(API + "/api/admin/knowledge-sync/status", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { handleUnauthorized(); return null; } return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        $("ksTotal").textContent = d.total || d.manifest?.total_files || 0;
        $("ksIngested").textContent = d.ingested || d.manifest?.ingested || 0;
        $("ksPending").textContent = d.downloaded || d.manifest?.pending_review || 0;
        $("ksFailed").textContent = d.failed || 0;
      })
      .catch(function () { syncLog("Failed to load sync status"); });
  }

  function loadSyncSources() {
    fetch(API + "/api/admin/knowledge-sync/sources?limit=50", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { handleUnauthorized(); return []; } return r.ok ? r.json() : []; })
      .then(function (sources) {
        var list = $("syncSourceList");
        list.innerHTML = "";
        if (!sources.length) { list.innerHTML = '<p class="muted">No synced sources yet.</p>'; return; }
        sources.forEach(function (s) {
          var row = document.createElement("div");
          row.className = "doc-row";
          var statusClass = s.status === "ingested" ? "ok" : s.status === "failed" ? "err" : "proc";
          row.innerHTML =
            '<div class="fi">\u{1f4c4}</div>' +
            '<div class="meta"><div class="nm">' + esc(s.filename || s.url.slice(0, 80) + "...") + '</div>' +
            '<div class="st ' + statusClass + '">' + esc(s.status) + (s.year ? " \u00b7 " + s.year : "") + (s.category ? " \u00b7 " + s.category : "") + '</div></div>' +
            (s.status === "downloaded" ? '<button class="approve-sync">Approve &amp; Ingest</button>' : '') +
            (s.error ? '<span class="muted" title="' + esc(s.error) + '">\u26a0\ufe0f</span>' : '');
          var approveBtn = row.querySelector(".approve-sync");
          if (approveBtn) { approveBtn.addEventListener("click", function () { approveSync(s.id, row); }); }
          list.appendChild(row);
        });
      })
      .catch(function () { $("syncSourceList").innerHTML = '<p class="muted">Could not load sync sources.</p>'; });
  }

  function approveSync(id, row) {
    syncLog("Approving " + id + "...");
    fetch(API + "/api/admin/knowledge-sync/approve/" + id, { method: "POST", headers: authHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.status === "processing" || res.status === "ingested") {
          syncLog("Approved and queued " + id);
          loadSyncSources(); loadSyncStatus();
          toast("Sync source approved and queued for ingestion", "success");
        } else {
          syncLog("Approval failed: " + (res.error || res.status));
          toast("Approval failed", "error");
        }
      })
      .catch(function () { syncLog("Network error approving " + id); toast("Network error", "error"); });
  }

  $("syncRunBtn").addEventListener("click", function () {
    var btn = this; btn.disabled = true; btn.textContent = "\u23f3 Running...";
    var urls = $("syncUrls").value.trim();
    var autoDiscover = $("syncAutoDiscover").checked;
    var url = API + "/api/admin/knowledge-sync/run?auto_discover=" + autoDiscover;
    if (urls) url += "&urls=" + encodeURIComponent(urls);
    syncLog("Starting Knowledge Sync...");
    fetch(url, { method: "POST", headers: authHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.error) { syncLog("Sync error: " + res.error); toast("Sync error: " + res.error, "error"); }
        else {
          syncLog("Sync complete: " + res.downloaded + " downloaded, " + res.duplicates + " duplicates, " + res.failed + " failed");
          toast("Sync complete: " + res.downloaded + " files", "success");
          loadDocs();
          loadHealth();
          refreshInsightsIfVisible();
        }
        loadSyncStatus(); loadSyncSources();
      })
      .catch(function () { syncLog("Sync network error"); toast("Sync network error", "error"); })
      .finally(function () { btn.disabled = false; btn.textContent = "\u25b6 Run Sync"; });
  });

  $("syncRefreshBtn").addEventListener("click", function () { loadSyncStatus(); loadSyncSources(); });

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
      if (btn.dataset.tab === "sync") { loadSyncStatus(); loadSyncSources(); }
      if (btn.dataset.tab === "colleges") { loadColleges(); }
      if (btn.dataset.tab === "insights") {
        if (window.CUS && window.CUS.insightsInit) window.CUS.insightsInit();
      }
      if (btn.dataset.tab === "authorities") {
        setTimeout(function () { loadAuthorities(); }, 50);
      }
    });
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
            '<button class="view-college">View</button>';
          card.querySelector(".view-college").addEventListener("click", function () { showCollegeDetail(c.id); });
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

  // ===== Authority Management =====
  var _authorities = [];
  var _authSearchTimer = 0;

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
        '<td><div class="auth-cell-dept">' + esc(a.department_name || "-") + '</div></td>' +
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

    return _authorities.filter(function (a) {
      if (dept && a.department_name !== dept) return false;
      if (status === "active" && a.active === false) return false;
      if (status === "inactive" && a.active !== false) return false;
      if (search) {
        var haystack = ((a.authority_name || "") + " " + (a.department_name || "") + " " + (a.description || "") + " " + (a.phone || "") + " " + (a.email || "") + " " + (a.designation || "")).toLowerCase();
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

  // Toggle authority active status
  function toggleAuthority(id) {
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
          toast("Status toggled", "success");
          loadAuthorities();
        }
      })
      .catch(function () { toast("Toggle failed", "error"); });
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
    resultEl.innerHTML = '<div class="auth-loading">Uploading ' + esc(file.name) + '...</div>';

    var fd = new FormData();
    fd.append("file", file);

    fetch(API + "/api/admin/authorities/bulk-import", {
      method: "POST",
      headers: authHeaders(),
      body: fd,
    })
      .then(function (r) {
        if (r.status === 401) { handleUnauthorized(); return null; }
        return r.json().then(function (d) { return { ok: r.ok, data: d }; });
      })
      .then(function (res) {
        if (!res) { resultEl.innerHTML = ""; return; }
        if (res.ok) {
          var d = res.data;
          var html = '<div class="import-summary">' +
            '<div class="is-box"><div class="is-num">' + (d.imported || 0) + '</div><div class="is-label">Imported</div></div>' +
            '<div class="is-box is-err"><div class="is-num">' + (d.skipped || 0) + '</div><div class="is-label">Skipped</div></div>' +
            '<div class="is-box is-err"><div class="is-num">' + (d.errors ? d.errors.length : 0) + '</div><div class="is-label">Errors</div></div>' +
            '</div>';
          if (d.errors && d.errors.length) {
            html += '<div class="import-errors">';
            d.errors.forEach(function (e) { html += '<div>' + esc(typeof e === "string" ? e : (e.error || JSON.stringify(e))) + '</div>'; });
            html += '</div>';
          }
          resultEl.innerHTML = html;
          toast((d.imported || 0) + " authorities imported", "success");
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

  // ===== Init =====
  console.log("[Admin] Initializing on " + API);
  if (token) showDash();

  // Init authority events after DOM ready
  initAuthorityEvents();
})();
