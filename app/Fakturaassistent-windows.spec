# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Windows program.

Built on Windows, by the GitHub workflow in .github/workflows/build-windows.yml:
    pyinstaller --noconfirm app/Fakturaassistent-windows.spec
The result is the folder dist/Fakturaassistent/ with Fakturaassistent.exe in it. config.env,
data/, logs/ and output/ live in that same folder, next to the .exe.

Same modules and data as the Mac spec (Fakturaassistent-mac.spec); what differs is the launcher,
which opens the browser instead of a native window, and the packaging.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


spec_dir = Path(SPECPATH)
repo_root = spec_dir.parent
src_dir = repo_root / "src"

# our own modules are imported after a runtime sys.path change the analysis cannot follow
own_modules = [
    "server", "api_dashboard", "api_knowledge", "api_settings", "api_batch", "main", "list_agent", "lookup_data", "lookup_tools", "normalize", "run_log",
    "utils", "prompts", "erp_list", "ocr_parser", "mistral_ocr", "xml_processor",
]

# packages that import their submodules lazily, which the analysis misses
hidden_imports = list(own_modules)
for package in ("langchain_core", "langchain_openai", "langgraph", "openai", "mistralai"):
    hidden_imports = hidden_imports + collect_submodules(package)

# the pages and the static files are read from APP_DIR at runtime, so they keep their folders
datas = [(str(spec_dir / "pages"), "pages"), (str(spec_dir / "static"), "static")]
# anyascii keeps its transliteration tables as data files
datas = datas + collect_data_files("anyascii")

a = Analysis(
    [str(spec_dir / "launcher_windows.py")],
    pathex=[str(spec_dir), str(src_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "webview"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# UTF-8 mode: every file the program reads or writes is UTF-8, whatever the Windows code page is
interpreter_options = [("X utf8", None, "OPTION")]

exe = EXE(
    pyz,
    a.scripts,
    interpreter_options,
    exclude_binaries=True,
    name="Fakturaassistent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Fakturaassistent",
)
