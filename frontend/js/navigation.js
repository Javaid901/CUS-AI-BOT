/* ============================================================================
   navigation.js — CUS AI
   Injects shared navbar + footer, handles mobile menu, scroll reveal,
   active link, modals, FAQ accordion. Also exposes shared college data.
   ============================================================================ */
window.CUS = window.CUS || {};

/* Constituent colleges — used by home + colleges pages for cards & modals */
window.CUS.COLLEGES = {
  sp: {
    name: "S.P. College",
    desc: "One of the oldest and most prestigious constituent colleges, offering a rich blend of sciences, humanities and commerce with a strong research culture.",
    depts: ["English", "History", "Economics", "Commerce", "Political Science"],
  },
  bemina: {
    name: "GDC Bemina",
    desc: "Known for its science and humanities streams, modern laboratories and active student societies fostering all-round development.",
    depts: ["Physics", "Chemistry", "Botany", "Zoology", "Mathematics"],
  },
  anantnag: {
    name: "GDC Anantnag",
    desc: "A South Kashmir campus committed to academic excellence and community engagement across disciplines.",
    depts: ["Geography", "Chemistry", "Commerce", "Education"],
  },
  pulwama: {
    name: "GDC Pulwama",
    desc: "An emerging centre for commerce, computer applications and professional studies serving the Pulwama region.",
    depts: ["Commerce", "Computer Applications", "Management", "Science"],
  },
  kulgam: {
    name: "GDC Kulgam",
    desc: "Community-engaged academic programmes with a strong focus on inclusive and equitable higher education.",
    depts: ["Education", "Islamic Studies", "Science", "Commerce"],
  },
};

window.CUS.NAV = [
  { href: "index.html", label: "Home" },
  { href: "about.html", label: "About" },
  { href: "admissions.html", label: "Admissions" },
  { href: "colleges.html", label: "Colleges" },
  { href: "contact.html", label: "Contact" },
];

window.CUS.injectChrome = function () {
  var page = (location.pathname.split("/").pop() || "index.html").split("?")[0];

  // Navbar
  var header = document.getElementById("site-header");
  if (header) {
    var links = window.CUS.NAV.map(function (n) {
      return '<a href="' + n.href + '"' + (n.href === page ? ' class="active"' : "") + ">" + n.label + "</a>";
    }).join("");
    header.innerHTML =
      '<div class="container nav">' +
        '<a class="brand" href="index.html">' +
          '<span class="logo"><svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M12 3 1 8l11 5 9-4.09V17h2V8L12 3z"/><path d="M5 13.18v4L12 21l7-3.82v-4L12 17l-7-3.82z"/></svg></span>' +
          '<span class="name">Cluster University<small>Srinagar · Est. 2016</small></span>' +
        "</a>" +
        '<nav class="nav-links">' + links + "</nav>" +
        '<a class="btn gold nav-cta" href="#" data-open-chat>Ask CUS AI</a>' +
        '<button class="hamburger" aria-label="Menu"><span></span><span></span><span></span></button>' +
      "</div>";
  }

  // Footer
  var footer = document.getElementById("site-footer");
  if (footer) {
    footer.className = "site-footer";
    footer.innerHTML =
      '<div class="top">' +
        "<div>" +
          '<div class="brand" style="color:#fff;margin-bottom:14px;"><span class="logo"><svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M12 3 1 8l11 5 9-4.09V17h2V8L12 3z"/><path d="M5 13.18v4L12 21l7-3.82v-4L12 17l-7-3.82z"/></svg></span><span style="font-weight:700;font-family:var(--display);">Cluster University of Srinagar</span></div>' +
          '<p style="color:#9fb2c2;max-width:34ch;margin:0;">Gogji-Bagh, Srinagar, Jammu & Kashmir, India 190008. A premier state university committed to academic excellence.</p>' +
        "</div>" +
        '<div><h4>Quick Links</h4><a href="index.html">Home</a><a href="about.html">About</a><a href="admissions.html">Admissions</a><a href="colleges.html">Colleges</a><a href="contact.html">Contact</a></div>' +
        '<div><h4>Academics</h4><a href="admissions.html">UG Admissions</a><a href="admissions.html">PG Admissions 2026</a><a href="about.html">Departments</a><a href="about.html">Research</a></div>' +
        '<div><h4>Connect</h4><a href="contact.html">HelpDesk</a><a href="contact.html">Grievance</a><a href="#" data-open-chat>AI Assistant</a><a href="authority-admin.html">Authority Portal</a></div>' +
      "</div>" +
      '<div class="copy">© 2016–2026 Cluster University of Srinagar · CUS AI Knowledge Assistant</div>';
  }
};

document.addEventListener("DOMContentLoaded", function () {
  window.CUS.injectChrome();

  // Mobile menu
  var burger = document.querySelector(".hamburger");
  var links = document.querySelector(".nav-links");
  if (burger && links) {
    burger.addEventListener("click", function () {
      var open = links.classList.toggle("open");
      burger.classList.toggle("open", open);
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () { links.classList.remove("open"); burger.classList.remove("open"); });
    });
  }
  if (burger) burger.setAttribute("aria-label", "Toggle menu");

  // Sticky header shadow on scroll
  var header = document.querySelector(".site-header");
  function onScroll() { if (header) header.classList.toggle("scrolled", window.scrollY > 8); }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Open-chat triggers
  document.querySelectorAll("[data-open-chat]").forEach(function (el) {
    el.addEventListener("click", function (e) {
      e.preventDefault();
      if (window.CUS && window.CUS.openChat) window.CUS.openChat();
    });
  });

  // Scroll reveal (with subtle stagger for grid children)
  var els = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          var sib = en.target.parentElement ? Array.prototype.indexOf.call(en.target.parentElement.children, en.target) : 0;
          if (sib > 0 && sib < 5) en.target.style.transitionDelay = (sib * 0.07) + "s";
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.12 });
    els.forEach(function (el) { io.observe(el); });
  } else {
    els.forEach(function (el) { el.classList.add("in"); });
  }

  // College modals
  document.querySelectorAll("[data-college]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      var c = window.CUS.COLLEGES[btn.getAttribute("data-college")];
      if (!c) return;
      var bg = document.getElementById("collegeModal");
      bg.querySelector(".m-name").textContent = c.name;
      bg.querySelector(".m-desc").textContent = c.desc;
      var d = bg.querySelector(".depts"); d.innerHTML = "";
      (c.depts || []).forEach(function (x) { var s = document.createElement("span"); s.textContent = x; d.appendChild(s); });
      bg.classList.add("open");
    });
  });
  document.querySelectorAll(".modal-bg").forEach(function (bg) {
    bg.addEventListener("click", function (e) { if (e.target === bg || e.target.classList.contains("close")) bg.classList.remove("open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.querySelectorAll(".modal-bg.open").forEach(function (m) { m.classList.remove("open"); });
  });

  // FAQ accordion
  document.querySelectorAll(".acc-item").forEach(function (item) {
    var head = item.querySelector(".acc-head");
    if (head) head.addEventListener("click", function () { item.classList.toggle("open"); });
  });
});

/* Shared modal markup (injected once) */
(function () {
  if (document.getElementById("collegeModal")) return;
  var m = document.createElement("div");
  m.className = "modal-bg"; m.id = "collegeModal";
  m.innerHTML =
    '<div class="modal">' +
      '<h3><span class="m-name"></span> <button class="close" aria-label="Close">×</button></h3>' +
      '<p class="m-desc"></p>' +
      '<div class="depts"></div>' +
      '<a class="btn green block" href="contact.html">Contact College Office</a>' +
    "</div>";
  document.body.appendChild(m);
})();
