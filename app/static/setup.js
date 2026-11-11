// The setup page: the four settings and the three reports, before any other page can be used.
// Each report file is checked and installed the moment it is chosen; the button saves the
// settings and, when nothing is missing any more, opens the dashboard.

var KEYS = ["MODEL_SERVER", "API_URL", "API_MODEL", "API_KEY", "GATEWAY_API_URL", "GATEWAY_API_MODEL",
  "GATEWAY_API_KEY", "MISTRAL_API_KEY", "USER_NAME"];
var SETTING_NAMES = {
  API_URL: "adressen til modellserveren",
  API_MODEL: "modellen",
  API_KEY: "nøkkelen til modellserveren",
  MISTRAL_API_KEY: "nøkkelen til Mistral OCR",
  GATEWAY_API_URL: "adressen til ApiManager",
  GATEWAY_API_MODEL: "modellen hos ApiManager",
  GATEWAY_API_KEY: "nøkkelen fra ApiManager",
  USER_NAME: "navnet",
};

var errorBanner = document.getElementById("setup-error");
var reportRows = document.getElementById("report-rows");
var finishButton = document.getElementById("finish-button");
var messageLine = document.getElementById("message");
var reports = [];

document.getElementById("setup-mark").innerHTML = BRAND_MARK;

function fieldFor(key) {
  return document.getElementById(key);
}

// the block of the server that is not chosen is dimmed, so the eye goes to the one in use
function markChosenServer() {
  var chosen = fieldFor("MODEL_SERVER").value;
  document.getElementById("server-maggie").classList.toggle("unchosen", chosen !== "maggie");
  document.getElementById("server-gateway").classList.toggle("unchosen", chosen !== "gateway");
}

fieldFor("MODEL_SERVER").addEventListener("change", markChosenServer);

function showError(text) {
  errorBanner.textContent = text;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
}

function renderSettings(state) {
  for (var i = 0; i < state.settings.length; i++) {
    var setting = state.settings[i];
    var input = fieldFor(setting.key);
    if (setting.secret) {
      // a secret never comes to the page; a blank field keeps the stored one
      input.value = "";
      var hint = document.getElementById(setting.key + "-hint");
      if (setting.is_set) {
        input.placeholder = setting.masked;
        hint.textContent = "Lagret nøkkel slutter på " + setting.masked.slice(-4) + ". La feltet stå tomt for å beholde den.";
      } else {
        input.placeholder = "";
        hint.textContent = "Ingen nøkkel er lagret.";
      }
    } else {
      input.value = setting.value || "";
    }
  }
}

function reportIsReady(report) {
  return !report.error && report.rows !== null;
}

function renderReports() {
  var parts = [];
  for (var i = 0; i < reports.length; i++) {
    var report = reports[i];
    var status;
    if (reportIsReady(report)) {
      status = '<span class="report-status ok">' + formatNumber(report.rows) + " rader, sist oppdatert "
        + esc(formatDateTime(report.modified)) + "</span>";
    } else if (report.modified === null) {
      // no file at all; the loader's "no such file" text would only show a long path
      status = '<span class="report-status missing">Mangler</span>';
    } else {
      status = '<span class="report-status missing">Filen kan ikke leses: ' + esc(report.error) + "</span>";
    }
    parts.push('<div class="report-row">'
      + '<div><div class="report-title">' + esc(report.title) + "</div>"
      + '<div class="small">Kolonner: ' + esc(report.columns.join(", ")) + "</div>"
      + status + "</div>"
      + '<label class="button button-secondary">Velg fil'
      + '<input type="file" accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv" data-report="' + escAttr(report.id) + '" hidden></label>'
      + "</div>");
  }
  reportRows.innerHTML = parts.join("");
}

async function loadReports() {
  var data = await fetchJson("/knowledge/reports");
  reports = data.reports;
  renderReports();
}

async function load() {
  try {
    var state = await fetchJson("/settings/state");
    renderSettings(state);
    markChosenServer();
    await loadReports();
    clearError();
  } catch (error) {
    showError(error.message);
  }
}

// a chosen file goes straight to the server, which checks the columns and installs it
reportRows.addEventListener("change", async function (event) {
  var input = event.target;
  if (input.type !== "file" || input.files.length === 0) {
    return;
  }
  var file = input.files[0];
  var reportId = input.dataset.report;
  clearError();
  try {
    var data = await fetchJson("/knowledge/upload?report=" + encodeURIComponent(reportId)
      + "&filename=" + encodeURIComponent(file.name), { method: "POST", body: file });
    messageLine.textContent = data.message;
    messageLine.hidden = false;
  } catch (error) {
    showError(error.message);
  }
  await loadReports();
});

finishButton.addEventListener("click", async function () {
  var values = {};
  for (var i = 0; i < KEYS.length; i++) {
    values[KEYS[i]] = fieldFor(KEYS[i]).value;
  }
  clearError();
  finishButton.disabled = true;
  try {
    await postJson("/settings/save", { values: values });
    var state = await fetchJson("/status");
    if (!state.setup_needed) {
      window.location.href = "/";
      return;
    }
    var missing = [];
    for (var m = 0; m < state.settings_missing.length; m++) {
      missing.push(SETTING_NAMES[state.settings_missing[m]] || state.settings_missing[m]);
    }
    for (var r = 0; r < reports.length; r++) {
      if (!reportIsReady(reports[r])) {
        missing.push(reports[r].title);
      }
    }
    showError("Mangler fortsatt: " + missing.join(", ") + ".");
    await loadReports();
  } catch (error) {
    showError(error.message);
  }
  finishButton.disabled = false;
});

load();
