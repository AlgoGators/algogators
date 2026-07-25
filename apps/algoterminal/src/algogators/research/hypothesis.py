"""The "Hypothesis" stage: a guided wizard that captures a structured
hypothesis object and saves it as a new, versioned research record.
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt

from algogators.data.provider import AssetClass
from algogators.data.universe import UniverseStore
from algogators.research.models import Hypothesis
from algogators.research.storage import ResearchRecord, create_record


def run_hypothesis_wizard(console: Console | None = None) -> tuple[Hypothesis, ResearchRecord]:
    """Interactive CLI form. Captures thesis, target universe, expected edge,
    and risk notes, then persists a new versioned research record.
    """
    if console is None:
        from algogators.console import console as default_console

        console = default_console
    store = UniverseStore()

    console.print("\n[brand]New Research Hypothesis[/brand]\n")

    title = Prompt.ask("Title")
    thesis = Prompt.ask("Thesis (what do you believe, and why)")

    known = [u.name for u in store.list()]
    console.print(f"Known universes: {', '.join(known)}")
    universe = Prompt.ask("Universe (name, or comma-separated symbols)")
    symbols = store.resolve(universe)

    asset_class_value = Prompt.ask(
        "Asset class",
        choices=[c.value for c in AssetClass],
        default=AssetClass.EQUITY.value,
    )
    expected_edge = Prompt.ask("Expected edge (why should this be profitable)")
    risk_notes = Prompt.ask("Risk notes", default="")
    author = Prompt.ask("Author", default="")

    hypothesis = Hypothesis(
        title=title,
        thesis=thesis,
        universe=universe,
        symbols=symbols,
        expected_edge=expected_edge,
        asset_class=AssetClass(asset_class_value),
        risk_notes=risk_notes,
        author=author,
    )
    record = create_record(hypothesis)
    console.print(f"\n[success]Saved research record:[/success] {record.slug}/{record.version}")
    return hypothesis, record
