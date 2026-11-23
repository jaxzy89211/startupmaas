"""Thin wrapper, kept so existing commands and imports keep working. The code lives in hedgewise/backtest.py.

    python backtest.py [--years 2021 2022] [--risk-aversion 0.5]

Imported as a module (`import backtest`), this file *is*
`hedgewise.backtest`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.backtest")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
