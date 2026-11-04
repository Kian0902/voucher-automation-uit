# Installing Fakturaassistent on a Mac

1. Build on your own Mac, from the repo root with the venv active: `pyinstaller --noconfirm app/Fakturaassistent.spec`. The app is `dist/Fakturaassistent.app`.
2. Make a folder on the caseworker's Mac, for example `~/Fakturaassistent/`, and copy `Fakturaassistent.app` into it.
3. In that folder, create `config.env` with `API_URL`, `API_KEY`, `API_MODEL` and `MISTRAL_API_KEY`, one `NAME="value"` per line, like `.env.example`.
4. In that folder, create `data/koststed_tables/` and put the three report CSVs there with their original names.
5. Double-click the app. The first start may show a Gatekeeper warning, since the app is unsigned: right-click, Open, Open.
6. The app creates `logs/` and `output/` in the folder on the first run.
7. Drag the app to the Dock if they want an icon there.
8. New report exports: replace the three CSVs and restart the app.
9. The Mac must be on the UiT network or VPN for the LLM calls, and online for Mistral when screenshots are used.
10. To check a build without opening a window: `FAKTURAASSISTENT_SMOKE=1 dist/Fakturaassistent.app/Contents/MacOS/Fakturaassistent` prints the status and exits.
