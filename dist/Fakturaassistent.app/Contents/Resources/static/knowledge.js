// The knowledge base: the three reports as pages of rows with search, inline add, edit and
// delete, upload of a new export and download of the current file. Every change goes through
// the server, which writes the CSV and reloads the lookups; the page then fetches again.

var reportTabs = document.getElementById("report-tabs");
var searchInput = document.getElementById("search");
var pageSizeSelect = document.getElementById("page-size");
var countLine = document.getElementById("count");
var addButton = document.getElementById("add-button");
var uploadButton = document.getElementById("upload-button");
var uploadInput = document.getElementById("upload-input");
var uploadConfirm = document.getElementById("upload-confirm");
var uploadText = document.getElementById("upload-text");
var uploadAccept = document.getElementById("upload-accept");
var uploadCancel = document.getElementById("upload-cancel");
var downloadLink = document.getElementById("download-link");
var messageLine = document.getElementById("message");
var errorBanner = document.getElementById("knowledge-error");
var lockNote = document.getElementById("lock-note");
var rowsHead = document.getElementById("rows-head");
var rowsBody = document.getElementById("rows-body");
var prevButton = document.getElementById("prev");
var nextButton = document.getElementById("next");
var pageInfo = document.getElementById("page-info");

var SEARCH_DELAY_MS = 300;

// what the page shows; every change to it goes through a render function
var view = {
  report: "a",
  query: "",
  page: 1,
  size: 50,
  reports: [],          // from /knowledge/reports
  backupFolder: "",
  columns: [],
  rows: [],             // the current page, {index, values}
  total: 0,
  pages: 1,
  locked: false,        // a run is in progress, the server refuses writes
  editing: null,        // index of the row being edited
  deleting: null,       // index of the row whose deletion awaits confirmation
  adding: false,        // the new-row inputs are shown
  pendingUpload: null,  // the chosen file, until the staff confirms
};
var searchTimer = null;

function currentReport() {
  for (var i = 0; i < view.reports.length; i++) {
    if (view.reports[i].id === view.report) {
      return view.reports[i];
    }
  }
  return null;
}

function showError(text) {
  errorBanner.textContent = text;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
}

function showMessage(text) {
  messageLine.textContent = text;
  messageLine.hidden = false;
}

function clearMessage() {
  messageLine.hidden = true;
}

// RENDERING

function renderTabs() {
  var parts = [];
  for (var i = 0; i < view.reports.length; i++) {
    var report = view.reports[i];
    var className = "report-tab";
    if (report.id === view.report) {
      className = className + " active";
    }
    var detail;
    if (report.error) {
      detail = "Filen kan ikke leses";
    } else {
      detail = formatNumber(report.rows) + " rader i filen";
      if (report.loaded !== null && report.loaded !== report.rows) {
        detail = detail + ", " + formatNumber(report.loaded) + " i bruk";
      }
    }
    var fileLine = report.file;
    if (report.modified) {
      fileLine = fileLine + ", " + formatDate(report.modified);
    }
    parts.push('<button type="button" class="' + className + '" data-report="' + report.id + '">'
      + '<span class="report-title">' + esc(report.title) + "</span>"
      + '<span class="report-detail">' + esc(detail) + "</span>"
      + '<span class="report-file">' + esc(fileLine) + "</span>"
      + "</button>");
  }
  reportTabs.innerHTML = parts.join("");
}

function applyLock() {
  addButton.disabled = view.locked;
  uploadButton.disabled = view.locked;
  lockNote.hidden = !view.locked;
}

function renderHead() {
  var cells = ['<th class="row-number">Rad</th>'];
  for (var i = 0; i < view.columns.length; i++) {
    cells.push("<th>" + esc(view.columns[i]) + "</th>");
  }
  cells.push('<th class="row-actions"></th>');
  rowsHead.innerHTML = "<tr>" + cells.join("") + "</tr>";
}

function inputCells(values) {
  var cells = [];
  for (var i = 0; i < view.columns.length; i++) {
    var value = "";
    if (values[i] !== undefined) {
      value = values[i];
    }
    cells.push('<td><input class="cell-input" value="' + escAttr(value) + '"></td>');
  }
  return cells.join("");
}

function textCells(values) {
  var cells = [];
  for (var i = 0; i < view.columns.length; i++) {
    cells.push("<td>" + esc(values[i]) + "</td>");
  }
  return cells.join("");
}

function actionButton(action, index, label, className) {
  var disabled = "";
  if (view.locked) {
    disabled = " disabled";
  }
  return '<button type="button" class="' + className + '" data-action="' + action + '" data-index="' + index + '"'
    + disabled + ">" + label + "</button>";
}

function actionCell(index) {
  if (view.deleting === index) {
    return actionButton("confirm-delete", index, "Bekreft sletting", "button-danger") + " "
      + actionButton("cancel", index, "Avbryt", "button-secondary");
  }
  if (view.editing === index) {
    return actionButton("save", index, "Lagre", "") + " "
      + actionButton("cancel", index, "Avbryt", "button-secondary");
  }
  return actionButton("edit", index, "Rediger", "button-secondary") + " "
    + actionButton("delete", index, "Slett", "button-secondary");
}

function renderRows() {
  var parts = [];
  if (view.adding) {
    parts.push('<tr class="row-editing"><td class="row-number">Ny</td>' + inputCells([])
      + '<td class="row-actions">' + actionButton("save-new", -1, "Lagre", "") + " "
      + actionButton("cancel-new", -1, "Avbryt", "button-secondary") + "</td></tr>");
  }
  for (var i = 0; i < view.rows.length; i++) {
    var row = view.rows[i];
    var className = "";
    var cells = textCells(row.values);
    if (view.editing === row.index) {
      className = "row-editing";
      cells = inputCells(row.values);
    } else if (view.deleting === row.index) {
      className = "row-deleting";
    }
    parts.push('<tr class="' + className + '"><td class="row-number">' + (row.index + 1) + "</td>" + cells
      + '<td class="row-actions">' + actionCell(row.index) + "</td></tr>");
  }
  if (view.rows.length === 0 && !view.adding) {
    var text = "Ingen rader";
    if (view.query !== "") {
      text = "Ingen rader matcher søket";
    }
    parts.push('<tr><td colspan="' + (view.columns.length + 2) + '" class="empty">' + text + "</td></tr>");
  }
  rowsBody.innerHTML = parts.join("");

  var firstInput = rowsBody.querySelector(".row-editing input");
  if (firstInput !== null) {
    firstInput.focus();
  }
}

function renderPager() {
  var from = 0;
  if (view.total > 0) {
    from = (view.page - 1) * view.size + 1;
  }
  var to = Math.min(view.page * view.size, view.total);
  if (view.query === "") {
    countLine.textContent = formatNumber(view.total) + " rader";
  } else {
    countLine.textContent = formatNumber(view.total) + " treff";
  }
  pageInfo.textContent = "Viser " + from + " til " + to + " av " + formatNumber(view.total)
    + ", side " + view.page + " av " + view.pages;
  prevButton.disabled = view.page <= 1;
  nextButton.disabled = view.page >= view.pages;
}

// LOADING

async function loadReports() {
  try {
    var data = await fetchJson("/knowledge/reports");
    view.reports = data.reports;
    view.backupFolder = data.backup_folder;
    view.locked = data.locked;
    renderTabs();
    applyLock();
  } catch (error) {
    showError(error.message);
  }
}

async function loadRows() {
  var report = currentReport();
  if (report !== null) {
    view.columns = report.columns;
  }
  var url = "/knowledge/rows?report=" + encodeURIComponent(view.report)
    + "&query=" + encodeURIComponent(view.query)
    + "&page=" + view.page + "&size=" + view.size;
  try {
    var data = await fetchJson(url);
    view.rows = data.rows;
    view.total = data.total;
    view.page = data.page;
    view.pages = data.pages;
    clearError();
  } catch (error) {
    view.rows = [];
    view.total = 0;
    view.pages = 1;
    showError(error.message);
  }
  renderHead();
  renderRows();
  renderPager();
}

async function reloadAll() {
  await loadReports();
  await loadRows();
}

// CHANGES

function valuesFromInputs(rowElement) {
  var inputs = rowElement.querySelectorAll("input.cell-input");
  var values = [];
  for (var i = 0; i < inputs.length; i++) {
    values.push(inputs[i].value);
  }
  return values;
}

function findRow(index) {
  for (var i = 0; i < view.rows.length; i++) {
    if (view.rows[i].index === index) {
      return view.rows[i];
    }
  }
  return null;
}

async function saveNew(rowElement) {
  var values = valuesFromInputs(rowElement);
  try {
    var data = await postJson("/knowledge/add", { report: view.report, values: values });
    view.adding = false;
    // the new row is last in the file, so show the page it landed on
    view.query = "";
    searchInput.value = "";
    view.page = Math.floor(data.index / view.size) + 1;
    showMessage(data.message);
    await reloadAll();
  } catch (error) {
    showError(error.message);
  }
}

async function saveEdit(index, rowElement) {
  var row = findRow(index);
  var values = valuesFromInputs(rowElement);
  try {
    var data = await postJson("/knowledge/edit", {
      report: view.report, index: index, old_values: row.values, values: values,
    });
    view.editing = null;
    showMessage(data.message);
    await reloadAll();
  } catch (error) {
    showError(error.message);
  }
}

async function confirmDelete(index) {
  var row = findRow(index);
  try {
    var data = await postJson("/knowledge/delete", {
      report: view.report, index: index, old_values: row.values,
    });
    view.deleting = null;
    showMessage(data.message);
    await reloadAll();
  } catch (error) {
    showError(error.message);
  }
}

async function uploadPending() {
  var file = view.pendingUpload;
  view.pendingUpload = null;
  uploadConfirm.hidden = true;
  showMessage("Laster opp " + file.name + " ...");
  var url = "/knowledge/upload?report=" + encodeURIComponent(view.report)
    + "&filename=" + encodeURIComponent(file.name);
  try {
    var data = await fetchJson(url, { method: "POST", body: file });
    view.query = "";
    searchInput.value = "";
    view.page = 1;
    showMessage(data.message);
    await reloadAll();
  } catch (error) {
    clearMessage();
    showError(error.message);
  }
}

// EVENTS

reportTabs.addEventListener("click", function (event) {
  var tab = event.target.closest("button[data-report]");
  if (tab === null || tab.dataset.report === view.report) {
    return;
  }
  view.report = tab.dataset.report;
  view.page = 1;
  view.editing = null;
  view.deleting = null;
  view.adding = false;
  view.pendingUpload = null;
  uploadConfirm.hidden = true;
  downloadLink.href = "/knowledge/download?report=" + view.report;
  clearMessage();
  clearError();
  renderTabs();
  loadRows();
});

rowsBody.addEventListener("click", function (event) {
  var button = event.target.closest("button[data-action]");
  if (button === null) {
    return;
  }
  var action = button.dataset.action;
  var index = parseInt(button.dataset.index, 10);
  var rowElement = button.closest("tr");
  clearError();
  if (action === "edit") {
    view.editing = index;
    view.deleting = null;
    view.adding = false;
    renderRows();
  } else if (action === "delete") {
    view.deleting = index;
    view.editing = null;
    renderRows();
  } else if (action === "cancel") {
    view.editing = null;
    view.deleting = null;
    renderRows();
  } else if (action === "save") {
    saveEdit(index, rowElement);
  } else if (action === "confirm-delete") {
    confirmDelete(index);
  } else if (action === "save-new") {
    saveNew(rowElement);
  } else if (action === "cancel-new") {
    view.adding = false;
    renderRows();
  }
});

// Enter saves and Escape cancels the row being typed in
rowsBody.addEventListener("keydown", function (event) {
  if (!event.target.classList.contains("cell-input")) {
    return;
  }
  var selector = null;
  if (event.key === "Enter") {
    selector = 'button[data-action="save"], button[data-action="save-new"]';
  } else if (event.key === "Escape") {
    selector = 'button[data-action="cancel"], button[data-action="cancel-new"]';
  }
  if (selector !== null) {
    event.preventDefault();
    var button = event.target.closest("tr").querySelector(selector);
    if (button !== null) {
      button.click();
    }
  }
});

searchInput.addEventListener("input", function () {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(function () {
    view.query = searchInput.value.trim();
    view.page = 1;
    view.editing = null;
    view.deleting = null;
    loadRows();
  }, SEARCH_DELAY_MS);
});

pageSizeSelect.addEventListener("change", function () {
  view.size = parseInt(pageSizeSelect.value, 10);
  view.page = 1;
  loadRows();
});

prevButton.addEventListener("click", function () {
  view.page = view.page - 1;
  loadRows();
});

nextButton.addEventListener("click", function () {
  view.page = view.page + 1;
  loadRows();
});

addButton.addEventListener("click", function () {
  view.adding = true;
  view.editing = null;
  view.deleting = null;
  clearMessage();
  renderRows();
});

uploadButton.addEventListener("click", function () {
  uploadInput.value = "";
  uploadInput.click();
});

uploadInput.addEventListener("change", function () {
  if (uploadInput.files.length === 0) {
    return;
  }
  var report = currentReport();
  view.pendingUpload = uploadInput.files[0];
  uploadText.textContent = "Erstatte hele " + report.title + " (" + formatNumber(report.rows)
    + " rader) med " + view.pendingUpload.name + "? Endringer gjort i appen for denne rapporten går tapt. "
    + "Forrige fil beholdes i " + view.backupFolder + ".";
  uploadConfirm.hidden = false;
});

uploadCancel.addEventListener("click", function () {
  view.pendingUpload = null;
  uploadConfirm.hidden = true;
});

uploadAccept.addEventListener("click", function () {
  if (view.pendingUpload !== null) {
    uploadPending();
  }
});

// the row buttons follow the run; the server refuses a write during a run anyway
onStatus(function (state) {
  var locked = state.status === "running";
  if (locked !== view.locked) {
    view.locked = locked;
    applyLock();
    renderRows();
  }
});

downloadLink.href = "/knowledge/download?report=" + view.report;
reloadAll();
