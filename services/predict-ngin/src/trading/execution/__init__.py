"""Execution package.

The package directory ``trading/execution/`` shadows the sibling module
``trading/execution.py`` (TradeBasedExecutionEngine, LOBExecutionEngine,
FillResult). Load that module's file here and re-export its public names so
``from trading.execution import TradeBasedExecutionEngine`` keeps working
alongside ``trading.execution.clob_simulator``.
"""

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

_parent_pkg = __name__.rsplit(".", 1)[0]
_legacy_name = f"{_parent_pkg}._execution_module"
_legacy = _sys.modules.get(_legacy_name)
if _legacy is None:
    _spec = _ilu.spec_from_file_location(
        _legacy_name, _Path(__file__).resolve().parent.parent / "execution.py"
    )
    _legacy = _ilu.module_from_spec(_spec)
    _legacy.__package__ = _parent_pkg
    _sys.modules[_legacy_name] = _legacy
    _spec.loader.exec_module(_legacy)

FillResult = _legacy.FillResult
TradeBasedExecutionEngine = _legacy.TradeBasedExecutionEngine
LOBExecutionEngine = _legacy.LOBExecutionEngine

__all__ = ["FillResult", "TradeBasedExecutionEngine", "LOBExecutionEngine"]
