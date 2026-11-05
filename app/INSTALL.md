# Installing Fakturaassistent

Two builds from the same code: a Mac app and a Windows program. The Windows part is at the end.

## Mac

The built app is in the repo, so the caseworker's Mac needs no Python and no build.

1. On the caseworker's Mac, download the repo as a ZIP: https://github.com/Kian0902/voucher-automation-uit/archive/refs/heads/main.zip (or on GitHub: Code, Download ZIP). Unzip it.
2. The folder `dist/` inside holds `Fakturaassistent.app` and an empty `data/koststed_tables/`. Move that `dist/` folder wherever you like, for example to `~/Fakturaassistent/`, and delete the rest of the unzipped repo.
3. Put the three report CSVs into `data/koststed_tables/` with their original names.
4. Start the app once: it writes `config.env` with empty values next to itself. Fill in `API_URL`, `API_MODEL`, `API_KEY` and `MISTRAL_API_KEY` there, or under Innstillinger in the app, then restart the app. `config.env` is never part of the repo, it holds the keys.
5. Open the app. macOS blocks an unsigned app from a download on the first try: open System Settings, Privacy & Security, scroll down and press "Open Anyway", then open the app again. On older macOS versions, right-click the app and choose Open.
6. The app creates `logs/` and `output/` next to itself on the first run.
7. Drag the app to the Dock if they want an icon there.
8. New report exports: upload them under Kunnskapsbase in the app, or replace the three CSVs and restart the app.
9. The Mac must be on the UiT network or VPN for the LLM calls, and online for Mistral when screenshots are used.

Building a new version, on the developer Mac from the repo root with the venv active: `pyinstaller --noconfirm app/Fakturaassistent-mac.spec`, then commit `dist/Fakturaassistent.app` and push. To check a build without opening a window: `FAKTURAASSISTENT_SMOKE=1 dist/Fakturaassistent.app/Contents/MacOS/Fakturaassistent` prints the status and exits.

## Windows

The Windows program is built by GitHub, not on the developer Mac: every push to `windows-build` or `main` runs the workflow "Windows build", which builds the program on a Windows machine, starts it once to check it, and offers it as a ZIP.

1. On GitHub, open Actions, the latest "Windows build" run, and download the artifact `Fakturaassistent-windows`. Give that ZIP to the caseworker, or download it on their PC.
2. Unzip it. The folder `Fakturaassistent` holds `Fakturaassistent.exe`, `LES MEG.txt` with these steps in Norwegian, and `data/koststed_tables/`. The folder can live anywhere, for example on the desktop.
3. Put the three report CSVs into `data/koststed_tables/` with their original names.
4. Double-click `Fakturaassistent.exe`. The first time, Windows may show "Windows protected your PC", since the program is unsigned: choose "More info", then "Run anyway". On a PC managed by IT the program may be blocked outright; then IT has to allow it.
5. The app opens in the default browser on `http://127.0.0.1:8765`. It has no window of its own. On the first start it writes `config.env` with empty values next to the .exe: fill in the settings under Innstillinger, save, press "Avslutt appen" at the bottom of the sidebar, and start the .exe again.
6. "Avslutt appen" ends the program. Closing the browser tab does not. Starting the .exe while it already runs just opens the browser on the running one.
7. If the program does not start, `Fakturaassistent.log` next to the .exe says why.

What differs from the Mac app: the launcher (`app/launcher_windows.py`, browser instead of a native window) and the build recipe (`app/Fakturaassistent-windows.spec`). Everything else is the same code. `scripts/check_build.py dist/Fakturaassistent` is the check the workflow runs on the built program.
