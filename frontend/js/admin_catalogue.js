(function () {
  "use strict";

  if (!window.CUS_API_BASE) throw new Error("CUS_API_BASE not defined");
  var API = window.CUS_API_BASE;
  var BASE = API + "/api/admin/catalogue";

  var token = localStorage.getItem("cus_admin_token") || null;
  function authHeaders() {
    var h = { "Content-Type": "application/json" };
    if (token) h.Authorization = "Bearer " + token;
    return h;
  }
  function log(m) { console.log("[CUS-Catalogue] " + m); }

  function req(method, url, body) {
    var opts = { method: method, headers: authHeaders() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) {
      if (r.status === 401) { log("Unauthorized, reloading"); window.location.reload(); throw new Error("Unauthorized"); }
      return r.json().then(function (d) { if (!r.ok) throw new Error((d && d.detail) || ("HTTP " + r.status)); return d; });
    });
  }
  function get(url) { return req("GET", url); }
  function post(url, body) { return req("POST", url, body); }
  function put(url, body) { return req("PUT", url, body); }
  function del(url) { return req("DELETE", url); }

  var esc = function (s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };

  var ROOT_ID = "catalogueRoot";
  var _view = "list";
  var _data = { programmes: [], categories: [], schemes: [] };

  function root() { return document.getElementById(ROOT_ID); }

  // ========== API data ==========
  function loadAll() {
    return Promise.all([get(BASE + "/programmes"), get(BASE + "/categories"), get(BASE + "/schemes")]).then(function (r) {
      _data.programmes = r[0];
      _data.categories = r[1];
      _data.schemes = r[2];
    });
  }

  async function refresh() {
    await loadAll();
    render();
  }

  // ========== Render ==========

  function render() {
    if (!root()) return;
    if (_view === "list") renderList();
    else if (_view === "programme") renderProgramme();
    else if (_view === "schemes") renderSchemes();
    else if (_view === "uploads") renderUploads();
    else if (_view === "upload_review") renderUploadReview();
  }

  function _tabBar() {
    return '<div class="cat-tabs" style="display:flex;gap:8px;margin-bottom:14px;">' +
      '<button class="btn sm' + (_view === "list" ? " green" : "") + '" id="catTabProgs">Programmes</button>' +
      '<button class="btn sm' + (_view === "schemes" ? " green" : "") + '" id="catTabSchemes">Academic Schemes</button>' +
      '<button class="btn sm' + (_view === "uploads" || _view === "upload_review" ? " green" : "") + '" id="catTabUploads">Curriculum Uploads</button>' +
      "</div>";
  }

  function renderList() {
    var progs = _data.programmes || [];
    var schemes = _data.schemes || [];
    var schemeByName = {};
    schemes.forEach(function (s) { schemeByName[s.id] = s.name; });
    var html = "";
    html += _tabBar();
    html += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px;">';
    html += '<div class="kpi kpi-sm" style="flex:1;min-width:200px;">' +
      '<div class="box"><div class="n">' + progs.length + '</div><div class="l">Programmes</div></div>' +
      '</div>';
    html += '<button class="btn green" id="catNewProg">+ Add Programme</button>';
    html += '</div>';
    html += '<div style="overflow-x:auto;"><table class="admin-table" style="width:100%;border-collapse:collapse;">';
    html += '<thead><tr><th>Code</th><th>Programme</th><th>Level</th><th>Scheme</th><th>Duration</th><th>Credits</th><th>Major Disciplines</th><th></th></tr></thead><tbody>';
    progs.forEach(function (p) {
      html += '<tr data-pid="' + esc(p.id) + '">';
      html += '<td><b>' + esc(p.code) + '</b></td>';
      html += '<td>' + esc(p.name) + '</td>';
      html += '<td>' + esc(p.degree_level || "-") + '</td>';
      html += '<td>' + esc(schemeByName[p.scheme_id] || p.academic_scheme || "-") + '</td>';
      html += '<td>' + esc(p.duration_years || "-") + ' yr</td>';
      html += '<td>' + esc(p.total_credits || "-") + '</td>';
      html += '<td style="max-width:260px;">' + esc((p.major_disciplines || []).join(", ")) + '</td>';
      html += '<td><button class="btn sm ghost" data-act="open" data-id="' + esc(p.id) + '">Manage</button> ';
      html += '<button class="btn sm" data-act="delete" data-id="' + esc(p.id) + '">Delete</button></td>';
      html += '</tr>';
    });
    html += progs.length ? "" : '<tr><td colspan="8" style="text-align:center;color:var(--muted);">No programmes yet. Add one to populate the academic catalogue (seed data loads on startup in demo mode).</td></tr>';
    html += '</tbody></table></div>';
    root().innerHTML = html;

    var nb = document.getElementById("catNewProg");
    if (nb) nb.addEventListener("click", showProgrammeForm);
    document.getElementById("catTabSchemes").addEventListener("click", function () { _view = "schemes"; render(); });
    document.getElementById("catTabUploads").addEventListener("click", function () { openUploads(); });
    root().querySelectorAll("[data-act=open]").forEach(function (b) {
      b.addEventListener("click", function () { openProgramme(b.dataset.id); });
    });
    root().querySelectorAll("[data-act=delete]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (!confirm("Delete programme " + b.dataset.id + " and its subjects?")) return;
        del(BASE + "/programmes/" + b.dataset.id).then(refresh).catch(function (e) { alert(e.message); });
      });
    });
  }

  function renderSchemes() {
    var schemes = _data.schemes || [];
    var html = "";
    html += _tabBar();
    html += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px;">';
    html += '<div class="kpi kpi-sm" style="flex:1;min-width:200px;">' +
      '<div class="box"><div class="n">' + schemes.length + '</div><div class="l">Academic Schemes</div></div>' +
      '</div>';
    html += '<button class="btn green" id="catNewScheme">+ Add Scheme</button>';
    html += '</div>';
    html += '<div style="overflow-x:auto;"><table class="admin-table" style="width:100%;border-collapse:collapse;">';
    html += '<thead><tr><th>Name</th><th>Code</th><th>Programmes</th><th>Description</th><th>Active</th><th></th></tr></thead><tbody>';
    schemes.forEach(function (s) {
      html += '<tr data-sid="' + esc(s.id) + '">';
      html += '<td><b>' + esc(s.name) + '</b></td>';
      html += '<td>' + esc(s.code || "-") + '</td>';
      html += '<td>' + esc(s.programme_count || 0) + '</td>';
      html += '<td style="max-width:320px;">' + esc(s.description || "-") + '</td>';
      html += '<td>' + (s.is_active === false ? "No" : "Yes") + '</td>';
      html += '<td><button class="btn sm ghost" data-act="editScheme" data-id="' + esc(s.id) + '">Edit</button> ';
      html += '<button class="btn sm" data-act="delScheme" data-id="' + esc(s.id) + '">Delete</button></td>';
      html += '</tr>';
    });
    html += schemes.length ? "" : '<tr><td colspan="6" style="text-align:center;color:var(--muted);">No academic schemes yet.</td></tr>';
    html += '</tbody></table></div>';
    root().innerHTML = html;

    document.getElementById("catTabProgs").addEventListener("click", function () { _view = "list"; render(); });
    document.getElementById("catTabUploads").addEventListener("click", function () { openUploads(); });
    document.getElementById("catNewScheme").addEventListener("click", showSchemeForm);
    root().querySelectorAll("[data-act=editScheme]").forEach(function (b) {
      b.addEventListener("click", function () { showSchemeForm(b.dataset.id); });
    });
    root().querySelectorAll("[data-act=delScheme]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (!confirm("Delete academic scheme " + b.dataset.id + "?\n(Programmes linked to it keep their legacy academic_scheme tag.)")) return;
        del(BASE + "/schemes/" + b.dataset.id).then(refresh).catch(function (e) { alert(e.message); });
      });
    });
  }

  function showSchemeForm(id) {
    var editing = null;
    if (id) {
      for (var i = 0; i < _data.schemes.length; i++) if (_data.schemes[i].id === id) { editing = _data.schemes[i]; break; }
    }
    var html = '<div style="max-width:560px;">';
    html += '<h3>' + (editing ? "Edit Academic Scheme" : "Add Academic Scheme") + '</h3>';
    html += '<div class="cat-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">';
    html += _field("schName", "Name", editing ? editing.name : "", "text");
    html += _field("schCode", "Code", editing ? (editing.code || "") : "", "text");
    html += _field("schSort", "Sort Order", editing ? (editing.sort_order != null ? editing.sort_order : "0") : "0", "number");
    html += '<label>Active<select id="schActive"><option value="1">Yes</option><option value="0"' + (editing && editing.is_active === false ? " selected" : "") + '>No</option></select></label>';
    html += '</div>';
    html += '<div style="margin-top:10px;">' + _field("schDesc", "Description", editing ? (editing.description || "") : "", "textarea") + "</div>";
    html += '<div style="margin-top:12px;"><button class="btn green" id="schSave">Save</button> <button class="btn" id="schCancel">Cancel</button></div>';
    html += "</div>";
    root().innerHTML = html;
    document.getElementById("schSave").addEventListener("click", function () { saveScheme(id); });
    document.getElementById("schCancel").addEventListener("click", function () { _view = "schemes"; render(); });
  }

  function saveScheme(id) {
    var body = {
      name: $v("schName"),
      code: $v("schCode") || null,
      description: $v("schDesc") || null,
      sort_order: parseInt($v("schSort") || "0", 10) || 0,
      is_active: $v("schActive") === "1",
    };
    if (!body.name) { alert("Scheme name required"); return; }
    var p = id ? put(BASE + "/schemes/" + id, body) : post(BASE + "/schemes", body);
    p.then(function () { _view = "schemes"; refresh(); }).catch(function (e) { alert(e.message); });
  }

  function _catOptions(selectedId) {
    var c = _data.categories || [];
    return c.map(function (x) {
      var sel = x.id === selectedId ? " selected" : "";
      return '<option value="' + esc(x.id) + '"' + sel + ">" + esc(x.name) + " (" + esc(x.level_label) + ')</option>';
    }).join("");
  }

  function _schemeOptions(selectedId, legacy) {
    var schemes = _data.schemes || [];
    var opts = schemes.map(function (s) {
      var sel = s.id === selectedId ? " selected" : "";
      return '<option value="' + esc(s.id) + '"' + sel + ">" + esc(s.name) + " (" + esc(s.code || "-") + ')</option>';
    }).join("");
    if (legacy) {
      opts = '<option value=""' + (selectedId ? "" : " selected") + ">Legacy: " + esc(legacy) + "</option>" + opts;
    }
    return opts;
  }

  function _feeRowsHtml(entries) {
    var list = entries && entries.length ? entries : [{ label: "", value: "" }];
    var html = '<div id="feeRows">';
    list.forEach(function (e, i) {
      html += '<div class="fee-row" style="display:flex;gap:6px;margin-bottom:6px;">';
      html += '<input id="feeLbl_' + i + '" placeholder="Label (e.g. Annual Tuition Fee)" value="' + esc(e.label || "") + '" style="flex:2;padding:6px;border:1px solid var(--border);border-radius:5px;"/>';
      html += '<input id="feeVal_' + i + '" placeholder="Amount (e.g. Rs. 45,000)" value="' + esc(e.value || "") + '" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:5px;"/>';
      html += '<button class="btn sm" data-act="feeDel" data-i="' + i + '">\u2212</button></div>';
    });
    html += "</div>";
    html += '<button class="btn sm ghost" id="feeAdd">+ Add Fee Entry</button>';
    return html;
  }

  function _collectFeeRows() {
    var entries = [];
    var rows = document.querySelectorAll(".fee-row");
    rows.forEach(function (row) {
      var lbl = row.querySelector("input[placeholder^='Label']");
      var val = row.querySelector("input[placeholder^='Amount']");
      if (lbl && lbl.value.trim()) entries.push({ label: lbl.value.trim(), value: val ? val.value.trim() : "" });
    });
    return entries;
  }

  function showProgrammeForm(prog) {
    var editing = Boolean(prog);
    var p = prog || {};
    var html = '<div style="max-width:680px;">';
    html += '<h3>' + (editing ? "Edit Programme" : "Add Programme") + '</h3><div class="cat-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">';
    html += _field("catName", "Name", p.name || "", "text");
    html += _field("catCode", "Code", p.code || "", "text");
    html += '<label>Category<select id="catCat">' + _catOptions(p.category_id) + "</select></label>";
    html += '<label>Academic Scheme<select id="catScheme">' + _schemeOptions(p.scheme_id, p.academic_scheme) + "</select></label>";
    html += _field("catDegree", "Degree Level", p.degree_level || "Bachelor", "text");
    html += _field("catYears", "Duration (years)", p.duration_years != null ? String(p.duration_years) : "3", "number");
    html += _field("catCredits", "Total Credits", p.total_credits != null ? String(p.total_credits) : "160", "number");
    html += _field("catDisciplines", "Major Disciplines (comma separated)", (p.major_disciplines || []).join(", "), "text");
    html += '</div>';
    html += '<div style="margin-top:10px;">' + _field("catEligibility", "Eligibility", p.eligibility || "", "textarea") + "</div>";
    html += '<div style="margin-top:10px;"><h4 style="margin:0 0 6px;">Fee Structure</h4>' + _feeRowsHtml(p.fee_structure) + "</div>";
    html += '<div style="margin-top:10px;">' + _field("catDesc", "Description", p.description || "", "textarea") + "</div>";
    html += '<div style="margin-top:12px;"><button class="btn green" id="catSaveProg">Save</button> <button class="btn" id="catCancelProg">Cancel</button></div>';
    html += "</div>";
    root().innerHTML = html;
    document.getElementById("catSaveProg").addEventListener("click", function () { saveProgramme(prog); });
    document.getElementById("catCancelProg").addEventListener("click", function () { _view = editing ? "programme" : "list"; refresh(); });
    document.getElementById("feeAdd").addEventListener("click", function () {
      var i = document.querySelectorAll(".fee-row").length;
      var div = document.createElement("div");
      div.className = "fee-row";
      div.style.cssText = "display:flex;gap:6px;margin-bottom:6px;";
      div.innerHTML = '<input id="feeLbl_' + i + '" placeholder="Label (e.g. Annual Tuition Fee)" value="" style="flex:2;padding:6px;border:1px solid var(--border);border-radius:5px;"/>' +
        '<input id="feeVal_' + i + '" placeholder="Amount (e.g. Rs. 45,000)" value="" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:5px;"/>' +
        '<button class="btn sm" data-act="feeDel" data-i="' + i + '">\u2212</button>';
      document.getElementById("feeRows").appendChild(div);
      bindFeeDel();
    });
    bindFeeDel();
  }

  function bindFeeDel() {
    root().querySelectorAll("[data-act=feeDel]").forEach(function (b) {
      b.removeEventListener("click", feeDel);
      b.addEventListener("click", feeDel);
    });
  }

  function feeDel() {
    var row = this.closest(".fee-row");
    if (row) row.parentNode.removeChild(row);
  }

  function saveProgramme(prog) {
    var editing = Boolean(prog);
    var body = {
      name: $v("catName"),
      code: $v("catCode"),
      category_id: $v("catCat") || null,
      scheme_id: $v("catScheme") || null,
      degree_level: $v("catDegree"),
      duration_years: parseInt($v("catYears") || "0", 10) || null,
      total_credits: parseInt($v("catCredits") || "0", 10) || null,
      major_disciplines: ($v("catDisciplines") || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean),
      eligibility: $v("catEligibility") || null,
      fee_structure: _collectFeeRows(),
      description: $v("catDesc") || null,
    };
    if (!body.name || !body.code) { alert("Name and code are required"); return; }
    var p = editing ? put(BASE + "/programmes/" + prog.id, body) : post(BASE + "/programmes", body);
    p.then(function () {
      if (editing) { openProgramme(prog.id); } else { _view = "list"; refresh(); }
    }).catch(function (e) { alert(e.message); });
  }

  function _field(id, label, value, type) {
    if (type === "textarea") {
      return "<label>" + esc(label) + '<textarea id="' + id + '" style="width:100%;min-height:60px;padding:8px;border:1px solid var(--border);border-radius:6px;">' + esc(value) + "</textarea></label>";
    }
    return '<label>' + esc(label) + '<input id="' + id + '" type="' + type + '" value="' + esc(value) + '" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;"/></label>';
  }

  // ========== Programme detail ==========
  function openProgramme(id) {
    get(BASE + "/programmes/" + id).then(function (p) {
      _view = "programme";
      _data.active = p;
      renderProgramme();
    }).catch(function (e) { alert(e.message); });
  }

  function renderProgramme() {
    var p = _data.active;
    if (!p) { _view = "list"; render(); return; }
    var html = "";
    html += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">';
    html += '<h3 style="margin:0;">' + esc(p.name) + ' (<b>' + esc(p.code) + '</b>)</h3>';
    html += '<div style="display:flex;gap:8px;"><button class="btn btn-sm ghost" id="catEditProg">Edit Details</button> ';
    html += '<button class="btn btn-sm" id="catBack">\u2190 Back</button></div></div>';
    html += '<div class="cat-meta" style="display:flex;gap:18px;flex-wrap:wrap;color:var(--muted);font-size:13px;margin-bottom:14px;">';
    html += '<span>Level: ' + esc(p.degree_level || "-") + "</span>";
    html += '<span>Scheme: ' + esc(_schemeName(p.scheme_id) || p.academic_scheme || "-") + "</span>";
    html += '<span>Years: ' + esc(p.duration_years || "-") + "</span>";
    html += '<span>Credits: ' + esc(p.total_credits || "-") + "</span>";
    html += "</div>";

    html += _section("Eligibility", false, "eligibility");
    html += p.eligibility
      ? '<p style="margin:0 0 6px;font-size:13px;">' + esc(p.eligibility) + "</p>"
      : '<p style="margin:0 0 6px;color:var(--muted);font-size:13px;">Not set.</p>';

    html += _section("Fee Structure", false, "fee");
    var fees = p.fee_structure || [];
    if (fees.length) {
      html += '<table class="cat-table" style="width:100%;border-collapse:collapse;font-size:13px;max-width:420px;"><tbody>';
      fees.forEach(function (f) {
        html += "<tr><td>" + esc(f.label || "") + "</td><td style='text-align:right;'><b>" + esc(f.value || "") + "</b></td></tr>";
      });
      html += "</tbody></table>";
    } else {
      html += '<p style="margin:0 0 6px;color:var(--muted);font-size:13px;">Not set.</p>';
    }

    html += _section("Subjects (" + (p.subjects || []).length + ")", true, "subjects");
    html += '<table class="cat-table" style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr><th>Code</th><th>Name</th><th>Category</th><th>Semester</th><th>Credits</th><th></th></tr></thead><tbody>';
    (p.subjects || []).forEach(function (s) {
      html += '<tr><td>' + esc(s.subject_code || "-") + "</td><td>" + esc(s.subject_name) + "</td><td>" + esc(s.category_label || s.category) + "</td><td>" + esc(s.semester || "-") + "</td><td>" + esc(s.credits || "-") + "</td>";
      html += '<td><button class="btn btn-sm" data-act="delSubj" data-id="' + esc(s.id) + '">Remove</button></td></tr>';
    });
    html += '</tbody></table>';
    html += '<div style="display:grid;grid-template-columns:120px 150px 220px 90px 80px auto;gap:8px;margin-top:10px;">';
    html += '<input id="nS_Code" placeholder="Code" style="padding:6px;border:1px solid var(--border);border-radius:5px;"/>';
    html += '<select id="nS_Cat"><option value="major">Major</option><option value="minor">Minor</option><option value="vac">VAC</option><option value="sec">SEC</option><option value="aec">AEC</option><option value="generic">Generic</option></select>';
    html += '<input id="nS_Name" placeholder="Subject name" style="padding:6px;border:1px solid var(--border);border-radius:5px;"/>';
    html += '<input id="nS_Sem" placeholder="Semester" type="number" style="padding:6px;border:1px solid var(--border);border-radius:5px;">';
    html += '<input id="nS_Credits" placeholder="Credits" type="number" style="padding:6px;border:1px solid var(--border);border-radius:5px;">';
    html += '<button class="btn btn-sm green" id="btnAddSubject">Add</button></div>';

    html += _section("Minor Disciplines (" + (p.minors || []).length + ")", true, "minors");
    html += '<ul style="margin:4px 0 8px;">';
    (p.minors || []).forEach(function (m) {
      html += '<li>' + esc(m.name) + ' <button class="btn btn-sm" data-act="delMinor" data-id="' + esc(m.id) + '">Remove</button></li>';
    });
    html += (p.minors || []).length ? "" : '<li style="color:var(--muted);">No minors.</li>';
    html += "</ul>";
    html += '<div style="display:flex;gap:8px;"><input id="nM_Name" placeholder="Minor discipline name" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:5px;"/><button class="btn btn-sm" id="btnAddMinor">Add Minor</button></div>';

    html += _section("Learning Outcomes", true, "outcomes");
    html += '<textarea id="outcomesText" style="width:100%;min-height:90px;padding:8px;border:1px solid var(--border);border-radius:6px;" placeholder="One outcome per line...">' + esc((p.outcomes || []).join("\n")) + "</textarea>";
    html += '<button class="btn btn-sm green" id="btnSaveOutcomes" style="margin-top:8px;">Save Outcomes</button>';

    root().innerHTML = html;

    document.getElementById("catBack").addEventListener("click", function () { _view = "list"; refresh(); });
    document.getElementById("catEditProg").addEventListener("click", function () { showProgrammeForm(_data.active); });
    document.getElementById("btnAddSubject").addEventListener("click", addSubject);
    document.getElementById("btnAddMinor").addEventListener("click", addMinor);
    document.getElementById("btnSaveOutcomes").addEventListener("click", saveOutcomes);
    root().querySelectorAll("[data-act=delSubj]").forEach(function (b) {
      b.addEventListener("click", function () { del(BASE + "/subjects/" + b.dataset.id).then(function () { openProgramme(p.id); }).catch(function (e) { alert(e.message); }); });
    });
    root().querySelectorAll("[data-act=delMinor]").forEach(function (b) {
      b.addEventListener("click", function () { del(BASE + "/minors/" + b.dataset.id).then(function () { openProgramme(p.id); }).catch(function (e) { alert(e.message); }); });
    });
  }

  function _section(title, open, key) {
    return '<h4 style="margin:16px 0 6px;">' + esc(title) + "</h4>";
  }

  function _schemeName(id) {
    var schemes = _data.schemes || [];
    for (var i = 0; i < schemes.length; i++) if (schemes[i].id === id) return schemes[i].name;
    return null;
  }

  function addSubject() {
    var p = _data.active;
    var name = $("nS_Name");
    if (!name) { alert("Subject name required"); return; }
    var body = {
      subject_name: name,
      subject_code: $("nS_Code") || null,
      category: $("nS_Cat"),
      semester: $("nS_Sem") ? parseInt($("nS_Sem"), 10) : null,
      credits: $("nS_Credits") ? parseInt($("nS_Credits"), 10) : null,
    };
    post(BASE + "/programmes/" + p.id + "/subjects", body)
      .then(function () { return openProgramme(p.id); })
      .catch(function (e) { alert(e.message); });
  }

  function addMinor() {
    var p = _data.active;
    var name = $("nM_Name");
    if (!name) { alert("Minor name required"); return; }
    post(BASE + "/programmes/" + p.id + "/minors", { name: name })
      .then(function () { return openProgramme(p.id); })
      .catch(function (e) { alert(e.message); });
  }

  function saveOutcomes() {
    var p = _data.active;
    var list = ($("outcomesText") || "").split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
    put(BASE + "/programmes/" + p.id + "/outcomes", { outcomes: list })
      .then(function () { return openProgramme(p.id); })
      .catch(function (e) { alert(e.message); });
  }

  // ========== Curriculum uploads ==========
  function openUploads() {
    _view = "uploads";
    get(BASE + "/uploads").then(function (list) {
      _data.uploads = list;
      renderUploads();
    }).catch(function (e) { alert(e.message); });
  }

  function _upStatusBadge(u) {
    var cls = u.status === "active" ? "background:#0d9488;color:#fff;"
      : u.status === "archived" ? "background:#64748b;color:#fff;"
      : "background:#f59e0b;color:#fff;";
    return '<span style="padding:2px 8px;border-radius:10px;font-size:11px;' + cls + '">' + esc(u.status) + "</span>";
  }

  function renderUploads() {
    var list = _data.uploads || [];
    var html = "";
    html += _tabBar();
    html += '<div class="cat-meta" id="uploadSummary" style="display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-bottom:12px;"></div>';
    html += '<div style="border:1px dashed var(--border);border-radius:10px;padding:18px;margin-bottom:18px;">';
    html += '<h4 style="margin:0 0 6px;">Upload a Curriculum Document</h4>';
    html += '<p style="margin:0 0 12px;font-size:12px;color:var(--muted);">Formats: PDF, DOCX, DOC, CSV, XLSX, XLS. The file is parsed into structured catalogue data and stays a <b>draft</b> until you review and publish it.</p>';
    html += '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">';
    html += '<input type="file" id="upFile" accept=".pdf,.docx,.doc,.csv,.xlsx,.xls" style="flex:2;min-width:240px;"/>';
    html += '<input id="upProgramme" placeholder="Programme hint (optional)" title="e.g. Bachelor of Computer Applications" style="flex:1;min-width:160px;padding:6px;border:1px solid var(--border);border-radius:5px;"/>';
    html += '<select id="upLevel" style="padding:6px;border:1px solid var(--border);border-radius:5px;"><option value="">Level…</option><option>ug</option><option>pg</option><option>phd</option><option>integrated</option></select>';
    html += '<button class="btn green" id="upUpload">Upload &amp; Parse</button>';
    html += "</div></div>";
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">';
    html += '<h4 style="margin:0;">Uploads</h4>';
    html += '<button class="btn sm" id="upRefresh" style="visibility:hidden;">Refresh</button></div>';
    html += '<div style="overflow-x:auto;"><table class="admin-table" style="width:100%;border-collapse:collapse;">';
    html += '<thead><tr><th>File</th><th>Programme</th><th>Level</th><th>Session</th><th>Status</th><th>Parse</th><th></th></tr></thead><tbody>';
    list.forEach(function (u) {
      var warnings = (u.warnings || []).length;
      html += '<tr data-uid="' + esc(u.id) + '">';
      html += "<td><b>" + esc(u.filename) + "</b><br/><span style='font-size:11px;color:var(--muted);'>v" + esc(u.version || 1) + (u.revision ? " · " + esc(u.revision) : "") + "</span></td>";
      html += "<td>" + esc(u.programme_name || "-") + (u.programme_code ? " (" + esc(u.programme_code) + ")" : "") + "</td>";
      html += "<td>" + esc(u.level || "-") + "</td>";
      html += "<td>" + esc(u.academic_session || "-") + "</td>";
      html += "<td>" + _upStatusBadge(u) + "</td>";
      html += "<td>" + (u.parse_status === "failed" ? '<span style="color:#ef4444;">failed</span>' : u.parse_status === "ok"
        ? '<span style="color:#0d9488;">ok</span>' : '<span style="color:#b45309;">partial</span>') + (warnings ? " <span title='" + esc((u.warnings || []).join(" • ")) + "' style='cursor:help;'>(⚠ " + warnings + ")</span>" : "") + "</td>";
      html += "<td style='white-space:nowrap;'>";
      html += '<button class="btn sm ghost" data-act="upReview" data-id="' + esc(u.id) + '">' + (u.status === "draft" ? "Review" : "View") + "</button> ";
      html += '<button class="btn sm" data-act="upDownload" data-id="' + esc(u.id) + '">Download</button> ';
      if (u.status === "draft") html += '<button class="btn sm green" data-act="upPublish" data-id="' + esc(u.id) + '">Publish</button>';
      else if (u.status === "active") html += '<button class="btn sm" data-act="upArchive" data-id="' + esc(u.id) + '">Archive</button>';
      else html += '<button class="btn sm" data-act="upArchive" data-id="' + esc(u.id) + '">Archive</button>';
      if (u.status !== "active") html += ' <button class="btn sm" data-act="upDelete" data-id="' + esc(u.id) + '">Delete</button>';
      html += "</td></tr>";
    });
    html += list.length ? "" : '<tr><td colspan="7" style="text-align:center;color:var(--muted);">No curriculum uploads yet.</td></tr>';
    html += "</tbody></table></div>";
    root().innerHTML = html;

    var counts = { draft: 0, active: 0, archived: 0 };
    list.forEach(function (u) { counts[u.status] = (counts[u.status] || 0) + 1; });
    document.getElementById("uploadSummary").innerHTML =
      "<span><b>" + counts.draft + "</b> drafts</span>" +
      "<span><b>" + counts.active + "</b> active</span>" +
      "<span><b>" + counts.archived + "</b> archived</span>";

    document.getElementById("catTabProgs").addEventListener("click", function () { _view = "list"; render(); });
    document.getElementById("catTabSchemes").addEventListener("click", function () { _view = "schemes"; render(); });
    document.getElementById("upUpload").addEventListener("click", doUpload);
    root().querySelectorAll("[data-act=upReview]").forEach(function (b) {
      b.addEventListener("click", function () { openUploadReview(b.dataset.id); });
    });
    root().querySelectorAll("[data-act=upDownload]").forEach(function (b) {
      b.addEventListener("click", function () {
        var a = document.createElement("a");
        a.href = BASE + "/uploads/" + b.dataset.id + "/download";
        a.download = "";
        document.body.appendChild(a); a.click(); a.remove();
      });
    });
    root().querySelectorAll("[data-act=upPublish]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (!confirm("Publish this curriculum as the ACTIVE programme source?")) return;
        post(BASE + "/uploads/" + b.dataset.id + "/publish").then(function () { openUploads(); }).catch(function (e) { alert(e.message); });
      });
    });
    root().querySelectorAll("[data-act=upArchive]").forEach(function (b) {
      b.addEventListener("click", function () {
        post(BASE + "/uploads/" + b.dataset.id + "/archive").then(function () { openUploads(); }).catch(function (e) { alert(e.message); });
      });
    });
    root().querySelectorAll("[data-act=upDelete]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (!confirm("Delete this upload?")) return;
        del(BASE + "/uploads/" + b.dataset.id).then(function () { openUploads(); }).catch(function (e) { alert(e.message); });
      });
    });
  }

  function doUpload() {
    var fileInput = document.getElementById("upFile");
    if (!fileInput.files || !fileInput.files.length) { alert("Choose a file first"); return; }
    var fd = new FormData();
    fd.append("file", fileInput.files[0]);
    var progName = document.getElementById("upProgramme").value.trim();
    if (progName) fd.append("programme_name", progName);
    var lvl = document.getElementById("upLevel").value;
    if (lvl) fd.append("level", lvl);
    var btn = document.getElementById("upUpload");
    btn.disabled = true; btn.textContent = "Parsing…";
    var headers = { Authorization: token ? "Bearer " + token : "" };
    var opts = { method: "POST", headers: headers, body: fd };
    function _do() {
      return fetch(BASE + "/uploads", opts).then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) {
            if (d && d.detail && d.detail.duplicate) {
              var ex = d.detail.existing || {};
              if (confirm("Duplicate detected — the same bytes were already uploaded (" + (ex.filename || "?") + ", " + (ex.status || "?") + ").\nUpload anyway (archives the old copy)?")) {
                fd.append("replace_duplicate", "replace");
                return _do();
              }
              throw new Error("Upload skipped (duplicate).");
            }
            throw new Error((d && d.detail) || ("HTTP " + r.status));
          }
          return d;
        });
      });
    }
    _do().then(function () {
      openUploads();
    }).catch(function (e) { alert(e.message); }).finally(function () {
      btn.disabled = false; btn.textContent = "Upload & Parse";
    });
  }

  function openUploadReview(upload_id) {
    get(BASE + "/uploads/" + upload_id).then(function (u) {
      _view = "upload_review";
      _data.activeUpload = u;
      renderUploadReview();
    }).catch(function (e) { alert(e.message); });
  }

  function renderUploadReview() {
    var u = _data.activeUpload;
    if (!u) { _view = "uploads"; openUploads(); return; }
    var payload = u.payload || {};
    var prog = payload.programme || {};
    var semesters = payload.semesters || [];
    var minors = payload.minors || [];
    var outcomes = payload.outcomes || [];
    var fees = payload.fee_structure || [];
    var html = "";
    html += _tabBar();
    html += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">';
    html += "<h3 style='margin:0;'>Review: " + esc(u.filename) + "</h3>";
    html += '<div style="display:flex;gap:8px;flex-wrap:wrap;">' + _upStatusBadge(u);
    html += '<button class="btn btn-sm ghost" id="urBack">\u2190 Back</button>';
    html += '<button class="btn btn-sm" id="urSave">Save Changes</button>';
    if (u.status === "draft") html += '<button class="btn btn-sm green" id="urPublish">Publish</button>';
    html += "</div></div>";
    html += '<div class="cat-meta" style="display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:13px;margin-bottom:12px;">';
    html += "<span>File: <b>" + esc(u.filename) + "</b></span>";
    html += "<span>" + esc(u.file_size ? (u.file_size / 1024).toFixed(0) + " KB" : "-") + "</span>";
    html += "<span>Session: " + esc(u.academic_session || "-") + "</span>";
    html += "<span>Version: " + esc(u.version || 1) + "</span>";
    if (u.rag_status) html += "<span>RAG: <b>" + esc(u.rag_status) + "</b></span>";
    html += "</div>";

    if ((u.warnings || []).length) {
      html += '<div style="background:#5b3a12;border:1px solid #b45309;color:#fde68a;border-radius:8px;padding:10px 12px;margin-bottom:14px;font-size:13px;"><b>Parse warnings:</b><ul style="margin:6px 0 0;padding-left:18px;">';
      u.warnings.forEach(function (w) { html += "<li>" + esc(w) + "</li>"; });
      html += "</ul></div>";
    }

    // Editable header fields (mirror the CurriculumUpload columns)
    html += _section("Programme details", false, "");
    var grid = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;">';
    grid += _field("rvProgramme", "Programme name", prog.name || u.programme_name, "text");
    grid += _field("rvCode", "Programme code", prog.code || u.programme_code, "text");
    grid += _field("rvLevel", "Level", u.level || prog.level || "", "text");
    grid += _field("rvSession", "Academic session", u.academic_session || "", "text");
    grid += _field("rvRevision", "Revision", u.revision || "", "text");
    grid += _field("rvYears", "Duration (years)", prog.duration_years || "", "number");
    grid += _field("rvCredits", "Total credits", prog.total_credits || "", "number");
    grid += "</div>";
    html += grid;
    html += '<div style="margin-top:10px;">' + _field("rvEligibility", "Eligibility", prog.eligibility || "", "textarea") + "</div>";
    html += '<div style="margin-top:10px;">' + _field("rvDescription", "Description", prog.description || "", "textarea") + "</div>";

    html += _section("Semester-wise subjects (" + semesters.length + " semesters / " + _countSubjects(semesters) + " subjects)", true, "sems");
    semesters.forEach(function (s, i) {
      html += '<h5 style="margin:12px 0 4px;">Semester ' + esc(s.number) + "</h5>";
      html += '<table class="cat-table" style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr><th>Category</th><th>Code</th><th>Subject</th><th>Credits</th><th>Hours</th></tr></thead><tbody>';
      (s.subjects || []).forEach(function (subj) {
        html += "<tr><td>" + esc(subj.category || "major") + "</td><td>" + esc(subj.code || "") + "</td><td>" + esc(subj.name || "") + "</td><td>" + esc(subj.credits != null ? subj.credits : "") + "</td><td>" + esc(subj.hours != null ? subj.hours : "") + "</td></tr>";
      });
      html += "</tbody></table>";
      if (!(s.subjects || []).length) html += '<p style="color:var(--muted);font-size:12px;">No subjects parsed for this semester.</p>';
    });
    if (!semesters.length) html += '<p style="color:var(--muted);font-size:13px;">No semesters parsed. Review the raw payload below or upload a better-structured file.</p>';

    html += _section("Minor disciplines (" + minors.length + ")", true, "");
    if (minors.length) {
      html += "<ul style='margin:4px 0 0;'>";
      minors.forEach(function (m) { html += "<li><b>" + esc(m.name) + "</b> (" + (m.subjects || []).length + " subjects)</li>"; });
      html += "</ul>";
    } else html += '<p style="color:var(--muted);font-size:13px;">None parsed.</p>';

    html += _section("Learning outcomes (" + outcomes.length + ")", true, "");
    html += '<textarea id="rvOutcomesText" style="width:100%;min-height:90px;padding:8px;border:1px solid var(--border);border-radius:6px;">' + esc(outcomes.join("\n")) + "</textarea>";

    if (fees.length) {
      html += _section("Fee structure", true, "");
      html += '<table class="cat-table" style="width:100%;max-width:420px;border-collapse:collapse;font-size:13px;"><tbody>';
      fees.forEach(function (f) { html += "<tr><td>" + esc(f.label || "") + "</td><td style='text-align:right;'><b>" + esc(f.value || "") + "</b></td></tr>"; });
      html += "</tbody></table>";
    }

    html += _section("Raw payload (editable JSON)", false, "json");
    html += '<textarea id="rvJson" style="width:100%;min-height:180px;padding:8px;border:1px solid var(--border);border-radius:6px;font-family:monospace;font-size:12px;">' + esc(JSON.stringify(payload, null, 2)) + "</textarea>";
    html += '<p style="color:var(--muted);font-size:12px;margin-top:4px;">Overwrite the JSON then save once — changes merge into the stored payload.</p>';

    root().innerHTML = html;
    document.getElementById("catTabProgs").addEventListener("click", function () { _view = "list"; render(); });
    document.getElementById("catTabSchemes").addEventListener("click", function () { _view = "schemes"; render(); });
    document.getElementById("catTabUploads").addEventListener("click", function () { openUploads(); });
    document.getElementById("urBack").addEventListener("click", function () { openUploads(); });
    document.getElementById("urSave").addEventListener("click", function () { saveUploadReview(u); });
    if (document.getElementById("urPublish")) document.getElementById("urPublish").addEventListener("click", function () {
      if (!confirm("Publish as the active curriculum source?")) return;
      saveUploadReview(u, true);
    });
  }

  function saveUploadReview(u, andPublish) {
    var json;
    try { json = JSON.parse(document.getElementById("rvJson").value || "{}"); }
    catch (e) { alert("Invalid JSON: " + e.message); return; }
    var prog = json.programme || {};
    json.programme = Object.assign(prog, {
      name: document.getElementById("rvProgramme").value.trim(),
      code: document.getElementById("rvCode").value.trim(),
      level: document.getElementById("rvLevel").value.trim(),
      duration_years: parseInt(document.getElementById("rvYears").value || "0", 10) || null,
      total_credits: parseInt(document.getElementById("rvCredits").value || "0", 10) || null,
      eligibility: document.getElementById("rvEligibility").value.trim() || null,
      description: document.getElementById("rvDescription").value.trim() || null,
    });
    json.academic_session = document.getElementById("rvSession").value.trim() || null;
    json.revision = document.getElementById("rvRevision").value.trim() || null;
    var outs = document.getElementById("rvOutcomesText").value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
    json.outcomes = outs;
    var body = {
      payload: json,
      level: json.programme.level || null,
      revision: json.revision,
      programme_name: json.programme.name || null,
      programme_code: json.programme.code || null,
    };
    put(BASE + "/uploads/" + u.id, body).then(function () {
      if (andPublish) return post(BASE + "/uploads/" + u.id + "/publish");
      return Promise.resolve();
    }).then(function () { openUploads(); })
      .catch(function (e) { alert(e.message); });
  }

  function _countSubjects(sems) {
    var n = 0;
    sems.forEach(function (s) { n += (s.subjects || []).length; });
    return n;
  }

  // ========== Init ==========
  function init() {
    log("Initializing Academic Catalogue manager");
    refresh();
  }

  function $(id) {
    var el = document.getElementById(id);
    return el ? el.value : "";
  }

  function $v(id) {
    var el = document.getElementById(id);
    if (!el) return "";
    if (el.type === "checkbox") return el.checked ? "1" : "";
    return el.value == null ? "" : String(el.value);
  }

  var initialized = false;
  function initOnce() {
    if (initialized) return;
    initialized = true;
    init();
  }

  window.CUS = window.CUS || {};
  window.CUS.catalogueInit = initOnce;
})();