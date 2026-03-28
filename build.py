"""
Core Structure - Сборка в исполняемый файл

Скрипт для сборки приложения Core Structure в .exe файл.
Секреты НЕ вшиваются в бинарник — приложение загружает .env
через python-dotenv при старте (app/main.py → load_dotenv()).
Положите .env рядом с CoreStructure.exe при деплое.
"""
import subprocess
import sys
from pathlib import Path

# Генерируем spec файл
spec_content = """# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['app\\\\main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy', 'pytest', 'unittest',
        'test', 'tests', '_pytest', 'py.test', 'tkinter', 'IPython', 'jupyter',
        'PyQt5', 'PyQt6', 'wx', 'alabaster', 'sphinx', 'docutils', 'jinja2',
        'pygments', 'setuptools', 'pip', 'wheel',
        'PIL.ImageQt', 'pytz'
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CoreStructure',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"""

spec_file = Path("CoreStructure.spec")
spec_file.write_text(spec_content, encoding="utf-8")

print("[OK] Spec updated (no embedded secrets)")
print("[INFO] Положите .env рядом с CoreStructure.exe при деплое")
print("\nRunning PyInstaller...")

result = subprocess.run(
    [sys.executable, "-m", "PyInstaller", "CoreStructure.spec"],
    check=False,
)

if result.returncode != 0:
    print(f"\n[ERROR] PyInstaller завершился с кодом {result.returncode}")
    sys.exit(result.returncode)

print("\n[OK] Build complete: dist\\CoreStructure.exe")
