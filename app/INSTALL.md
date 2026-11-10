# Installing Fakturaassistent

Two builds from the same code: a Mac app and a Windows program. The Windows part is at the end.

## Mac

The built app is in the repo, so the caseworker's Mac needs no Python and no build.

1. On the caseworker's Mac, download the repo as a ZIP: https://github.com/Kian0902/voucher-automation-uit/archive/refs/heads/main.zip (or on GitHub: Code, Download ZIP). Unzip it.
2. The folder `dist/` inside holds `Fakturaassistent.app` and an empty `data/koststed_tables/`. Move that `dist/` folder wherever you like, for example to `~/Fakturaassistent/`, and delete the rest of the unzipped repo.
3. Open the app. It starts on the setup page: fill in the model server address, the model and the two keys, choose the three report CSVs, and press "Fullfør oppsettet". That writes `config.env` next to the app (never part of the repo, it holds the keys) and puts the reports in `data/koststed_tables/`. Everything can be changed later under Innstillinger and Kunnskapsbase, with no restart.
4. Opening it the first time: macOS blocks an unsigned app from a download on the first try: open System Settings, Privacy & Security, scroll down and press "Open Anyway", then open the app again. On older macOS versions, right-click the app and choose Open.
5. The app creates `logs/` and `output/` next to itself on the first run.
6. Drag the app to the Dock if they want an icon there.
7. New report exports: upload them under Kunnskapsbase in the app.
8. The Mac must be on the UiT network or VPN for the LLM calls, and online for Mistral when screenshots are used.

Building a new version, on the developer Mac from the repo root with the venv active: `pyinstaller --noconfirm app/Fakturaassistent-mac.spec`, then commit `dist/Fakturaassistent.app` and push. To check a build without opening a window: `FAKTURAASSISTENT_SMOKE=1 dist/Fakturaassistent.app/Contents/MacOS/Fakturaassistent` prints the status and exits.

## Windows

The Windows program is built by GitHub, not on the developer Mac: every push to `windows-build` or `main` runs the workflow "Windows build", which builds the program on a Windows machine, starts it once to check it, and offers it as a ZIP.

1. On GitHub, open Actions, the latest "Windows build" run, and download the artifact `Fakturaassistent-windows`. Give that ZIP to the caseworker, or download it on their PC.
2. Unzip it. The folder `Fakturaassistent` holds `Fakturaassistent.exe`, `README.txt` with these steps in Norwegian, and `data/koststed_tables/`. The folder can live anywhere, for example on the desktop.
3. Double-click `Fakturaassistent.exe`. The first time, Windows may show "Windows protected your PC", since the program is unsigned: choose "More info", then "Run anyway". On a PC managed by IT the program may be blocked outright; then IT has to allow it.
4. The app opens in the default browser on `http://127.0.0.1:8765`, on the setup page: the model server address, the model, the two keys and the three report CSVs, then "Fullfør oppsettet". It has no window of its own.
5. "Avslutt appen" ends the program. Closing the browser tab does not. Starting the .exe while it already runs just opens the browser on the running one.
6. If the program does not start, `Fakturaassistent.log` next to the .exe says why.

What differs from the Mac app: the launcher (`app/launcher_windows.py`, browser instead of a native window) and the build recipe (`app/Fakturaassistent-windows.spec`). Everything else is the same code. `scripts/check_build.py dist/Fakturaassistent` is the check the workflow runs on the built program.
