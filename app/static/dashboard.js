// The dashboard: the numbers from /dashboard as tiles, one SVG bar chart per week and three
// lists. Reloads when the period changes and when a run on the agent page finishes.

var weeksSelect = document.getElementById("weeks");
var dashboardError = document.getElementById("dashboard-error");
var lastRunStatus = null;

// the status colours of app.css; the legend in dashboard.html repeats them
var COLOR_RESOLVED = "#1e7f4f";
var COLOR_CONFIRM = "#b7791f";
var COLOR_UNRESOLVED = "#667585";
var KIND_TITLES = { liste: "Skjermbilder av listen", faktura: "Fakturaer (XML)" };
var KIND_UNITS = { liste: "rader", faktura: "fakturaer" };
var DONUT_RADIUS = 44;
var DONUT_GAP = 2;
var CHART_WIDTH = 800;
var CHART_HEIGHT = 280;
var MARGIN = { top: 24, right: 10, bottom: 40, left: 40 };

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

function kpi(label, value, sub, className) {
  return '<div class="kpi ' + (className || "") + '">'
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
    timeSub = "lister " + formatSeconds(data.time.liste.median) + ", fakturaer " + formatSeconds(data.time.faktura.median);
  }
  tiles.push(kpi("Tid per faktura (median)", formatSeconds(time.median), timeSub));

  // the three cost and health numbers sit in a lighter row under the four main tiles
  tiles.push(kpi("tokens brukt", formatNumber(data.tokens), "per faktura: " + formatNumber(data.per_item.tokens), "secondary"));
  var lookupsPerItem = "-";
  if (data.per_item.lookups !== null) {
    lookupsPerItem = String(data.per_item.lookups).replace(".", ",");
  }
  tiles.push(kpi("oppslag i rapportene", formatNumber(data.lookups), "per faktura: " + lookupsPerItem, "secondary"));
  tiles.push(kpi("feilede kjøringer", formatNumber(totals.failed_runs), "av " + totals.runs + " kjøringer", "secondary"));

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

var KIND_ORDER = ["liste", "faktura"];
var KIND_LETTERS = { liste: "L", faktura: "F" };

// the weeks: two bars per week, lists left and invoices right, each stacked by result
function renderChart(data) {
  var weeks = data.per_week;
  var plotWidth = CHART_WIDTH - MARGIN.left - MARGIN.right;
  var plotHeight = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;
  var slot = plotWidth / weeks.length;
  var barGap = Math.min(6, slot * 0.08);
  var barWidth = Math.min((slot * 0.72 - barGap) / 2, 34);
  var groupWidth = barWidth * 2 + barGap;
  // week labels only every n-th week, so that they never overlap on long periods
  var labelEvery = Math.max(1, Math.ceil(44 / slot));

  var maxItems = 0;
  for (var i = 0; i < weeks.length; i++) {
    for (var k = 0; k < KIND_ORDER.length; k++) {
      var kindItems = weeks[i][KIND_ORDER[k]].items;
      if (kindItems > maxItems) {
        maxItems = kindItems;
      }
    }
  }
  var top = axisTop(maxItems);
  var scale = plotHeight / top;
  var baseline = MARGIN.top + plotHeight;

  var parts = ['<svg viewBox="0 0 ' + CHART_WIDTH + " " + CHART_HEIGHT
    + '" role="img" aria-label="Behandlet per uke, skjermbilder og fakturaer hver for seg">'];

  // four gridlines with their values
  for (var g = 0; g <= 4; g++) {
    var value = top * g / 4;
    var y = MARGIN.top + plotHeight - value * scale;
    parts.push('<line x1="' + MARGIN.left + '" x2="' + (CHART_WIDTH - MARGIN.right) + '" y1="' + y + '" y2="' + y
      + '" stroke="#e6ebf0" stroke-width="1"/>');
    parts.push('<text x="' + (MARGIN.left - 8) + '" y="' + (y + 4) + '" text-anchor="end" font-size="11" fill="#5b6b7a">'
      + value + "</text>");
  }

  for (var w = 0; w < weeks.length; w++) {
    var week = weeks[w];
    var groupLeft = MARGIN.left + slot * w + (slot - groupWidth) / 2;
    var dateText = formatDate(week.week_start);

    for (var b = 0; b < KIND_ORDER.length; b++) {
      var kind = KIND_ORDER[b];
      var counts = week[kind];
      var x = groupLeft + b * (barWidth + barGap);
      var confirm = counts["må bekreftes"] + counts["flere funnet"];
      var segments = [
        { value: counts["løst"], color: COLOR_RESOLVED },
        { value: confirm, color: COLOR_CONFIRM },
        { value: counts["uløst"], color: COLOR_UNRESOLVED },
      ];
      var title = dateText + ", " + KIND_TITLES[kind] + ": " + counts.items + " behandlet, " + counts["løst"]
        + " løst, " + confirm + " må bekreftes, " + counts["uløst"] + " uløst";
      parts.push("<g><title>" + esc(title) + "</title>");
      var stackTop = baseline;
      for (var s = 0; s < segments.length; s++) {
        var height = segments[s].value * scale;
        if (height > 0) {
          stackTop = stackTop - height;
          // a sliver of background between two segments
          var gapBelow = 0;
          if (s > 0 && height > 3) {
            gapBelow = 2;
          }
          parts.push('<rect x="' + x + '" y="' + stackTop + '" width="' + barWidth + '" height="' + (height - gapBelow)
            + '" fill="' + segments[s].color + '"/>');
        }
      }
      if (counts.items > 0 && barWidth >= 12) {
        parts.push('<text x="' + (x + barWidth / 2) + '" y="' + (stackTop - 4)
          + '" text-anchor="middle" font-size="10" font-weight="600" fill="#10243a">' + counts.items + "</text>");
      }
      if (week.items > 0 && barWidth >= 10) {
        parts.push('<text x="' + (x + barWidth / 2) + '" y="' + (baseline + 12)
          + '" text-anchor="middle" font-size="10" fill="#5b6b7a">' + KIND_LETTERS[kind] + "</text>");
      }
      parts.push("</g>");
    }

    if (w % labelEvery === 0) {
      parts.push('<text x="' + (groupLeft + groupWidth / 2) + '" y="' + (baseline + 27)
        + '" text-anchor="middle" font-size="11" fill="#5b6b7a">' + esc(dateText.slice(0, 5)) + "</text>");
    }
  }

  parts.push("</svg>");
  document.getElementById("chart").innerHTML = parts.join("");
}

// one ring per input type: the share of løst in the middle, the three counts beside it
function donut(kind, counts) {
  var confirm = counts["må bekreftes"] + counts["flere funnet"];
  var segments = [
    { label: "Løst", value: counts["løst"], color: COLOR_RESOLVED },
    { label: "Må bekreftes", value: confirm, color: COLOR_CONFIRM },
    { label: "Uløst", value: counts["uløst"], color: COLOR_UNRESOLVED },
  ];
  var circumference = 2 * Math.PI * DONUT_RADIUS;
  var filledSegments = 0;
  for (var i = 0; i < segments.length; i++) {
    if (segments[i].value > 0) {
      filledSegments = filledSegments + 1;
    }
  }
  // a small gap of background between the segments, none when there is only one
  var gap = 0;
  if (filledSegments > 1) {
    gap = DONUT_GAP;
  }
  var rate = "-";
  if (counts.items > 0) {
    rate = Math.round(100 * counts["løst"] / counts.items) + " %";
  }
  var summary = KIND_TITLES[kind] + ": " + counts["løst"] + " løst, " + confirm + " må bekreftes, "
    + counts["uløst"] + " uløst";

  var parts = ['<svg viewBox="0 0 120 120" role="img" aria-label="' + escAttr(summary) + '">'];
  parts.push('<circle cx="60" cy="60" r="' + DONUT_RADIUS + '" fill="none" stroke="#e1e6ec" stroke-width="14"/>');
  var start = 0;
  for (var s = 0; s < segments.length; s++) {
    var segment = segments[s];
    if (segment.value === 0 || counts.items === 0) {
      continue;
    }
    var length = circumference * segment.value / counts.items;
    var dash = Math.max(length - gap, 0);
    parts.push('<circle cx="60" cy="60" r="' + DONUT_RADIUS + '" fill="none" stroke="' + segment.color + '" stroke-width="14"'
      + ' stroke-dasharray="' + dash + " " + (circumference - dash) + '" stroke-dashoffset="' + (-start) + '"'
      + ' transform="rotate(-90 60 60)"><title>' + esc(segment.label + ": " + segment.value) + "</title></circle>");
    start = start + length;
  }
  parts.push('<text x="60" y="58" text-anchor="middle" font-size="22" font-weight="650" fill="#10243a">' + esc(rate) + "</text>");
  parts.push('<text x="60" y="76" text-anchor="middle" font-size="11" fill="#5b6b7a">løst</text>');
  parts.push("</svg>");

  var facts = [];
  for (var f = 0; f < segments.length; f++) {
    facts.push('<li><span class="swatch" style="background: ' + segments[f].color + '"></span>' + esc(segments[f].label)
      + "<strong>" + formatNumber(segments[f].value) + "</strong></li>");
  }
  var sub = formatNumber(counts.items) + " " + KIND_UNITS[kind] + " i " + counts.runs + " kjøringer";
  if (counts.items === 0) {
    sub = "Ingen kjøringer i perioden";
  }
  return '<div class="donut-card"><div class="donut">' + parts.join("") + "</div>"
    + '<div class="donut-facts"><div class="donut-title">' + esc(KIND_TITLES[kind]) + "</div>"
    + '<div class="donut-sub">' + esc(sub) + "</div>"
    + '<ul class="donut-legend">' + facts.join("") + "</ul></div></div>";
}

function renderByKind(data) {
  document.getElementById("by-kind").innerHTML = donut("liste", data.by_kind.liste) + donut("faktura", data.by_kind.faktura);
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
      + '<td class="num">' + esc(rowCount) + "</td><td>" + esc(modified) + "</td></tr>");
  }
  document.getElementById("reports").innerHTML = rows.join("");
}

function resultText(run) {
  if (run.status === "error") {
    return '<span class="badge badge-open">Feilet</span><div class="small">' + esc(run.error) + "</div>";
  }
  if (run.status === "stopped") {
    return '<span class="badge badge-confirm">Stoppet</span><div class="small">' + run.items + " behandlet før stopp</div>";
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
        + '<td class="num">' + esc(formatSeconds(run.duration_seconds)) + "</td>"
        + '<td class="num">' + esc(formatNumber(run.tokens)) + "</td>"
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
    renderByKind(data);
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
