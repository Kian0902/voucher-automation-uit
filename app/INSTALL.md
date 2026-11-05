# Installing Fakturaassistent on a Mac

The built app is in the repo, so the caseworker's Mac needs no Python and no build.

1. On the caseworker's Mac, download the branch as a ZIP: https://github.com/Kian0902/voucher-automation-uit/archive/refs/heads/local-desktop-app.zip (or on GitHub: switch to the branch `local-desktop-app`, then Code, Download ZIP). Unzip it.
2. The folder `dist/` inside holds `Fakturaassistent.app` and an empty `data/koststed_tables/`. Move that `dist/` folder wherever you like, for example to `~/Fakturaassistent/`, and delete the rest of the unzipped repo.
3. Put the three report CSVs into `data/koststed_tables/` with their original names.
4. Start the app once: it writes `config.env` with empty values next to itself. Fill in `API_URL`, `API_MODEL`, `API_KEY` and `MISTRAL_API_KEY` there, or under Innstillinger in the app, then restart the app. `config.env` is never part of the repo, it holds the keys.
5. Open the app. macOS blocks an unsigned app from a download on the first try: open System Settings, Privacy & Security, scroll down and press "Open Anyway", then open the app again. On older macOS versions, right-click the app and choose Open.
6. The app creates `logs/` and `output/` next to itself on the first run.
7. Drag the app to the Dock if they want an icon there.
8. New report exports: upload them under Kunnskapsbase in the app, or replace the three CSVs and restart the app.
9. The Mac must be on the UiT network or VPN for the LLM calls, and online for Mistral when screenshots are used.

Building a new version, on the developer Mac from the repo root with the venv active: `pyinstaller --noconfirm app/Fakturaassistent.spec`, then commit `dist/Fakturaassistent.app` and push. To check a build without opening a window: `FAKTURAASSISTENT_SMOKE=1 dist/Fakturaassistent.app/Contents/MacOS/Fakturaassistent` prints the status and exits.
