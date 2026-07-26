/* ============================================================================
   main.js — CUS AI page-level behavior
   - renders college cards from shared data
   - contact form handling (demo)
   - admissions interactions
   ============================================================================ */
(function () {
  "use strict";

  function renderCollegeCards(grid) {
    if (!grid || !window.CUS || !window.CUS.COLLEGES) return;
    var html = "";
    Object.keys(window.CUS.COLLEGES).forEach(function (k) {
      var c = window.CUS.COLLEGES[k];
      var depts = c.depts.map(function (d) { return "<span>" + d + "</span>"; }).join("");
      html +=
        '<div class="college-card reveal">' +
          '<div class="ph"><div class="em">🏛️</div></div>' +
          '<div class="pad">' +
            "<h3>" + c.name + "</h3>" +
            "<p>" + c.desc + "</p>" +
            '<div class="depts">' + depts + "</div>" +
            '<a class="btn light" href="#" data-college="' + k + '">Learn More</a>' +
          "</div>" +
        "</div>";
    });
    grid.innerHTML = html;
    // Wire modal buttons added after DOMContentLoaded in navigation.js
    grid.querySelectorAll("[data-college]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var c = window.CUS.COLLEGES[btn.getAttribute("data-college")];
        var bg = document.getElementById("collegeModal");
        if (!c || !bg) return;
        bg.querySelector(".m-name").textContent = c.name;
        bg.querySelector(".m-desc").textContent = c.desc;
        var d = bg.querySelector(".depts"); d.innerHTML = "";
        (c.depts || []).forEach(function (x) { var s = document.createElement("span"); s.textContent = x; d.appendChild(s); });
        bg.classList.add("open");
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-college-grid]").forEach(renderCollegeCards);

    // Contact form (demo only — no backend submit wired)
    var cf = document.getElementById("contactForm");
    if (cf) {
      cf.addEventListener("submit", function (e) {
        e.preventDefault();
        var btn = cf.querySelector("button[type=submit]");
        btn.disabled = true; btn.textContent = "Sending…";
        setTimeout(function () {
          cf.innerHTML = '<div class="quote-card" style="text-align:center;"><div class="q" style="margin:0;">✅ Thank you! Your message has been recorded. For instant answers, try the CUS AI Assistant.</div></div>';
        }, 900);
      });
    }

    // Admissions: eligibility/program tabs
    var tabs = document.querySelectorAll("[data-tab]");
    var panes = document.querySelectorAll("[data-pane]");
    if (tabs.length) {
      tabs.forEach(function (t) {
        t.addEventListener("click", function () {
          tabs.forEach(function (x) { x.classList.remove("active"); });
          panes.forEach(function (x) { x.style.display = "none"; });
          t.classList.add("active");
          var p = document.querySelector('[data-pane="' + t.getAttribute("data-tab") + '"]');
          if (p) p.style.display = "block";
        });
      });
    }
  });
})();
