"""Bundled companion images: the tray icons (``*.ico``) and ``make_icon.py``, which regenerates them.

Nothing here is imported at runtime; :mod:`needledropper.tray` locates the icons by path
(``Path(tray.__file__).parent / "assets"``), which works from a source checkout and from a
PyInstaller bundle that maps this folder to ``needledropper/assets/``.
"""
