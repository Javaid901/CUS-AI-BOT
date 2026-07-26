(function () {
  "use strict";

  if (!window.CUS_API_BASE) throw new Error("CUS_API_BASE not defined");
  var API = window.CUS_API_BASE;
  var BASE = API + "/api/admin/analytics";

  var COLORS = ["#0F5132","#145A32","#1E7E34","#28A745","#34CE7C","#6FCF97","#A3E4C4","#C8E6D3","#E8F5E9","#FFF3CD","#FED7AA","#FB923C","#FDBA74","#F97316","#DC2626","#991B1B"];

  var token = localStorage.getItem("cus_admin_token") || null;
  function authHeaders() { var h = {}; if (token) h.Authorization = "Bearer " + token; return h; }
  function log(m) { console.log("[CUS-Insights] " + m); }

  var _charts = {};
  function destroyChart(id) { if (_charts[id]) { try { _charts[id].destroy(); } catch(e) {} delete _charts[id]; } }

  function fetchJSON(url) {
    log("GET " + url);
    return fetch(url, { headers: authHeaders() }).then(function (r) {
      if (r.status === 401) { log("Unauthorized, reloading"); window.location.reload(); throw new Error("Unauthorized"); }
      if (!r.ok) { log("HTTP " + r.status + " for " + url); throw new Error("HTTP " + r.status); }
      return r.json();
    });
  }

  // ========== Empty state renderer ==========
  function emptyState(id, msg) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = '<div class="insight-empty"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#aaa" stroke-width="1.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg><p>' + (msg || "No analytics available yet. Data will appear once users interact with the chatbot.") + '</p></div>';
  }

  function showLoading(id) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = '<div class="insight-loading"><div class="spinner"></div><span>Loading analytics…</span></div>';
  }

  // ========== Initialize ==========
  var initialized = false;
  function init() {
    if (initialized) return;
    initialized = true;
    log("Initializing AI Insights dashboard");

    // Sub-navigation
    var navBtns = document.querySelectorAll(".insight-nav-btn");
    navBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        navBtns.forEach(function (b) { b.classList.remove("active"); b.style.background = "transparent"; b.style.color = "var(--text)"; b.style.borderColor = "var(--border)"; });
        btn.classList.add("active"); btn.style.background = "var(--green)"; btn.style.color = "#fff"; btn.style.borderColor = "var(--green)";
        document.querySelectorAll(".insight-section").forEach(function (s) { s.style.display = "none"; });
        var sid = "insightSection" + btn.dataset.section.charAt(0).toUpperCase() + btn.dataset.section.slice(1);
        var sec = document.getElementById(sid);
        if (sec) sec.style.display = "block";
        loadSection(btn.dataset.section);
      });
    });

    // Period
    var ps = document.getElementById("insightPeriod");
    if (ps) ps.addEventListener("change", function () {
      var a = document.querySelector(".insight-nav-btn.active");
      if (a) loadSection(a.dataset.section);
    });

    // Filter
    document.querySelectorAll(".insight-filter-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll(".insight-filter-btn").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        var a = document.querySelector(".insight-nav-btn.active");
        if (a) loadSection(a.dataset.section);
      });
    });

    // Search
    var sb = document.getElementById("insightSearchBtn");
    if (sb) sb.addEventListener("click", function () { doSearch(); });
    var si = document.getElementById("insightSearchInput");
    if (si) si.addEventListener("keydown", function (e) { if (e.key === "Enter") doSearch(); });

    // Export
    var eb = document.getElementById("insightExportBtn");
    if (eb) eb.addEventListener("click", doExport);
    var ef = document.getElementById("insightExportFormat");
    if (ef) ef.addEventListener("change", doExport);

    // Load overview
    loadSection("overview");
  }

  function getPeriod() {
    var s = document.getElementById("insightPeriod");
    return s ? s.value : "month";
  }

  function loadSection(section) {
    var period = getPeriod();
    var map = {
      overview: loadOverview,
      trending: loadTrending,
      courses: loadCourses,
      colleges: loadColleges,
      services: loadServices,
      knowledge: loadKnowledge,
      gaps: loadGaps,
      conversations: loadConversations,
      queries: loadQueries,
      performance: loadPerformance,
      insights: loadInsights,
    };
    window._insightLoaders = map;
    if (map[section]) map[section](period);
  }

  // ===================================================================
  // 1. OVERVIEW
  // ===================================================================
  function loadOverview() {
    showLoading("overviewContent");
    var html = "";
    fetchJSON(BASE + "/overview?period=all").then(function (data) {
      var periods = ["today","week","month","year"];
      var labels = {today:"Today",week:"This Week",month:"This Month",year:"This Year"};
      html = '<div class="insight-overview-grid">';
      var hasAny = false;
      periods.forEach(function (p) {
        var d = data[p] || {};
        if (d.total_messages > 0) hasAny = true;
        html += '<div class="admin-card insight-period-card">';
        html += '<h3 class="insight-period-title">' + labels[p] + '</h3><div class="kpi">';
        html += '<div class="box"><div class="n">' + (d.total_messages||0) + '</div><div class="l">Messages</div></div>';
        html += '<div class="box"><div class="n">' + (d.unique_sessions||0) + '</div><div class="l">Sessions</div></div>';
        html += '<div class="box"><div class="n">' + (d.avg_response_time_ms||0) + 'ms</div><div class="l">Avg Response</div></div>';
        html += '<div class="box"><div class="n">' + (d.service_requests||0) + '</div><div class="l">Services</div></div>';
        html += '</div><div class="kpi">';
        html += '<div class="box"><div class="n">' + (d.rag_requests||0) + '</div><div class="l">RAG</div></div>';
        html += '<div class="box"><div class="n">' + (d.cache_hit_ratio ? Math.round(d.cache_hit_ratio*100)+"%" : "0%") + '</div><div class="l">Cache</div></div>';
        html += '<div class="box"><div class="n">' + (d.completion_rate ? Math.round(d.completion_rate*100)+"%" : "0%") + '</div><div class="l">Completed</div></div>';
        html += '<div class="box"><div class="n">' + (d.query_corrections||0) + '</div><div class="l">Corrections</div></div>';
        html += '</div></div>';
      });
      html += '</div>';

      if (!hasAny) {
        html += '<div class="admin-card"><div class="insight-empty"><p>No analytics data yet. Analytics will appear once users interact with the chatbot.</p></div></div>';
      }

      // Charts from /charts endpoint
      fetchJSON(BASE + "/charts?period=" + getPeriod()).then(function (chartData) {
        html += renderOverviewCharts(chartData);
        document.getElementById("overviewContent").innerHTML = html;
        renderCharts("overview", chartData);
      }).catch(function () {
        document.getElementById("overviewContent").innerHTML = html;
      });

    }).catch(function (err) {
      html = '<div class="admin-card"><div class="insight-empty"><p>Could not load analytics data. ' + err.message + '</p></div></div>';
      document.getElementById("overviewContent").innerHTML = html;
    });
  }

  function renderOverviewCharts(data) {
    if (!data || !data.daily_conversations || !data.daily_conversations.length) return '';
    return '<div class="charts-row">' +
      '<div class="admin-card chart-card"><h3>Daily Conversations</h3><div class="chart-container"><canvas id="chartDailyLine" height="220"></canvas></div></div>' +
      '<div class="admin-card chart-card"><h3>Hourly Activity</h3><div class="chart-container"><canvas id="chartHourlyBar" height="220"></canvas></div></div>' +
      '</div>' +
      '<div class="charts-row">' +
      '<div class="admin-card chart-card-sm"><h3>Query Sources</h3><div class="chart-container-sm"><canvas id="chartSourcesPie" height="220"></canvas></div></div>' +
      '<div class="admin-card chart-card-sm"><h3>Response Performance</h3><div class="chart-container-sm"><canvas id="chartPerfBar" height="220"></canvas></div></div>' +
      '</div>';
  }

  function renderCharts(prefix, data) {
    if (!data) return;
    // Daily line chart
    var daily = data.daily_conversations || [];
    if (daily.length) {
      destroyChart("chartDailyLine");
      var c1 = document.getElementById("chartDailyLine");
      if (c1) {
        _charts["chartDailyLine"] = new Chart(c1.getContext("2d"), {
          type: "line",
          data: {
            labels: daily.map(function(d){return d.date ? d.date.slice(5) : "";}),
            datasets: [
              { label: "Messages", data: daily.map(function(d){return d.messages||0;}), borderColor: COLORS[0], backgroundColor: COLORS[0]+"22", fill: true, tension: 0.3, pointRadius: 2 },
            ]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, grid: { color: "#eee" } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: { size: 10 } } } }
          }
        });
      }
    }
    // Hourly bar
    var hourly = data.hourly_activity || [];
    if (hourly.length) {
      destroyChart("chartHourlyBar");
      var c2 = document.getElementById("chartHourlyBar");
      if (c2) {
        _charts["chartHourlyBar"] = new Chart(c2.getContext("2d"), {
          type: "bar",
          data: {
            labels: hourly.map(function(d){return d.hour + ":00";}),
            datasets: [{ label: "Messages", data: hourly.map(function(d){return d.count||0;}), backgroundColor: COLORS[4] }]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, grid: { color: "#eee" } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 12, font: { size: 9 } } } }
          }
        });
      }
    }
    // Sources pie
    var sources = data.query_distribution || [];
    if (sources.length) {
      destroyChart("chartSourcesPie");
      var c3 = document.getElementById("chartSourcesPie");
      if (c3) {
        _charts["chartSourcesPie"] = new Chart(c3.getContext("2d"), {
          type: "doughnut",
          data: {
            labels: sources.map(function(s){return s.source;}),
            datasets: [{ data: sources.map(function(s){return s.count;}), backgroundColor: COLORS }]
          },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 10, padding: 6, font: { size: 10 } } } } }
        });
      }
    }
    // Perf bar
    var perf = data.performance || {};
    if (perf.total_samples > 0) {
      destroyChart("chartPerfBar");
      var c4 = document.getElementById("chartPerfBar");
      if (c4) {
        _charts["chartPerfBar"] = new Chart(c4.getContext("2d"), {
          type: "bar",
          data: {
            labels: ["Avg", "P50", "P90", "P99"],
            datasets: [{ label: "ms", data: [perf.avg_response_time_ms||0, perf.p50||0, perf.p90||0, perf.p99||0], backgroundColor: COLORS.slice(0,4) }]
          },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
        });
      }
    }
  }

  // ===================================================================
  // 2. TRENDING
  // ===================================================================
  function loadTrending(period) {
    showLoading("trendingContent");
    fetchJSON(BASE + "/trending?period=" + period + "&limit=20").then(function (data) {
      var html = '<div class="insight-columns">';
      html += '<div class="admin-card"><h3>Topics</h3>' + renderBarList(data.topics, "term", "count") + '</div>';
      html += '<div class="admin-card"><h3>Programmes</h3>' + renderBarList(data.programmes, "term", "count") + '</div>';
      html += '<div class="admin-card"><h3>Colleges</h3>' + renderBarList(data.colleges, "term", "count") + '</div>';
      html += '<div class="admin-card"><h3>Services</h3>' + renderBarList(data.services, "term", "count") + '</div>';
      html += '</div>';
      // Raw queries table
      var qs = data.queries || [];
      if (qs.length) {
        html += '<div class="admin-card"><h3>Top Raw Queries</h3><table class="insight-table"><tr><th>Query</th><th>Count</th></tr>';
        qs.slice(0, 10).forEach(function (q) { html += '<tr><td>' + esc(q.term) + '</td><td>' + q.count + '</td></tr>'; });
        html += '</table></div>';
      }
      // Charts
      html += '<div class="charts-row">';
      html += '<div class="admin-card chart-card"><h3>Top Topics</h3><div class="chart-container"><canvas id="trendTopicsBar" height="200"></canvas></div></div>';
      html += '<div class="admin-card chart-card"><h3>Top Programmes</h3><div class="chart-container"><canvas id="trendProgsBar" height="200"></canvas></div></div>';
      html += '</div>';
      document.getElementById("trendingContent").innerHTML = html;
      // Charts
      renderTrendCharts(data);
    }).catch(function (err) {
      emptyState("trendingContent", "Could not load trending data: " + err.message);
    });
  }

  function renderTrendCharts(data) {
    var topics = data.topics || [];
    if (topics.length) {
      destroyChart("trendTopicsBar");
      var c = document.getElementById("trendTopicsBar");
      if (c) {
        _charts["trendTopicsBar"] = new Chart(c.getContext("2d"), {
          type: "bar",
          data: {
            labels: topics.slice(0, 10).map(function(t){return fmt(t.term);}),
            datasets: [{ label: "Count", data: topics.slice(0, 10).map(function(t){return t.count;}), backgroundColor: COLORS[3] }]
          },
          options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } }
        });
      }
    }
    var progs = data.programmes || [];
    if (progs.length) {
      destroyChart("trendProgsBar");
      var c = document.getElementById("trendProgsBar");
      if (c) {
        _charts["trendProgsBar"] = new Chart(c.getContext("2d"), {
          type: "bar",
          data: {
            labels: progs.slice(0, 10).map(function(p){return p.term.toUpperCase();}),
            datasets: [{ label: "Count", data: progs.slice(0, 10).map(function(p){return p.count;}), backgroundColor: COLORS[0] }]
          },
          options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } }
        });
      }
    }
  }

  // ===================================================================
  // 3. COURSES
  // ===================================================================
  function loadCourses(period) {
    showLoading("coursesContent");
    fetchJSON(BASE + "/courses?period=" + period).then(function (data) {
      var html = '<div class="insight-columns">';
      var pms = data.most_searched_programmes || [];
      html += '<div class="admin-card"><h3>Most Searched Programmes</h3>' + renderBarList(pms, "id", "count") + '</div>';
      var tbs = data.topic_breakdown || [];
      html += '<div class="admin-card"><h3>Topic Breakdown</h3>' + renderBarList(tbs, "topic", "count") + '</div>';
      html += '</div>';
      if (pms.length) {
        html += '<div class="admin-card"><h3>Programme Distribution</h3><div class="chart-container-sm"><canvas id="coursePie" height="250"></canvas></div></div>';
      }
      document.getElementById("coursesContent").innerHTML = html;
      if (pms.length) renderPie("coursePie", pms.slice(0, 8), "id", "count");
    }).catch(function (err) {
      emptyState("coursesContent", "Could not load course analytics: " + err.message);
    });
  }

  // ===================================================================
  // 4. COLLEGES
  // ===================================================================
  function loadColleges(period) {
    showLoading("collegesContent");
    fetchJSON(BASE + "/colleges?period=" + period).then(function (data) {
      var colleges = data.colleges || [];
      var html = '<div class="admin-card"><h3>College Query Volume</h3>';
      if (colleges.length) {
        html += renderBarList(colleges, "college", "queries");
        html += '</div><div class="admin-card"><h3>Top Colleges</h3><div class="chart-container-sm"><canvas id="collegeChart" height="250"></canvas></div></div>';
      } else {
        html += '<div class="insight-empty"><p>No college data yet.</p></div></div>';
      }
      document.getElementById("collegesContent").innerHTML = html;
      if (colleges.length) renderBar("collegeChart", colleges.slice(0, 10), "college", "queries", "Queries");
    }).catch(function (err) {
      emptyState("collegesContent", "Could not load college analytics: " + err.message);
    });
  }

  // ===================================================================
  // 5. SERVICES
  // ===================================================================
  function loadServices(period) {
    showLoading("servicesContent");
    fetchJSON(BASE + "/services?period=" + period).then(function (data) {
      var svcs = data.services || [];
      var html = '<div class="admin-card"><h3>Service Usage</h3>';
      if (svcs.length) {
        html += '<table class="insight-table"><tr><th>Service</th><th>Requests</th><th>Success Rate</th><th>Avg Time</th></tr>';
        svcs.forEach(function (s) { html += '<tr><td>' + esc(fmt(s.service)) + '</td><td>' + s.count + '</td><td>' + (s.success_rate ? Math.round(s.success_rate*100)+"%" : "–") + '</td><td>' + (s.avg_response_time_ms ? s.avg_response_time_ms+"ms" : "–") + '</td></tr>'; });
        html += '</table></div>';
        html += '<div class="admin-card"><div class="chart-container-sm"><canvas id="servicesChart" height="250"></canvas></div></div>';
      } else {
        html += '<div class="insight-empty"><p>No service usage data yet.</p></div></div>';
      }
      document.getElementById("servicesContent").innerHTML = html;
      if (svcs.length) renderBar("servicesChart", svcs.slice(0, 10), "service", "count", "Requests");
    }).catch(function (err) {
      emptyState("servicesContent", "Could not load service analytics: " + err.message);
    });
  }

  // ===================================================================
  // 6. KNOWLEDGE
  // ===================================================================
  function loadKnowledge() {
    showLoading("knowledgeContent");
    fetchJSON(BASE + "/knowledge").then(function (data) {
      var html = '<div class="kpi">';
      html += '<div class="box"><div class="n">' + (data.total_rag_uses||0) + '</div><div class="l">RAG Requests</div></div>';
      html += '<div class="box"><div class="n">' + (data.knowledge_sync_references||0) + '</div><div class="l">Sync References</div></div>';
      html += '</div>';
      // KB stats
      fetchJSON(BASE + "/kb-stats").then(function (kb) {
        html += '<div class="kpi">';
        html += '<div class="box"><div class="n">' + (kb.documents||0) + '</div><div class="l">Documents</div></div>';
        html += '<div class="box"><div class="n">' + (kb.chunks||0) + '</div><div class="l">Chunks</div></div>';
        html += '<div class="box"><div class="n">' + (kb.vectors||0) + '</div><div class="l">Vectors</div></div>';
        html += '</div>';
        document.getElementById("knowledgeContent").innerHTML = html;
      }).catch(function () {
        document.getElementById("knowledgeContent").innerHTML = html;
      });
    }).catch(function (err) {
      emptyState("knowledgeContent", "Could not load knowledge data: " + err.message);
    });
  }

  // ===================================================================
  // 7. GAPS
  // ===================================================================
  function loadGaps() {
    showLoading("gapsContent");
    fetchJSON(BASE + "/knowledge-gaps?limit=50").then(function (data) {
      var gaps = data || [];
      var html = '';
      if (!gaps.length) {
        html = '<div class="admin-card"><div class="insight-empty"><p>No knowledge gaps detected yet. Gaps are identified when the chatbot cannot answer a question.</p></div></div>';
      } else {
        html = '<div class="admin-card"><h3>Unresolved Knowledge Gaps</h3>';
        html += '<table class="insight-table"><tr><th>Type</th><th>Query</th><th>Freq</th><th>Confidence</th><th>Suggestion</th><th>Action</th></tr>';
        gaps.forEach(function (g) {
          html += '<tr><td>' + esc(g.gap_type) + '</td><td>' + esc(g.query_text||"") + '</td><td>' + g.frequency + '</td><td>' + (g.confidence_score != null ? Math.round(g.confidence_score*100)+"%" : "–") + '</td><td>' + esc(g.suggestion||"") + '</td>';
          html += '<td><button class="btn sm green resolve-gap" data-gap-id="' + esc(g.id) + '">Resolve</button></td></tr>';
        });
        html += '</table></div>';
      }
      document.getElementById("gapsContent").innerHTML = html;
      document.querySelectorAll(".resolve-gap").forEach(function (btn) {
        btn.addEventListener("click", function () { window.resolveGap(btn.getAttribute("data-gap-id")); });
      });
    }).catch(function (err) {
      emptyState("gapsContent", "Could not load knowledge gaps: " + err.message);
    });
  }

  window.resolveGap = function (gapId) {
    fetchJSON(BASE + "/knowledge-gaps/" + gapId + "/resolve").then(function () { loadGaps(); }).catch(function () {});
  };

  // ===================================================================
  // 8. CONVERSATIONS
  // ===================================================================
  function loadConversations(period) {
    showLoading("conversationsContent");
    fetchJSON(BASE + "/conversations?period=" + period).then(function (data) {
      var html = '<div class="kpi">';
      html += '<div class="box"><div class="n">' + (data.total_conversations||0) + '</div><div class="l">Conversations</div></div>';
      html += '<div class="box"><div class="n">' + (data.avg_depth||0) + '</div><div class="l">Avg Depth</div></div>';
      html += '<div class="box"><div class="n">' + (data.completion_rate ? Math.round(data.completion_rate*100)+"%" : "0%") + '</div><div class="l">Completed</div></div>';
      html += '<div class="box"><div class="n">' + (data.total_clarifications||0) + '</div><div class="l">Clarifications</div></div>';
      html += '<div class="box"><div class="n">' + (data.restarts||0) + '</div><div class="l">Restarts</div></div>';
      html += '<div class="box"><div class="n">' + (data.abandoned||0) + '</div><div class="l">Abandoned</div></div>';
      html += '</div>';
      html += '<div class="admin-card"><div class="chart-container-sm"><canvas id="convChart" height="200"></canvas></div></div>';
      document.getElementById("conversationsContent").innerHTML = html;
      renderPie("convChart", [
        {label:"Completed", val:data.completed||0},
        {label:"Abandoned", val:data.abandoned||0},
      ], "label", "val");
    }).catch(function (err) {
      emptyState("conversationsContent", "Could not load conversation data: " + err.message);
    });
  }

  // ===================================================================
  // 9. QUERIES
  // ===================================================================
  function loadQueries(period) {
    showLoading("queriesContent");
    fetchJSON(BASE + "/queries?period=" + period + "&limit=50").then(function (data) {
      var html = '<div class="kpi">';
      html += '<div class="box"><div class="n">' + (data.total_queries||0) + '</div><div class="l">Total Queries</div></div>';
      html += '<div class="box"><div class="n">' + (data.corrected_queries||0) + '</div><div class="l">Corrected</div></div>';
      html += '<div class="box"><div class="n">' + (data.correction_rate ? Math.round(data.correction_rate*100)+"%" : "0%") + '</div><div class="l">Correction Rate</div></div>';
      html += '</div>';
      var corrs = data.common_corrections || [];
      if (corrs.length) {
        html += '<div class="admin-card"><h3>Common Corrections</h3><table class="insight-table"><tr><th>Original</th><th>Count</th></tr>';
        corrs.forEach(function (c) { html += '<tr><td>' + esc(c.original) + '</td><td>' + c.count + '</td></tr>'; });
        html += '</table></div>';
      } else {
        html += '<div class="admin-card"><div class="insight-empty"><p>No query corrections recorded yet.</p></div></div>';
      }
      document.getElementById("queriesContent").innerHTML = html;
    }).catch(function (err) {
      emptyState("queriesContent", "Could not load query data: " + err.message);
    });
  }

  // ===================================================================
  // 10. PERFORMANCE
  // ===================================================================
  function loadPerformance(period) {
    showLoading("performanceContent");
    fetchJSON(BASE + "/performance?period=" + period).then(function (data) {
      var html = '<div class="kpi">';
      html += '<div class="box"><div class="n">' + (data.avg_response_time_ms||0) + 'ms</div><div class="l">Avg Response</div></div>';
      html += '<div class="box"><div class="n">' + (data.p50||0) + 'ms</div><div class="l">P50</div></div>';
      html += '<div class="box"><div class="n">' + (data.p90||0) + 'ms</div><div class="l">P90</div></div>';
      html += '<div class="box"><div class="n">' + (data.p99||0) + 'ms</div><div class="l">P99</div></div>';
      html += '</div><div class="kpi">';
      html += '<div class="box"><div class="n">' + (data.avg_planner_latency_ms||0) + 'ms</div><div class="l">Planner</div></div>';
      html += '<div class="box"><div class="n">' + (data.avg_rag_latency_ms||0) + 'ms</div><div class="l">RAG</div></div>';
      html += '<div class="box"><div class="n">' + (data.avg_llm_latency_ms||0) + 'ms</div><div class="l">LLM</div></div>';
      html += '<div class="box"><div class="n">' + (data.cache_hit_ratio ? Math.round(data.cache_hit_ratio*100)+"%" : "0%") + '</div><div class="l">Cache</div></div>';
      html += '</div>';
      html += '<div class="admin-card"><div class="chart-container-sm"><canvas id="perfChart" height="200"></canvas></div></div>';
      document.getElementById("performanceContent").innerHTML = html;
      renderPerfChart(data);
    }).catch(function (err) {
      emptyState("performanceContent", "Could not load performance data: " + err.message);
    });
  }

  function renderPerfChart(data) {
    destroyChart("perfChart");
    var c = document.getElementById("perfChart");
    if (!c) return;
    _charts["perfChart"] = new Chart(c.getContext("2d"), {
      type: "bar",
      data: {
        labels: ["Response", "Planner", "RAG", "LLM"],
        datasets: [{ label: "Avg Latency (ms)", data: [data.avg_response_time_ms||0, data.avg_planner_latency_ms||0, data.avg_rag_latency_ms||0, data.avg_llm_latency_ms||0], backgroundColor: COLORS.slice(0,4) }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
    });
  }

  // ===================================================================
  // 11. SMART AI INSIGHTS
  // ===================================================================
  function loadInsights() {
    showLoading("insightsContent");
    fetchJSON(BASE + "/insights").then(function (data) {
      var insights = data || [];
      var html = '';
      if (!insights.length) {
        html = '<div class="admin-card"><div class="insight-empty"><p>No insights yet. Insights are generated after sufficient data collection.</p></div></div>';
      } else {
        html = '<div class="insight-insights">';
        insights.forEach(function (ins) {
          var sc = "insight-severity-" + (ins.severity || "info");
          var icon = ins.type === "warning" ? "⚠️" : ins.type === "anomaly" ? "🔴" : ins.type === "trend" ? "📈" : "💡";
          html += '<div class="insight-card ' + sc + '">';
          html += '<div class="insight-icon">' + icon + '</div>';
          html += '<div class="insight-body">';
          html += '<div class="insight-message">' + esc(ins.message) + '</div>';
          html += '<div class="insight-meta">' + esc(ins.type) + ' · ' + esc(ins.severity) + (ins.change_pct != null ? " · " + ins.change_pct + "% change" : "") + '</div>';
          html += '</div></div>';
        });
        html += '</div>';
      }
      document.getElementById("insightsContent").innerHTML = html;
    }).catch(function (err) {
      emptyState("insightsContent", "Could not load AI insights: " + err.message);
    });
  }

  // ===================================================================
  // SEARCH
  // ===================================================================
  function doSearch() {
    var q = document.getElementById("insightSearchInput");
    if (!q) return;
    var query = q.value.trim();
    if (!query) return;
    showLoading("searchResults");
    fetchJSON(BASE + "/search?q=" + encodeURIComponent(query) + "&period=" + getPeriod() + "&limit=30").then(function (data) {
      var results = data.results || [];
      var html = '<div class="admin-card"><h3>Search Results for "' + esc(query) + '"</h3>';
      html += '<p class="sub">' + (data.total_matches||0) + ' total matches</p>';
      if (results.length) {
        html += '<table class="insight-table"><tr><th>Programme</th><th>College</th><th>Topic</th><th>Service</th><th>Matches</th></tr>';
        results.forEach(function (r) { html += '<tr><td>' + esc(r.programme||"") + '</td><td>' + esc(r.college||"") + '</td><td>' + esc(r.topic||"") + '</td><td>' + esc(r.service||"") + '</td><td>' + (r.count||1) + '</td></tr>'; });
        html += '</table>';
      } else {
        html += '<div class="insight-empty"><p>No matching data found for "' + esc(query) + '".</p></div>';
      }
      html += '</div>';
      document.getElementById("searchResults").innerHTML = html;
    }).catch(function (err) {
      emptyState("searchResults", "Search failed: " + err.message);
    });
  }

  // ===================================================================
  // EXPORT
  // ===================================================================
  function doExport() {
    var fmt = document.getElementById("insightExportFormat");
    var active = document.querySelector(".insight-nav-btn.active");
    if (!fmt || !active) return;
    var url = BASE + "/export/" + fmt.value + "?report=" + active.dataset.section + "&period=" + getPeriod();
    log("Export: " + url);
    window.open(url, "_blank");
  }

  // ===================================================================
  // RENDER HELPERS
  // ===================================================================
  function renderBarList(items, key, val) {
    if (!items || !items.length) return '<div class="insight-empty-sm"><p>No data</p></div>';
    var max = Math.max.apply(null, items.map(function(i){return i[val]||0;}));
    var html = '<div class="insight-bar-list">';
    items.slice(0, 15).forEach(function (item) {
      var pct = max > 0 ? Math.round((item[val]||0)/max*100) : 0;
      html += '<div class="insight-bar-item"><div class="insight-bar-label">' + esc(fmt(item[key])) + '</div>';
      html += '<div class="insight-bar-track"><div class="insight-bar-fill" style="width:' + pct + '%"></div></div>';
      html += '<div class="insight-bar-count">' + item[val] + '</div></div>';
    });
    html += '</div>';
    return html;
  }

  function renderPie(canvasId, items, labelKey, valKey) {
    destroyChart(canvasId);
    var canvas = document.getElementById(canvasId);
    if (!canvas || !items || !items.length) return;
    _charts[canvasId] = new Chart(canvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: items.map(function(i){return fmt(i[labelKey]);}),
        datasets: [{ data: items.map(function(i){return i[valKey]||0;}), backgroundColor: COLORS }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 12, padding: 8, font: { size: 11 } } } } }
    });
  }

  function renderBar(canvasId, items, labelKey, valKey, label) {
    destroyChart(canvasId);
    var canvas = document.getElementById(canvasId);
    if (!canvas || !items || !items.length) return;
    _charts[canvasId] = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: items.map(function(i){return fmt(i[labelKey]);}),
        datasets: [{ label: label, data: items.map(function(i){return i[valKey]||0;}), backgroundColor: COLORS }]
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, grid: { color: "#eee" } }, y: { grid: { display: false } } }
      }
    });
  }

  function fmt(s) { if (!s) return ""; return s.replace(/_/g, " ").replace(/\b\w/g, function(c){return c.toUpperCase();}); }
  function esc(s) { if (typeof s !== "string") return String(s||""); return s.replace(/[&<>"']/g, function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];}); }

  // ========== Expose ==========
  window.CUS = window.CUS || {};
  window.CUS.insightsInit = init;
  window.CUS.insightsRefresh = loadSection;
  log("Module loaded");
})();
