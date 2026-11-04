// The dashboard: the numbers from /dashboard as tiles, one SVG bar chart per week and three
// lists. Reloads when the period changes and when a run on the agent page finishes.

var weeksSelect = document.getElementById("weeks");
var dashboardError = document.getElementById("dashboard-error");
var lastRunStatus = null;

var COLOR_RESOLVED = "#4c9a52";
var COLOR_CONFIRM = "#d9a21b";
var COLOR_UNRESOLVED = "#9a9a9a";
var CHART_WIDTH = 800;
var CHART_HEIGHT = 260;
var MARGIN = { top: 24, right: 10, bottom: 30, left: 40 };

function formatSeconds(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value < 60) {
    return Math.round(value) + " s";
  }
  var minutes = Math.floor(value / 60);
  var seconds = Math.round(value - minutes * 60);
  return minutes + " min " + seconds + " s";
}

function formatDateTime(isoText) {
  var date = new Date(isoText);
  var hours = String(date.getHours()).padStart(2, "0");
  var minutes = String(date.getMinutes()).padStart(2, "0");
  return formatDate(isoText) + " " + hours + ":" + minutes;
}

function kpi(label, value, sub) {
  return '<div class="kpi">'
    + '<div class="kpi-label">' + esc(label) + "</div>"
    + '<div class="kpi-value">' + esc(value) + "</div>"
    + '<div class="kpi-sub">' + esc(sub) + "</div>"
    + "</div>";
}

function renderKpis(data) {
  var week = data.this_week;
  var totals = data.totals;
  var tiles = [];

  tiles.push(kpi("Behandlet denne uken", formatNumber(week.items), week.runs + " kjøringer"));
  tiles.push(kpi("Løst denne uken", formatNumber(week["løst"]),
    (week["må bekreftes"] + week["flere funnet"]) + " må bekreftes, " + week["uløst"] + " uløst"));

  var rate = "-";
  if (totals.items > 0) {
    rate = Math.round(100 * totals["løst"] / totals.items) + " %";
  }
  tiles.push(kpi("Løsningsgrad", rate, totals["løst"] + " av " + totals.items + " siste " + data.weeks + " uker"));

  var time = data.time.all;
  var timeSub = "ingen data";
  if (time.count > 0) {
    timeSub = "snitt " + formatSeconds(time.average) + ", " + time.count + " fakturaer";
  }
  tiles.push(kpi("Tid per faktura (median)", formatSeconds(time.median), timeSub));

  tiles.push(kpi("Tokens brukt", formatNumber(data.tokens), "per faktura: " + formatNumber(data.per_item.tokens)));
  var lookupsPerItem = "-";
  if (data.per_item.lookups !== null) {
    lookupsPerItem = String(data.per_item.lookups).replace(".", ",");
  }
  tiles.push(kpi("Oppslag i rapportene", formatNumber(data.lookups), "per faktura: " + lookupsPerItem));
  tiles.push(kpi("Feilede kjøringer", formatNumber(totals.failed_runs), "av " + totals.runs + " kjøringer"));

  document.getElementById("kpis").innerHTML = tiles.join("");
}

// the top of the axis: the smallest round number above the tallest bar
function axisTop(maxValue) {
  if (maxValue <= 0) {
    return 4;
  }
  var steps = [1, 2, 5];
  var magnitude = 1;
  while (true) {
    for (var i = 0; i < steps.length; i++) {
      var step = steps[i] * magnitude;
      if (step * 4 >= maxValue) {
        return step * 4;
      }
    }
    magnitude = magnitude * 10;
  }
}

function renderChart(data) {
  var weeks = data.per_week;
  var plotWidth = CHART_WIDTH - MARGIN.left - MARGIN.right;
  var plotHeight = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;
  var slot = plotWidth / weeks.length;
  var barWidth = Math.min(slot * 0.6, 60);

  var maxItems = 0;
  for (var i = 0; i < weeks.length; i++) {
    if (weeks[i].items > maxItems) {
      maxItems = weeks[i].items;
    }
  }
  var top = axisTop(maxItems);
  var scale = plotHeight / top;

  var parts = ['<svg viewBox="0 0 ' + CHART_WIDTH + " " + CHART_HEIGHT + '" role="img" aria-label="Behandlet per uke">'];

  // four gridlines with their values
  for (var g = 0; g <= 4; g++) {
    var value = top * g / 4;
    var y = MARGIN.top + plotHeight - value * scale;
    parts.push('<line x1="' + MARGIN.left + '" x2="' + (CHART_WIDTH - MARGIN.right) + '" y1="' + y + '" y2="' + y
      + '" stroke="#e4e7eb" stroke-width="1"/>');
    parts.push('<text x="' + (MARGIN.left - 8) + '" y="' + (y + 4) + '" text-anchor="end" font-size="11" fill="#777">'
      + value + "</text>");
  }

  for (var w = 0; w < weeks.length; w++) {
    var week = weeks[w];
    var x = MARGIN.left + slot * w + (slot - barWidth) / 2;
    var baseline = MARGIN.top + plotHeight;
    var confirm = week["må bekreftes"] + week["flere funnet"];
    var segments = [
      { value: week["løst"], color: COLOR_RESOLVED },
      { value: confirm, color: COLOR_CONFIRM },
      { value: week["uløst"], color: COLOR_UNRESOLVED },
    ];
    var title = formatDate(week.week_start) + ": " + week.items + " behandlet, " + week["løst"] + " løst, "
      + confirm + " må bekreftes, " + week["uløst"] + " uløst";
    parts.push("<g><title>" + esc(title) + "</title>");
    var stackTop = baseline;
    for (var s = 0; s < segments.length; s++) {
      var height = segments[s].value * scale;
      if (height > 0) {
        stackTop = stackTop - height;
        parts.push('<rect x="' + x + '" y="' + stackTop + '" width="' + barWidth + '" height="' + height
          + '" fill="' + segments[s].color + '"/>');
      }
    }
    if (week.items > 0) {
      parts.push('<text x="' + (x + barWidth / 2) + '" y="' + (stackTop - 5) + '" text-anchor="middle" font-size="11" fill="#333">'
        + week.items + "</text>");
    }
    parts.push('<text x="' + (x + barWidth / 2) + '" y="' + (baseline + 18) + '" text-anchor="middle" font-size="11" fill="#555">'
      + esc(formatDate(week.week_start).slice(0, 5)) + "</text>");
    parts.push("</g>");
  }

  parts.push("</svg>");
  document.getElementById("chart").innerHTML = parts.join("");
}

function renderReasons(data) {
  var list = document.getElementById("reasons");
  if (data.reasons.length === 0) {
    list.innerHTML = '<li class="empty">Ingen uløste fakturaer i perioden</li>';
    return;
  }
  var items = [];
  for (var i = 0; i < data.reasons.length; i++) {
    var reason = data.reasons[i];
    items.push("<li><span>" + esc(reason.name) + "</span><strong>" + reason.count + "</strong></li>");
  }
  list.innerHTML = items.join("");
}

function renderReports(data) {
  var rows = [];
  for (var i = 0; i < data.reports.length; i++) {
    var report = data.reports[i];
    var rowCount = "ikke lastet";
    if (report.rows !== null) {
      rowCount = formatNumber(report.rows);
    }
    var modified = "fil mangler";
    if (report.modified !== null) {
      modified = formatDate(report.modified);
    }
    rows.push("<tr><td>" + esc(report.title) + '<div class="small">' + esc(report.file) + "</div></td>"
      + "<td>" + esc(rowCount) + "</td><td>" + esc(modified) + "</td></tr>");
  }
  document.getElementById("reports").innerHTML = rows.join("");
}

function resultText(run) {
  if (run.status === "error") {
    return '<span class="badge badge-open">Feilet</span><div class="small">' + esc(run.error) + "</div>";
  }
  var results = run.results;
  var text = results["løst"] + " av " + run.items + " løst";
  var rest = [];
  if (results["må bekreftes"] + results["flere funnet"] > 0) {
    rest.push((results["må bekreftes"] + results["flere funnet"]) + " må bekreftes");
  }
  if (results["uløst"] > 0) {
    rest.push(results["uløst"] + " uløst");
  }
  if (rest.length > 0) {
    text = text + '<div class="small">' + esc(rest.join(", ")) + "</div>";
  }
  return text;
}

function renderRecent(data) {
  var body = document.getElementById("recent");
  if (data.recent.length === 0) {
    body.innerHTML = '<tr><td colspan="6" class="empty">Ingen kjøringer ennå</td></tr>';
  } else {
    var rows = [];
    for (var i = 0; i < data.recent.length; i++) {
      var run = data.recent[i];
      var kind = "Faktura";
      if (run.kind === "liste") {
        kind = "Skjermbilde";
      }
      rows.push("<tr>"
        + "<td>" + esc(formatDateTime(run.finished_at)) + "</td>"
        + "<td>" + esc(kind) + "</td>"
        + "<td>" + esc(run.filename) + "</td>"
        + "<td>" + resultText(run) + "</td>"
        + "<td>" + esc(formatSeconds(run.duration_seconds)) + "</td>"
        + "<td>" + esc(formatNumber(run.tokens)) + "</td>"
        + "</tr>");
    }
    body.innerHTML = rows.join("");
  }

  var models = document.getElementById("models");
  if (data.models.length === 0) {
    models.textContent = "";
    return;
  }
  var names = [];
  for (var m = 0; m < data.models.length; m++) {
    names.push(data.models[m].model + " (" + data.models[m].runs + " kjøringer)");
  }
  models.textContent = "Modell i perioden: " + names.join(", ");
}

async function loadDashboard() {
  try {
    var data = await fetchJson("/dashboard?weeks=" + encodeURIComponent(weeksSelect.value));
    dashboardError.hidden = true;
    renderKpis(data);
    renderChart(data);
    renderReasons(data);
    renderReports(data);
    renderRecent(data);
  } catch (error) {
    dashboardError.textContent = "Kunne ikke hente tallene: " + error.message;
    dashboardError.hidden = false;
  }
}

weeksSelect.addEventListener("change", loadDashboard);

// a run that just ended changes the numbers
onStatus(function (state) {
  if (lastRunStatus === "running" && state.status !== "running") {
    loadDashboard();
  }
  lastRunStatus = state.status;
});

loadDashboard();
