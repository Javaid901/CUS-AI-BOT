(function () {
  "use strict";

  var DEFAULT_PORT = 8001;
  var STORAGE_KEY = "cus_backend_url";

  function detectBaseUrl() {
    // 1. Check localStorage override
    try {
      var stored = localStorage.getItem(STORAGE_KEY);
      if (stored) return stored.replace(/\/+$/, "");
    } catch (_) {}

    // 2. Check <meta name="cus-backend-url">
    var meta = document.querySelector('meta[name="cus-backend-url"]');
    if (meta) return meta.getAttribute("content").replace(/\/+$/, "");

    // 3. If frontend is served from the same host:port as the backend (e.g. via uvicorn static files), use origin
    //    Otherwise default to localhost:DEFAULT_PORT
    var host = window.location.hostname || "localhost";
    return "http://" + host + ":" + DEFAULT_PORT;
  }

  window.CUS_API_BASE = detectBaseUrl();
})();
