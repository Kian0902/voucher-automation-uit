// The batch page: many files dropped into a queue that the server works through one at a
// time. The table has one line per file; the panel under it shows the rows of the selected
// file, live for the one that is running, and stays on that file until the staff pick another.
// A part of the page is rebuilt only when its HTML changed, so a button under the mouse stays put.

var dropzone = document.getElementById("dropzone");
var fileInput = document.getElementById("file-input");
var errorBanner = document.getElementById("batch-error");
var settingsNote = document.getElementById("settings-note");
var addNotes = document.getElementById("add-notes");
var summaryLine = document.getElementById("summary");
var stopButton = document.getElementById("stop-button");
var stopAllButton = document.getElementById("stop-all-button");
var clearButton = document.getElementById("clear-button");
var queueSection = document.getElementById("queue-section");
var queueBody = document.getElementById("queue-rows");
var detailSection = document.getElementById("detail-section");
var detailTitle = document.getElementById("detail-title");
var detailFilters = document.getElementById("detail-filters");
var detailBody = document.getElementById("detail-body");

var BATCH_POLL_MS = 500;
var NOTE_MS = 8000;
var KIND_NAMES = { faktura: "Faktura", liste: "Liste" };
var STATUS_NAMES = { waiting: "venter", running: "pågår", done: "ferdig", error: "feilet", stopped: "stoppet" };
var FILTERS = [
  { key: "alle", title: "Alle" },
  { key: "løst", title: "Løst" },
  { key: "bekreft", title: "Må bekreftes" },
  { key: "uløst", title: "Uløst" },
];

var view = {
  jobs: [],
  counts: null,
  totals: null,
  selectedId: null,      // stays where the staff put it; a file ending never moves it
  filter: "alle",
  liveState: null,       // the last /status: the detail of the file that is running
  results: {},           // job id -> final state, fetched once
  loading: {},           // job id -> true while its result is being fetched
  expanded: {},          // "jobId:key" -> true, the rows whose explanation is open
};
var lastHtml = {};        // element id -> the HTML last written
var noteTimer = null;

function setHtml(element, html) {
  if (lastHtml[element.id] !== html) {
    element.innerHTML = html;
    lastHtml[element.id] = html;
  }
}

function showError(text) {
  errorBanner.textContent = text;
  errorBanner.hidden = false;
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined) {
    return "";
  }
  if (seconds < 60) {
    return Math.round(seconds) + " s";
  }
  var minutes = Math.floor(seconds / 60);
  return minutes + " min " + Math.round(seconds - minutes * 60) + " s";
}

// "må bekreftes" and "flere funnet" are one group for the staff: something to confirm
function filterKeyOf(label) {
  if (label === "løst") {
    return "løst";
  }
  if (label === "må bekreftes" || label === "flere funnet") {
    return "bekreft";
  }
  if (label === "uløst") {
    return "uløst";
  }
  return null;
}

function countBadges(counts) {
  var parts = [];
  var confirm = counts["må bekreftes"] + counts["flere funnet"];
  if (counts["løst"] > 0) {
    parts.push('<span class="badge badge-ok">' + counts["løst"] + " løst</span>");
  }
  if (confirm > 0) {
    parts.push('<span class="badge badge-confirm">' + confirm + " må bekreftes</span>");
  }
  if (counts["uløst"] > 0) {
    parts.push('<span class="badge badge-open">' + counts["uløst"] + " uløst</span>");
  }
  return parts.join(" ");
}

function selectedJob() {
  for (var i = 0; i < view.jobs.length; i++) {
    if (view.jobs[i].id === view.selectedId) {
      return view.jobs[i];
    }
  }
  return null;
}

// THE QUEUE

// A file is picked only when none is: the one that is running, else the first. After that the
// selection is the staff's, so the rows they are reading stay while the queue moves on.
function ensureSelection() {
  if (selectedJob() !== null) {
    return;
  }
  view.selectedId = null;
  for (var i = 0; i < view.jobs.length; i++) {
    if (view.jobs[i].status === "running") {
      view.selectedId = view.jobs[i].id;
    }
  }
  if (view.selectedId === null && view.jobs.length > 0) {
    view.selectedId = view.jobs[0].id;
  }
}

function renderSummary() {
  var counts = view.counts;
  queueSection.hidden = counts.total === 0;
  if (counts.total === 0) {
    return;
  }
  var fileWord = "filer";
  if (counts.total === 1) {
    fileWord = "fil";
  }
  var text = counts.total + " " + fileWord + ": " + counts.done + " ferdig";
  if (counts.running > 0) {
    text = text + ", " + counts.running + " pågår";
  }
  if (counts.waiting > 0) {
    text = text + ", " + counts.waiting + " venter";
  }
  if (counts.error > 0) {
    text = text + ", " + counts.error + " feilet";
  }
  if (counts.stopped > 0) {
    text = text + ", " + counts.stopped + " stoppet";
  }
  setHtml(summaryLine, "<span>" + esc(text) + "</span> " + countBadges(view.totals));

  stopButton.hidden = counts.running === 0;
  stopAllButton.hidden = counts.running + counts.waiting < 2;
  clearButton.hidden = counts.done + counts.error + counts.stopped === 0;
}

function renderQueue() {
  var rows = [];
  for (var i = 0; i < view.jobs.length; i++) {
    var job = view.jobs[i];
    var className = "queue-row";
    if (job.id === view.selectedId) {
      className = className + " selected";
    }

    var statusCell;
    if (job.status === "running") {
      statusCell = "<em>" + esc(job.progress || "pågår") + " ...</em>";
    } else if (job.status === "error") {
      statusCell = '<span class="badge badge-open">feilet</span>';
    } else if (job.status === "stopped" && job.error === "Ikke startet") {
      statusCell = "stoppet, ikke startet";
    } else {
      statusCell = esc(STATUS_NAMES[job.status]);
    }

    var resultCell = "";
    if (job.summary !== null) {
      resultCell = countBadges(job.summary);
      if (job.status === "done" && job.summary.total === 0) {
        resultCell = '<span class="small">ingen problemer funnet</span>';
      }
    }

    var actions = "";
    if (job.can_retry) {
      actions = actions + '<button type="button" class="button-secondary" data-action="retry" data-id="' + job.id + '">Prøv igjen</button> ';
    }
    if (job.status !== "running") {
      actions = actions + '<button type="button" class="button-secondary" data-action="remove" data-id="' + job.id + '">Fjern</button>';
    }

    rows.push(
      '<tr class="' + className + '" data-id="' + job.id + '">'
      + '<td class="queue-file">' + esc(job.filename) + "</td>"
      + "<td>" + esc(KIND_NAMES[job.kind]) + "</td>"
      + "<td>" + statusCell + "</td>"
      + "<td>" + resultCell + "</td>"
      + "<td>" + esc(durationText(job.duration_seconds)) + "</td>"
      + '<td class="row-actions">' + actions + "</td>"
      + "</tr>"
    );
  }
  setHtml(queueBody, rows.join(""));
}

// THE DETAIL OF ONE FILE

async function fetchResult(jobId) {
  view.loading[jobId] = true;
  try {
    var data = await fetchJson("/batch/job?id=" + jobId);
    if (data.result !== null) {
      view.results[jobId] = data.result;
    }
  } catch (error) {
    // the file was removed meanwhile; the next queue poll drops it from the table
  }
  delete view.loading[jobId];
  renderDetail();
}

// The state to show for a file: the live run while it is that file's run, the kept result
// after. When a file ends, the live state on the page is already its final state, so it is
// kept as the result at once and the rows never blink away.
function detailState(job) {
  if (view.results[job.id] !== undefined) {
    return view.results[job.id];
  }
  if (job.status === "waiting") {
    return null;
  }
  var live = view.liveState;
  if (live !== null && job.run_id !== null && live.run_id === job.run_id) {
    if (job.status !== "running" && live.status !== "running") {
      view.results[job.id] = live;
    }
    return live;
  }
  if (job.status === "running") {
    return null;
  }
  if (!view.loading[job.id]) {
    fetchResult(job.id);
  }
  return null;
}

function expandKey(job, key) {
  return job.id + ":" + key;
}

function listDetail(job, state) {
  var runEnded = state.status === "error" || state.status === "stopped";
  var counts = { alle: 0, "løst": 0, bekreft: 0, "uløst": 0 };
  var rows = [];
  for (var i = 0; i < state.rows.length; i++) {
    var row = state.rows[i];
    var group = null;
    if (row.status === "done") {
      group = filterKeyOf(row.resultat);
    }
    counts.alle = counts.alle + 1;
    if (group !== null) {
      counts[group] = counts[group] + 1;
    }
    if (view.filter !== "alle" && group !== view.filter) {
      continue;
    }

    var koststedCell = "";
    for (var k = 0; k < row.funnet_koststed.length; k++) {
      koststedCell = koststedCell + '<div class="koststed-value">' + esc(row.funnet_koststed[k])
        + copyButton(row.funnet_koststed[k]) + "</div>";
    }

    var statusCell;
    if (row.status === "done") {
      statusCell = badgeFor(row.resultat);
    } else if (row.status === "active" && !runEnded) {
      statusCell = "<em>pågår ...</em>";
    } else if (row.status === "active") {
      statusCell = '<span class="small">avbrutt</span>';
    } else if (runEnded) {
      statusCell = '<span class="small">ikke behandlet</span>';
    } else {
      statusCell = '<span class="small">venter</span>';
    }

    var key = expandKey(job, row.row_number);
    var open = view.expanded[key] === true;
    var className = "result-row";
    if (open) {
      className = className + " open";
    }
    rows.push(
      '<tr class="' + className + '" data-key="' + key + '">'
      + "<td>" + esc(row.row_number) + "</td>"
      + "<td>" + esc(row.leverandor) + "</td>"
      + "<td>" + esc(row.fakturanr) + "</td>"
      + "<td>" + esc(row.deres_ref) + "</td>"
      + "<td>" + esc(row.ordrenr) + "</td>"
      + '<td class="split">' + koststedCell + "</td>"
      + "<td>" + statusCell + "</td>"
      + "</tr>"
    );
    if (open) {
      var explanation = row.begrunnelse;
      if (!explanation) {
        explanation = "Ingen begrunnelse ennå.";
      }
      var facts = "";
      if (row.tid_brukt !== null) {
        facts = '<div class="small">' + esc(row.tid_brukt) + " s, " + esc(row.oppslag_brukt) + " oppslag</div>";
      }
      rows.push('<tr class="row-detail"><td></td><td colspan="6">' + esc(explanation) + facts + "</td></tr>");
    }
  }

  var parts = [];
  if (state.rows.length === 0) {
    parts.push('<p class="empty">Ingen rader ennå.</p>');
  } else {
    if (rows.length === 0) {
      rows.push('<tr><td colspan="7" class="empty">Ingen rader i dette utvalget.</td></tr>');
    }
    parts.push(
      "<table><thead><tr>"
      + "<th>Rad</th><th>Leverandør</th><th>Fakturanr</th><th>Deres ref.</th><th>Ordrenr</th>"
      + '<th class="split">Funnet koststed</th><th>Status</th>'
      + "</tr></thead><tbody>" + rows.join("") + "</tbody></table>"
      + '<p class="note">Klikk en rad for begrunnelsen.</p>'
    );
  }
  if (state.saved.length > 0) {
    parts.push('<div class="saved-files">Lagret: ' + esc(state.saved.join(", ")) + "</div>");
  }
  return { html: parts.join(""), counts: counts };
}

function invoiceDetail(job, state) {
  var runEnded = state.status !== "running";
  var counts = { alle: 0, "løst": 0, bekreft: 0, "uløst": 0 };
  var rows = [];
  for (var i = 0; i < state.problems.length; i++) {
    var problem = state.problems[i];
    var group = filterKeyOf(problem.status);
    counts.alle = counts.alle + 1;
    if (group !== null) {
      counts[group] = counts[group] + 1;
    }
    if (view.filter !== "alle" && group !== view.filter) {
      continue;
    }

    var problemCell = esc(problem.problem);
    if (problem.category !== "") {
      problemCell = '<div class="headline">' + esc(problem.category) + "</div>"
        + '<div class="small">' + esc(problem.problem) + "</div>";
    }
    var suggestionCell = "";
    if (problem.suggestion !== "") {
      suggestionCell = '<div class="suggestion-value">' + esc(problem.suggestion) + copyButton(problem.suggestion) + "</div>";
    }
    var statusCell = badgeFor(problem.status);
    if (problem.status === "" && !runEnded) {
      statusCell = "<em>pågår ...</em>";
    } else if (problem.status === "") {
      statusCell = '<span class="small">ikke behandlet</span>';
    }

    var key = expandKey(job, "p" + i);
    var open = view.expanded[key] === true;
    var className = "result-row";
    if (open) {
      className = className + " open";
    }
    rows.push(
      '<tr class="' + className + '" data-key="' + key + '">'
      + "<td>" + problemCell + "</td>"
      + '<td class="split">' + suggestionCell + "</td>"
      + "<td>" + statusCell + "</td>"
      + "</tr>"
    );
    if (open) {
      var why = problem.why;
      if (why === "") {
        why = "Ingen begrunnelse ennå.";
      }
      rows.push(
        '<tr class="row-detail"><td colspan="3">' + esc(why)
        + '<div class="small">felt: ' + esc(problem.field) + ", verdi: " + esc(problem.value) + "</div></td></tr>"
      );
    }
  }

  var parts = [];
  if (state.problems.length === 0) {
    // the verdict of the stage that looks for problems says why the table is empty
    var verdict = "Leter etter problemer ...";
    for (var s = 0; s < state.stages.length; s++) {
      if (state.stages[s].key === "identify" && state.stages[s].verdict !== "") {
        verdict = state.stages[s].verdict;
      }
    }
    parts.push('<p class="empty">' + esc(verdict) + "</p>");
  } else {
    if (rows.length === 0) {
      rows.push('<tr><td colspan="3" class="empty">Ingen problemer i dette utvalget.</td></tr>');
    }
    parts.push(
      "<table><thead><tr><th>Problem</th>"
      + '<th class="split">Forslag</th><th>Status</th>'
      + "</tr></thead><tbody>" + rows.join("") + "</tbody></table>"
      + '<p class="note">Klikk en rad for begrunnelsen.</p>'
    );
  }
  if (state.lookups.length > 0) {
    var lookups = [];
    for (var j = 0; j < state.lookups.length; j++) {
      var lookup = state.lookups[j];
      var outcome = lookup.outcome;
      if (outcome === null) {
        outcome = "pågår";
      }
      lookups.push(lookup.name + ' "' + lookup.argument + '": ' + outcome);
    }
    parts.push('<p class="note">Oppslag: ' + esc(lookups.join("; ")) + "</p>");
  }
  return { html: parts.join(""), counts: counts };
}

function renderDetail() {
  var job = selectedJob();
  detailSection.hidden = job === null;
  if (job === null) {
    return;
  }
  var title = job.filename + ", " + STATUS_NAMES[job.status];
  if (detailTitle.textContent !== title) {
    detailTitle.textContent = title;
  }

  var state = detailState(job);
  if (state === null) {
    var waitingText = "Henter resultatet ...";
    if (job.status === "waiting") {
      waitingText = "Venter i køen.";
    } else if (job.status === "running") {
      waitingText = "Starter ...";
    } else if (job.error) {
      // a file that never started has no run to show, only its reason
      waitingText = job.error;
    }
    setHtml(detailFilters, "");
    setHtml(detailBody, '<p class="empty">' + esc(waitingText) + "</p>");
    return;
  }

  var detail;
  if (job.kind === "liste") {
    detail = listDetail(job, state);
  } else {
    detail = invoiceDetail(job, state);
  }

  var banner = "";
  if (state.status === "error" && state.error) {
    banner = '<div class="error-banner">' + esc(state.error) + "</div>";
  } else if (state.status === "stopped") {
    banner = '<div class="lock-note">Filen ble stoppet. Det som var ferdig, står under.</div>';
  }

  var chips = [];
  for (var i = 0; i < FILTERS.length; i++) {
    var filter = FILTERS[i];
    var className = "chip";
    if (filter.key === view.filter) {
      className = className + " active";
    }
    chips.push('<button type="button" class="' + className + '" data-filter="' + filter.key + '">'
      + esc(filter.title) + " " + detail.counts[filter.key] + "</button>");
  }
  setHtml(detailFilters, chips.join(""));
  setHtml(detailBody, banner + detail.html);
}

function renderAll() {
  ensureSelection();
  renderSummary();
  renderQueue();
  renderDetail();
}

// LOADING AND CHANGES

async function loadQueue() {
  try {
    var data = await fetchJson("/batch/state");
    view.jobs = data.jobs;
    view.counts = data.counts;
    view.totals = data.totals;
    renderAll();
  } catch (error) {
    // serveren svarer ikke akkurat naa, proever igjen
  }
}

function showNotes(notes) {
  if (notes.length === 0) {
    return;
  }
  var items = [];
  for (var i = 0; i < notes.length; i++) {
    items.push("<li>" + esc(notes[i]) + "</li>");
  }
  addNotes.innerHTML = items.join("");
  addNotes.hidden = false;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(function () { addNotes.hidden = true; }, NOTE_MS);
}

// the files go up one by one, so the queue keeps the order they were dropped in
async function addFiles(fileList) {
  var files = [];
  for (var i = 0; i < fileList.length; i++) {
    files.push(fileList[i]);
  }
  var notes = [];
  for (var j = 0; j < files.length; j++) {
    var file = files[j];
    try {
      await fetchJson("/batch/add?filename=" + encodeURIComponent(file.name), { method: "POST", body: file });
    } catch (error) {
      notes.push(error.message);
    }
  }
  showNotes(notes);
  await loadQueue();
}

async function postAction(url) {
  try {
    await fetchJson(url, { method: "POST" });
  } catch (error) {
    showNotes([error.message]);
  }
  await loadQueue();
}

// EVENTS

dropzone.addEventListener("click", function () { fileInput.click(); });

fileInput.addEventListener("change", function () {
  if (fileInput.files.length > 0) {
    addFiles(fileInput.files);
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
    addFiles(event.dataTransfer.files);
  }
});

stopButton.addEventListener("click", function () { postAction("/batch/stop"); });
stopAllButton.addEventListener("click", function () { postAction("/batch/stop-all"); });
clearButton.addEventListener("click", function () {
  view.results = {};
  postAction("/batch/clear");
});

queueBody.addEventListener("click", function (event) {
  var button = event.target.closest("button[data-action]");
  if (button !== null) {
    var jobId = parseInt(button.dataset.id, 10);
    delete view.results[jobId];
    if (button.dataset.action === "retry") {
      postAction("/batch/retry?id=" + jobId);
    } else {
      postAction("/batch/remove?id=" + jobId);
    }
    return;
  }
  var row = event.target.closest("tr[data-id]");
  if (row === null) {
    return;
  }
  view.selectedId = parseInt(row.dataset.id, 10);
  renderAll();
});

detailFilters.addEventListener("click", function (event) {
  var chip = event.target.closest("button[data-filter]");
  if (chip === null) {
    return;
  }
  view.filter = chip.dataset.filter;
  renderDetail();
});

detailBody.addEventListener("click", function (event) {
  if (event.target.closest("button") !== null) {
    return;
  }
  var row = event.target.closest("tr[data-key]");
  if (row === null) {
    return;
  }
  var key = row.dataset.key;
  if (view.expanded[key]) {
    delete view.expanded[key];
  } else {
    view.expanded[key] = true;
  }
  renderDetail();
});

// the shared status poll carries the live run, the data error and the missing settings
onStatus(function (state) {
  view.liveState = state;
  if (state.data_error) {
    showError(state.data_error);
  } else {
    errorBanner.hidden = true;
  }
  if (state.settings_missing && state.settings_missing.length > 0) {
    settingsNote.textContent = "Innstillinger mangler: " + state.settings_missing.join(", ")
      + ". Fyll dem inn under Innstillinger og start appen på nytt.";
    settingsNote.hidden = false;
  } else {
    settingsNote.hidden = true;
  }
  var job = selectedJob();
  if (job !== null && job.status === "running") {
    renderDetail();
  }
});

setInterval(loadQueue, BATCH_POLL_MS);
loadQueue();
