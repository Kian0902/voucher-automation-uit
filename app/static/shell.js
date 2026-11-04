// Shared by every page: the sidebar, the helpers and the one poll loop on /status.
// A page script registers its renderer with onStatus() instead of polling on its own.

var PAGES = [
  { path: "/", title: "Dashbord" },
  { path: "/agents", title: "Agenter" },
  { path: "/knowledge", title: "Kunnskapsbase" },
  { path: "/settings", title: "Innstillinger" },
];
var POLL_INTERVAL_MS = 500;
var statusListeners = [];

function esc(value) {
  var div = document.createElement("div");
  if (value === null || value === undefined) {
    div.textContent = "";
  } else {
    div.textContent = String(value);
  }
  return div.innerHTML;
}

// GET or POST JSON; a non-2xx answer becomes an Error carrying the server's message
async function fetchJson(url, options) {
  var response = await fetch(url, options);
  var data = await response.json();
  if (!response.ok) {
    throw new Error(data.message || "Forespørselen feilet");
  }
  return data;
}

function postJson(url, payload) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function formatNumber(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  // thousands separated by a space, as in Norwegian
  return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

// dd.mm.yyyy
function formatDate(isoText) {
  if (!isoText) {
    return "-";
  }
  var date = new Date(isoText);
  var day = String(date.getDate()).padStart(2, "0");
  var month = String(date.getMonth() + 1).padStart(2, "0");
  return day + "." + month + "." + date.getFullYear();
}

function renderSidebar() {
  var sidebar = document.getElementById("sidebar");
  var currentPage = document.body.dataset.page;
  var parts = ['<div class="brand">Fakturaassistent</div>'];
  for (var i = 0; i < PAGES.length; i++) {
    var page = PAGES[i];
    var className = "nav-link";
    if (page.path === currentPage) {
      className = className + " active";
    }
    var dot = "";
    if (page.path === "/agents") {
      dot = '<span class="run-dot" id="run-dot"></span>';
    }
    parts.push('<a class="' + className + '" href="' + page.path + '">' + esc(page.title) + dot + "</a>");
  }
  sidebar.innerHTML = parts.join("");
}

function onStatus(listener) {
  statusListeners.push(listener);
}

async function pollStatus() {
  try {
    var response = await fetch("/status");
    var state = await response.json();
    var runDot = document.getElementById("run-dot");
    if (runDot !== null) {
      runDot.classList.toggle("visible", state.status === "running");
    }
    for (var i = 0; i < statusListeners.length; i++) {
      statusListeners[i](state);
    }
  } catch (error) {
    // serveren svarer ikke akkurat naa, proever igjen
  }
}

renderSidebar();
setInterval(pollStatus, POLL_INTERVAL_MS);
pollStatus();
