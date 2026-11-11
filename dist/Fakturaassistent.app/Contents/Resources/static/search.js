// The search panel beside the drop zone, on the agents page and the batch page: one IO,
// koststed or person name looked up in the reports, with a copy button on every value.

document.getElementById("search-form").addEventListener("submit", async function (event) {
  event.preventDefault();
  var query = document.getElementById("search-input").value.trim();
  if (query === "") {
    return;
  }
  var resultBox = document.getElementById("search-result");
  resultBox.hidden = false;
  resultBox.innerHTML = "<em>søker ...</em>";
  try {
    var response = await fetch("/search?query=" + encodeURIComponent(query));
    var data = await response.json();
    resultBox.innerHTML = renderSearch(data);
  } catch (error) {
    resultBox.innerHTML = "Søket feilet";
  }
});

function renderPersons(persons) {
  var items = [];
  for (var i = 0; i < persons.length; i++) {
    var person = persons[i];
    items.push(
      "<li><strong>" + esc(person.name) + "</strong><br>"
      + esc(person.koststed) + " " + esc(person.koststed_name) + copyButton(person.koststed) + "</li>"
    );
  }
  return '<ul class="person-list">' + items.join("") + "</ul>";
}

function renderSearch(data) {
  if (data.kind === "io") {
    var io = data.result;
    var parts = ['<div class="search-kind">IO ' + esc(data.query) + "</div>"];
    if (io.status === "found") {
      parts.push('<div class="search-main">' + esc(io.koststed) + copyButton(io.koststed) + "</div>");
      parts.push('<div class="search-sub">' + esc(io.koststed_name) + "</div>");
    } else if (io.status === "found_unknown_koststed") {
      parts.push('<div class="search-sub">IO-en finnes, men rapport B mangler koststedet</div>');
    } else {
      parts.push('<div class="search-sub">Ikke i rapport B, ukjent for Unit4</div>');
    }
    return parts.join("");
  }

  if (data.kind === "koststed") {
    var parts = ['<div class="search-kind">Koststed ' + esc(data.query) + "</div>"];
    if (data.catalog.status === "found") {
      parts.push('<div class="search-main">' + esc(data.catalog.description) + "</div>");
    } else {
      parts.push('<div class="search-sub">Ikke i koststedskatalogen, kan være en utgått kode</div>');
    }
    if (data.ios.status === "found") {
      parts.push('<div class="search-sub">' + data.ios.count + " innkjøpsordrer på koststedet:</div>");
      var items = [];
      for (var i = 0; i < data.ios.ios.length; i++) {
        items.push("<li>" + esc(data.ios.ios[i]) + copyButton(data.ios.ios[i]) + "</li>");
      }
      parts.push('<ul class="io-list">' + items.join("") + "</ul>");
    } else {
      parts.push('<div class="search-sub">Ingen innkjøpsordrer registrert på koststedet</div>');
    }
    return parts.join("");
  }

  if (data.kind === "person") {
    var result = data.result;
    var parts = ['<div class="search-kind">Personsøk</div>'];
    if (result.match_type === "exact") {
      parts.push(renderPersons(result.persons));
    } else if (result.match_type === "partial") {
      parts.push('<div class="search-sub">Delvis treff:</div>');
      parts.push(renderPersons(result.persons));
    } else if (result.match_type === "partial_relaxed") {
      parts.push('<div class="search-sub">Delvis treff, ett ord matchet ikke:</div>');
      parts.push(renderPersons(result.persons));
    } else if (result.match_type === "too_many") {
      parts.push('<div class="search-sub">For mange treff (' + result.count + "), skriv mer av navnet</div>");
    } else {
      if (result.note && result.note.indexOf("two words") !== -1) {
        parts.push('<div class="search-sub">Ingen treff. Skriv minst to ord av navnet</div>');
      } else {
        parts.push('<div class="search-sub">Ingen treff i rapport A</div>');
      }
    }
    return parts.join("");
  }

  return '<div class="search-sub">' + esc(data.note) + "</div>";
}
