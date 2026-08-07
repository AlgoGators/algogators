"""The "Methodology" stage: scaffold a strategy file the user fills in themselves.

No AI/LLM generation happens here — this is boilerplate templating only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from algoterminal.research.models import Hypothesis
from algoterminal.research.storage import ResearchRecord

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "strategy_template.py.txt"


def scaffold_strategy(record: ResearchRecord, hypothesis: Hypothesis) -> Path:
    """Write a strategy.py scaffold into the research record directory."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    content = template.format(
        title=hypothesis.title,
        thesis=hypothesis.thesis,
        universe=hypothesis.universe,
        symbols=", ".join(hypothesis.symbols),
        expected_edge=hypothesis.expected_edge,
    )
    record.strategy_path.write_text(content, encoding="utf-8")
    return record.strategy_path


def load_strategy_module(strategy_path: Path) -> ModuleType:
    """Dynamically import a scaffolded (and user-edited) strategy.py file."""
    spec = importlib.util.spec_from_file_location(f"strategy_{strategy_path.parent.name}", strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load strategy module from {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for fn_name in ("generate_signals", "size_positions", "apply_risk_rules"):
        if not hasattr(module, fn_name):
            raise AttributeError(f"strategy file {strategy_path} is missing required function: {fn_name}")

    return module
