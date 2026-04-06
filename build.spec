# -*- mode: python ; coding: utf-8 -*-
import os
import sys

site_packages = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages')

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(site_packages, 'tkinterdnd2', 'tkdnd'), 'tkinterdnd2/tkdnd'),
        (os.path.join(site_packages, 'easyocr'), 'easyocr'),
    ],
    hiddenimports=[
        'tkinterdnd2',
        'easyocr',
        'PIL',
        'torch',
        'torchvision',
        'numpy',
        'cv2',
        'scipy',
        'skimage',
        'yaml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pyarrow', 'matplotlib', 'pandas'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='무좀사진분류기',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='toenail_classifier_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='무좀사진분류기',
)
