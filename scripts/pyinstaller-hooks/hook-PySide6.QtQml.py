"""Keep Qt's binary dependencies, not the entire unused QML module tree."""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
