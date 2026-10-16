"""Punto de entrada del .exe (PyInstaller necesita un script, no un módulo)."""
import sys

from installer.gui import main

sys.exit(main())
