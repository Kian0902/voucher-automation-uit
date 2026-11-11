// The settings page: the config file's values in a form, saved back to the file and taken
// into use at once; the model list fetched from the server on request; the folders the app uses.

var KEYS = ["MODEL_SERVER", "API_URL", "API_MODEL", "API_KEY", "GATEWAY_API_URL", "GATEWAY_API_MODEL",
  "GATEWAY_API_KEY", "MISTRAL_API_KEY", "USER_NAME"];

var introLine = document.getElementById("intro");
var errorBanner = document.getElementById("settings-error");
var modelsButton = document.getElementById("models-button");
var modelsList = document.getElementById("models-list");
var modelsNote = document.getElementById("models-note");
var saveButton = document.getElementById("save-button");
var messageLine = document.getElementById("message");
var foldersBody = document.getElementById("folders");
var aboutLine = document.getElementById("about");

function fieldFor(key) {
  return document.getElementById(key);
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

function render(state) {
  var intro = "Innstillingene ligger i " + state.config_file + ". Endringer tas i bruk med en gang.";
  if (!state.config_exists) {
    intro = intro + " Filen finnes ikke ennå og lages når du lagrer.";
  }
  introLine.textContent = intro;

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

  var rows = [];
  for (var j = 0; j < state.folders.length; j++) {
    var folder = state.folders[j];
    var badge = '<span class="badge badge-ok">finnes</span>';
    if (!folder.exists) {
      badge = '<span class="badge badge-open">mangler</span>';
    }
    rows.push("<tr><td>" + esc(folder.label) + '</td><td class="path">' + esc(folder.path) + "</td><td>" + badge + "</td></tr>");
  }
  foldersBody.innerHTML = rows.join("");

  var about = "Python " + state.about.python;
  if (state.about.packaged) {
    about = about + ", pakket app";
  } else {
    about = about + ", kjører fra kildekoden";
  }
  about = about + ", startet " + state.about.started.replace("T", " ");
  aboutLine.textContent = about;
}

// the block of the server that is not chosen is dimmed, so the eye goes to the one in use
function markChosenServer() {
  var chosen = fieldFor("MODEL_SERVER").value;
  document.getElementById("server-maggie").classList.toggle("unchosen", chosen !== "maggie");
  document.getElementById("server-gateway").classList.toggle("unchosen", chosen !== "gateway");
}

fieldFor("MODEL_SERVER").addEventListener("change", markChosenServer);

async function load() {
  try {
    var state = await fetchJson("/settings/state");
    render(state);
    markChosenServer();
    clearError();
  } catch (error) {
    showError(error.message);
  }
}

saveButton.addEventListener("click", async function () {
  var values = {};
  for (var i = 0; i < KEYS.length; i++) {
    values[KEYS[i]] = fieldFor(KEYS[i]).value;
  }
  clearError();
  saveButton.disabled = true;
  try {
    var data = await postJson("/settings/save", { values: values });
    render(data.state);
    showMessage(data.message);
  } catch (error) {
    clearMessage();
    showError(error.message);
  }
  saveButton.disabled = false;
});

modelsButton.addEventListener("click", async function () {
  clearError();
  modelsButton.disabled = true;
  modelsNote.textContent = "Spør serveren ...";
  modelsNote.hidden = false;
  try {
    var data = await postJson("/settings/models", {
      url: fieldFor("API_URL").value,
      key: fieldFor("API_KEY").value,
    });
    var options = ['<option value="">Velg en modell</option>'];
    for (var i = 0; i < data.models.length; i++) {
      options.push('<option value="' + esc(data.models[i]) + '">' + esc(data.models[i]) + "</option>");
    }
    modelsList.innerHTML = options.join("");
    modelsList.hidden = data.models.length === 0;
    if (data.models.length === 0) {
      modelsNote.textContent = "Serveren svarte, men lister ingen modeller.";
    } else {
      modelsNote.textContent = data.models.length + " modeller på serveren. Velg en for å sette den i feltet, og lagre.";
    }
  } catch (error) {
    modelsNote.hidden = true;
    showError(error.message);
  }
  modelsButton.disabled = false;
});

modelsList.addEventListener("change", function () {
  if (modelsList.value !== "") {
    fieldFor("API_MODEL").value = modelsList.value;
  }
});

load();
