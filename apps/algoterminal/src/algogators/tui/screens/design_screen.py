"""Design tab: pick a strategy on the left, describe what it should do at
the top right, and Claude Code writes it into that strategy's strategy.py.
The code itself is also directly editable below.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Static, TextArea

from algogators.research.design_agent import run_claude_edit
from algogators.research.methodology import scaffold_strategy
from algogators.research.storage import ResearchRecord, list_slugs, list_versions
from algogators.theme import ORANGE

_NO_SELECTION_MESSAGE = "Select a strategy on the left."


class DesignPane(Horizontal):
    """Left: table of strategies. Right: prompt bar + editable strategy.py."""

    def __init__(self) -> None:
        super().__init__()
        self._records: list[ResearchRecord] = []
        self._selected: ResearchRecord | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="design-records-col"):
            yield DataTable(id="design-records-table", cursor_type="row")
            yield Static(id="design-detail")
        with Vertical(id="design-code-col"):
            with Horizontal(id="design-prompt-row"):
                yield Input(
                    placeholder="Describe what this strategy should do, then press Enter...",
                    id="design-prompt",
                )
                yield Button("Send to Claude", id="design-send", variant="primary")
            yield Static(id="design-status")
            yield TextArea.code_editor("", language="python", id="design-code")
            with Horizontal(id="design-code-buttons"):
                yield Button("Save", id="design-save")
                yield Button("Reload from disk", id="design-reload")

    def on_mount(self) -> None:
        table = self.query_one("#design-records-table", DataTable)
        table.add_columns("Slug", "Version", "Title")
        self.refresh_records()
        self._set_editing_enabled(False)

    def refresh_records(self) -> None:
        self._records = []
        table = self.query_one("#design-records-table", DataTable)
        table.clear()
        for slug in list_slugs():
            for record in list_versions(slug):
                try:
                    title = record.load_hypothesis().title
                except Exception:
                    title = "(unreadable)"
                self._records.append(record)
                table.add_row(record.slug, record.version, title)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "design-records-table":
            return
        row_index = event.cursor_row
        if 0 <= row_index < len(self._records):
            self._selected = self._records[row_index]
            self._render_detail()
            self._load_code()
            self._set_editing_enabled(True)

    def _render_detail(self) -> None:
        detail = self.query_one("#design-detail", Static)
        record = self._selected
        if record is None:
            detail.update("No strategy selected.")
            return
        try:
            hyp = record.load_hypothesis()
        except Exception:
            detail.update(f"`{record.slug}/{record.version}`\n\n[dim]Could not read hypothesis.yaml[/dim]")
            return
        detail.update(
            f"[bold {ORANGE}]{hyp.title}[/]\n"
            f"`{record.slug}/{record.version}`\n\n"
            f"{hyp.thesis}\n\n"
            f"Universe: {hyp.universe} ({', '.join(hyp.symbols)})\n"
            f"Expected edge: {hyp.expected_edge}"
        )

    def _load_code(self) -> None:
        code = self.query_one("#design-code", TextArea)
        record = self._selected
        if record is None:
            code.text = ""
            return
        if record.strategy_path.exists():
            code.text = record.strategy_path.read_text(encoding="utf-8")
        else:
            code.text = (
                "# No strategy.py yet for this record.\n"
                "# Describe what it should do above and send it to Claude,\n"
                "# or press Save to scaffold the three required functions here first.\n"
            )

    def _set_editing_enabled(self, enabled: bool) -> None:
        for widget_id in ("#design-prompt", "#design-send", "#design-code", "#design-save", "#design-reload"):
            self.query_one(widget_id).disabled = not enabled

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "design-send":
            self._send_to_claude()
        elif event.button.id == "design-save":
            self._save_code()
        elif event.button.id == "design-reload":
            self._load_code()
            self.notify("Reloaded from disk.")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "design-prompt":
            self._send_to_claude()

    def _send_to_claude(self) -> None:
        if self._selected is None:
            self.notify(_NO_SELECTION_MESSAGE, severity="warning")
            return
        prompt_input = self.query_one("#design-prompt", Input)
        instruction = prompt_input.value.strip()
        if not instruction:
            self.notify("Type what you want the strategy to do first.", severity="warning")
            return

        record = self._selected
        if not record.strategy_path.exists():
            scaffold_strategy(record, record.load_hypothesis())
            self._load_code()

        status = self.query_one("#design-status", Static)
        status.update("[dim]Claude is writing the strategy...[/dim]")
        self._busy(True)
        self.notify(f"Sending instruction to Claude for {record.slug}/{record.version}...")
        self._run_claude_worker(record, instruction)

    def _busy(self, busy: bool) -> None:
        self.query_one("#design-prompt", Input).disabled = busy
        self.query_one("#design-send", Button).disabled = busy

    @work(exclusive=True, thread=True)
    def _run_claude_worker(self, record: ResearchRecord, instruction: str) -> None:
        ok, message = run_claude_edit(record, instruction)
        self.app.call_from_thread(self._after_claude, record, ok, message)

    def _after_claude(self, record: ResearchRecord, ok: bool, message: str) -> None:
        self._busy(False)
        status = self.query_one("#design-status", Static)
        color = "green" if ok else "red"
        status.update(f"[{color}]{message}[/]")
        if ok:
            self.query_one("#design-prompt", Input).value = ""
        if record is self._selected:
            self._load_code()
        self.refresh_records()
        self.notify("Claude finished." if ok else "Claude failed — see status below the prompt.", severity="information" if ok else "error")

    def _save_code(self) -> None:
        if self._selected is None:
            self.notify(_NO_SELECTION_MESSAGE, severity="warning")
            return
        code = self.query_one("#design-code", TextArea)
        self._selected.strategy_path.write_text(code.text, encoding="utf-8")
        self.notify(f"Saved {self._selected.strategy_path}.")
