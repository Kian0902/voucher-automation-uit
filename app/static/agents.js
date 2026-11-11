// The agent page: the drop zone starts a run, the status poll from shell.js renders it,
// and the search panel answers manual lookups.

var dropzone = document.getElementById("dropzone");
var fileInput = document.getElementById("file-input");
var errorBanner = document.getElementById("error-banner");
var stopButton = document.getElementById("stop-button");
var stopBanner = document.getElementById("stop-banner");
var settingsNote = document.getElementById("settings-note");
var lastUploadErrorAt = 0;

stopButton.addEventListener("click", async function () {
  stopButton.disabled = true;
  try {
    await fetchJson("/stop", { method: "POST" });
  } catch (error) {
    showError(error.message);
  }
});

dropzone.addEventListener("click", function () { fileInput.click(); });

fileInput.addEventListener("change", function () {
  if (fileInput.files.length > 0) {
    startRun(fileInput.files[0]);
  }
  fileInput.value = "";
});

dropzone.addEventListener("dragover", function (event) {
  event.preventDefault();
  dropzone.classList.add("hover");
});

dropzone.addEventListener("dragleave", function () {
  dropzone.classList.remove("hover");
});

dropzone.addEventListener("drop", function (event) {
  event.preventDefault();
  dropzone.classList.remove("hover");
  if (event.dataTransfer.files.length > 0) {
    startRun(event.dataTransfer.files[0]);
  }
});

async function startRun(file) {
  var url = "/run?filename=" + encodeURIComponent(file.name);
  var response = await fetch(url, { method: "POST", body: file });
  if (!response.ok) {
    var data = await response.json();
    showError(data.message);
  }
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
  lastUploadErrorAt = Date.now();
}

function renderStatusLine(state) {
  var line = document.getElementById("status-line");
  line.className = state.status;
  if (state.batch_running) {
    line.textContent = "Batchbehandling pågår. Resultatene står på siden Batchbehandling.";
  } else if (state.status === "idle") {
    line.textContent = "Venter på fil";
  } else if (state.status === "running") {
    if (state.stop_requested) {
      line.textContent = "Stopper ...";
    } else if (state.kind === "liste" && state.rows.length > 0) {
      line.textContent = "Behandler " + state.filename + ": rad " + Math.min(state.rows_done + 1, state.rows.length)
        + " av " + state.rows.length;
    } else {
      line.textContent = "Behandler " + state.filename + " ...";
    }
  } else if (state.status === "done") {
    line.textContent = "Ferdig på " + Math.round(state.duration_seconds) + " sekunder";
    // an invoice run carries one verdict, decided by its koststed
    if (state.resultat) {
      line.textContent = line.textContent + ", " + state.resultat;
    }
  } else if (state.status === "stopped") {
    line.textContent = "Stoppet";
  } else if (state.status === "error") {
    line.textContent = "Stoppet med feil";
  }
  if (state.model_server_label && (state.status === "running" || state.status === "done")) {
    line.textContent = line.textContent + " · " + state.model_server_label;
  }
}

function renderPipeline(state) {
  var pipeline = document.getElementById("pipeline");
  var parts = [];
  for (var i = 0; i < state.stages.length; i++) {
    var stage = state.stages[i];
    var className = "stage";
    if (stage.status !== "pending") {
      className = className + " " + stage.status;
    }
    parts.push(
      '<div class="' + className + '">'
      + '<span class="stage-dot"></span>'
      + '<div class="stage-title">' + esc(stage.title) + "</div>"
      + '<div class="stage-desc">' + esc(stage.description) + "</div>"
      + '<div class="stage-verdict">' + esc(stage.verdict) + "</div>"
      + "</div>"
    );
  }
  pipeline.innerHTML = parts.join("");
}

function renderLookups(state) {
  var lookupsSection = document.getElementById("lookups-section");
  var lookupList = document.getElementById("lookup-list");
  if (state.lookups.length === 0) {
    lookupsSection.hidden = true;
    return;
  }
  lookupsSection.hidden = false;
  var items = [];
  for (var i = 0; i < state.lookups.length; i++) {
    var lookup = state.lookups[i];
    var outcome;
    if (lookup.outcome === null) {
      outcome = "<em>pågår ...</em>";
    } else {
      outcome = '<span class="lookup-outcome">' + esc(lookup.outcome) + "</span>";
    }
    items.push(
      '<li><span class="lookup-name">' + esc(lookup.name) + ':</span> "'
      + esc(lookup.argument) + '" → ' + outcome + "</li>"
    );
  }
  lookupList.innerHTML = items.join("");
}

function renderProblems(state) {
  var resultsSection = document.getElementById("results-section");
  var resultRows = document.getElementById("result-rows");
  if (state.problems.length === 0) {
    resultsSection.hidden = true;
    return;
  }
  resultsSection.hidden = false;
  var rows = [];
  for (var j = 0; j < state.problems.length; j++) {
    var row = state.problems[j];

    var suggestionCell = "";
    if (row.suggestion !== "") {
      suggestionCell = '<div class="suggestion-value">' + esc(row.suggestion) + copyButton(row.suggestion) + "</div>";
    }
    if (row.why !== "") {
      suggestionCell = suggestionCell + '<div class="small">' + esc(row.why) + "</div>";
    }

    // the category name from the classifier is the short headline;
    // the model's problem text becomes the explanation under it
    var problemCell = "";
    if (row.category !== "") {
      problemCell = '<div class="headline">' + esc(row.category) + "</div>"
        + '<div class="small">' + esc(row.problem) + "</div>";
    } else {
      problemCell = esc(row.problem);
    }
    problemCell = problemCell
      + '<div class="small">felt: ' + esc(row.field) + ", verdi: " + esc(row.value) + "</div>";

    rows.push(
      "<tr>"
      + "<td>" + problemCell + "</td>"
      + "<td>" + suggestionCell + "</td>"
      + "<td>" + badgeFor(row.status) + "</td>"
      + "</tr>"
    );
  }
  resultRows.innerHTML = rows.join("");
}

function renderListRows(state) {
  var listSection = document.getElementById("list-section");
  var listRows = document.getElementById("list-rows");
  var savedFiles = document.getElementById("saved-files");
  if (state.rows.length === 0) {
    listSection.hidden = true;
    return;
  }
  listSection.hidden = false;
  var rows = [];
  for (var i = 0; i < state.rows.length; i++) {
    var row = state.rows[i];

    var koststedCell = "";
    for (var k = 0; k < row.funnet_koststed.length; k++) {
      koststedCell = koststedCell + '<div class="koststed-value">' + esc(row.funnet_koststed[k])
        + copyButton(row.funnet_koststed[k]) + "</div>";
    }

    var runEnded = state.status === "error" || state.status === "stopped";
    var statusCell;
    if (row.status === "pending" && runEnded) {
      statusCell = '<span class="small">ikke behandlet</span>';
    } else if (row.status === "active" && runEnded) {
      statusCell = '<span class="small">avbrutt</span>';
    } else if (row.status === "pending") {
      statusCell = '<span class="small">venter</span>';
    } else if (row.status === "active") {
      statusCell = "<em>pågår ...</em>";
    } else {
      statusCell = badgeFor(row.resultat);
      if (row.tid_brukt !== null) {
        statusCell = statusCell + '<div class="small">' + esc(row.tid_brukt) + " s</div>";
      }
    }

    rows.push(
      '<tr class="row-' + esc(row.status) + '">'
      + "<td>" + esc(row.row_number) + "</td>"
      + "<td>" + esc(row.leverandor) + "</td>"
      + '<td class="code">' + esc(row.fakturanr) + "</td>"
      + "<td>" + esc(row.deres_ref) + "</td>"
      + '<td class="code">' + esc(row.ordrenr) + "</td>"
      + "<td>" + koststedCell + "</td>"
      + "<td>" + esc(row.begrunnelse) + "</td>"
      + "<td>" + statusCell + "</td>"
      + "</tr>"
    );
  }
  listRows.innerHTML = rows.join("");

  if (state.saved.length > 0) {
    savedFiles.textContent = "Lagret: " + state.saved.join(", ");
  } else {
    savedFiles.textContent = "";
  }
}

var lastStateJson = null;

// A run started on the batch page is not this page's: it is shown as an idle page with one
// line while it runs, and not at all when it is over.
function batchView(state) {
  return {
    status: "idle",
    kind: null,
    source: "batch",
    filename: null,
    error: null,
    duration_seconds: null,
    stages: [],
    lookups: [],
    problems: [],
    rows: [],
    rows_done: 0,
    resultat: null,
    saved: [],
    stop_requested: false,
    batch_running: state.status === "running",
    data_error: state.data_error,
    settings_missing: state.settings_missing,
    run_id: state.run_id,
  };
}

function render(state) {
  if (state.source === "batch") {
    state = batchView(state);
  }

  // the error banner shows data and run errors, and upload errors for a few seconds
  if (state.data_error) {
    errorBanner.textContent = state.data_error;
    errorBanner.hidden = false;
  } else if (state.status === "error" && state.error) {
    errorBanner.textContent = state.error;
    errorBanner.hidden = false;
  } else if (Date.now() - lastUploadErrorAt > 5000) {
    errorBanner.hidden = true;
  }

  // the page is rebuilt only when the run state changed, so a button under the mouse stays put
  var stateJson = JSON.stringify(state);
  if (stateJson === lastStateJson) {
    return;
  }
  lastStateJson = stateJson;

  renderStatusLine(state);
  stopButton.hidden = state.status !== "running";
  stopButton.disabled = state.stop_requested === true;

  if (state.status === "stopped") {
    var stopText = "Kjøringen ble stoppet. Det som var ferdig, står på siden";
    if (state.saved.length > 0) {
      stopText = stopText + " og er lagret";
    }
    stopBanner.textContent = stopText + ".";
    stopBanner.hidden = false;
  } else {
    stopBanner.hidden = true;
  }

  if (state.settings_missing && state.settings_missing.length > 0) {
    settingsNote.textContent = "Innstillinger mangler: " + state.settings_missing.join(", ")
      + ". Fyll dem inn under Innstillinger og start appen på nytt.";
    settingsNote.hidden = false;
  } else {
    settingsNote.hidden = true;
  }

  renderPipeline(state);
  renderLookups(state);
  renderProblems(state);
  renderListRows(state);
}

onStatus(render);
