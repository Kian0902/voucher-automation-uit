// Shared by every page: the sidebar, the helpers and the one poll loop on /status.
// A page script registers its renderer with onStatus() instead of polling on its own.

var PAGES = [
  { path: "/", title: "Dashbord" },
  { path: "/agents", title: "Agenter" },
  { path: "/batch", title: "Batchbehandling" },
  { path: "/knowledge", title: "Kunnskapsbase" },
  { path: "/settings", title: "Innstillinger" },
];
var POLL_INTERVAL_MS = 500;
var FAILED_POLLS_BEFORE_BANNER = 4;
var statusListeners = [];
var failedPolls = 0;
var appHasQuit = false;

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

// COPY BUTTONS, shared by the agent page and the batch page: one click puts a koststed, an IO
// or a suggestion on the clipboard

var COPIED_FEEDBACK_MS = 1500;
var copiedValue = null;
var copiedUntil = 0;
// inline icons, so the app needs no icon font: two sheets for copy, a check mark for copied
var COPY_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
  + '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"></rect>'
  + '<path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"></path></svg>';
var CHECK_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
  + '<path d="M3 8.5l3 3 7-7"></path></svg>';

function escAttr(value) {
  return esc(value).replace(/"/g, "&quot;");
}

// the rows are re-rendered on every poll, so the "Kopiert" feedback is kept by value, not by element
function copyButton(value) {
  var icon = COPY_ICON;
  var title = "Kopier";
  var className = "copy-button";
  if (value === copiedValue && Date.now() < copiedUntil) {
    icon = CHECK_ICON;
    title = "Kopiert";
    className = className + " copied";
  }
  return '<button type="button" class="' + className + '" data-copy="' + escAttr(value) + '" title="' + title
    + '" aria-label="' + title + '">' + icon + "</button>";
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (error) {
    // older WebKit: a hidden field and the copy command
    var field = document.createElement("textarea");
    field.value = value;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    var done = false;
    try {
      done = document.execCommand("copy");
    } catch (commandError) {
      done = false;
    }
    document.body.removeChild(field);
    return done;
  }
}

document.addEventListener("click", async function (event) {
  var button = event.target.closest("button.copy-button");
  if (button === null) {
    return;
  }
  var value = button.dataset.copy;
  var done = await copyText(value);
  if (!done) {
    button.title = "Kunne ikke kopiere. Marker verdien og kopier manuelt.";
    button.classList.add("copy-failed");
    return;
  }
  copiedValue = value;
  copiedUntil = Date.now() + COPIED_FEEDBACK_MS;
  button.innerHTML = CHECK_ICON;
  button.title = "Kopiert";
  button.classList.add("copied");
  setTimeout(function () {
    if (button.isConnected) {
      button.innerHTML = COPY_ICON;
      button.title = "Kopier";
      button.classList.remove("copied");
    }
  }, COPIED_FEEDBACK_MS);
});

// the badge of a result label, shared by the pages that show results
function badgeFor(label) {
  if (label === "løst") {
    return '<span class="badge badge-ok">Løst</span>';
  }
  if (label === "må bekreftes") {
    return '<span class="badge badge-confirm">Må bekreftes</span>';
  }
  if (label === "flere funnet") {
    return '<span class="badge badge-confirm">Flere funnet</span>';
  }
  if (label === "uløst") {
    return '<span class="badge badge-open">Uløst</span>';
  }
  return "";
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

// a bar across the top for the two cases where the pages cannot work: the app was quit,
// or it does not answer any more
function showTopBar(text) {
  var bar = document.getElementById("top-bar");
  if (bar === null) {
    bar = document.createElement("div");
    bar.id = "top-bar";
    bar.className = "top-bar";
    document.body.appendChild(bar);
  }
  bar.textContent = text;
  bar.hidden = false;
}

function hideTopBar() {
  var bar = document.getElementById("top-bar");
  if (bar !== null) {
    bar.hidden = true;
  }
}

// In a browser the app has no window to close, so the sidebar gets a button that ends it.
function showQuitButton() {
  if (document.getElementById("quit-button") !== null) {
    return;
  }
  var button = document.createElement("button");
  button.id = "quit-button";
  button.type = "button";
  button.className = "quit-button";
  button.textContent = "Avslutt appen";
  button.addEventListener("click", async function () {
    try {
      await fetchJson("/quit", { method: "POST" });
      appHasQuit = true;
      showTopBar("Appen er avsluttet. Du kan lukke denne fanen. Start Fakturaassistent på nytt for å bruke den igjen.");
    } catch (error) {
      showTopBar(error.message);
      setTimeout(hideTopBar, 5000);
    }
  });
  document.getElementById("sidebar").appendChild(button);
}

async function pollStatus() {
  if (appHasQuit) {
    return;
  }
  var state;
  try {
    var response = await fetch("/status");
    state = await response.json();
  } catch (error) {
    // only a failed request counts as lost contact, never an error in a page's own drawing
    failedPolls = failedPolls + 1;
    if (failedPolls === FAILED_POLLS_BEFORE_BANNER) {
      showTopBar("Får ikke kontakt med appen. Start Fakturaassistent på nytt.");
    }
    return;
  }

  if (failedPolls >= FAILED_POLLS_BEFORE_BANNER) {
    hideTopBar();
  }
  failedPolls = 0;
  if (state.can_quit) {
    showQuitButton();
  }
  var runDot = document.getElementById("run-dot");
  if (runDot !== null) {
    runDot.classList.toggle("visible", state.status === "running");
  }
  for (var i = 0; i < statusListeners.length; i++) {
    try {
      statusListeners[i](state);
    } catch (error) {
      console.error(error);
    }
  }
}

renderSidebar();
setInterval(pollStatus, POLL_INTERVAL_MS);
pollStatus();
