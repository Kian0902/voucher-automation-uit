# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Mac app.

Build from the repo root with the venv active:
    pyinstaller --noconfirm app/Fakturaassistent.spec
The result is dist/Fakturaassistent.app. config.env, data/, logs/ and output/ live next to the
.app, never inside it, so a new report export or a key change needs no rebuild.
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
    [str(spec_dir / "launcher.py")],
    pathex=[str(spec_dir), str(src_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fakturaassistent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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

app = BUNDLE(
    coll,
    name="Fakturaassistent.app",
    icon=None,
    bundle_identifier="no.uit.fakturaassistent",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
)
