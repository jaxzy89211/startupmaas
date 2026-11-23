"""Thin wrapper, kept so existing commands and imports keep working. The code lives in hedgewise/report.py.

    python report.py --company data/company_example.csv --lang nl

Imported as a module (`import report`), this file *is*
`hedgewise.report`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.report")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
