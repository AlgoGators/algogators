"""
CLI Utilities for Enhanced Terminal Output

Provides rich formatting, progress bars, and interactive features.
"""

from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich.panel import Panel
    from rich import box
    from rich.prompt import Prompt, Confirm
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    # Fallback to basic print
    Console = None
    Table = None
    Progress = None
    Panel = None
    Prompt = None
    Confirm = None


def get_console():
    """Get rich Console instance or None."""
    if RICH_AVAILABLE:
        return Console()
    return None


def print_table(
    data: list,
    headers: list,
    title: Optional[str] = None,
    show_lines: bool = False,
) -> None:
    """Print data as a formatted table."""
    console = get_console()
    
    if console and RICH_AVAILABLE:
        table = Table(title=title, box=box.ROUNDED, show_lines=show_lines)
        for header in headers:
            table.add_column(header)
        
        for row in data:
            table.add_row(*[str(v) for v in row])
        
        console.print(table)
    else:
        # Plain text fallback
        if title:
            print(f"\n{title}")
            print("=" * len(title))
        
        # Print headers
        print(" | ".join(headers))
        print("-" * (sum(len(h) for h in headers) + 3 * (len(headers) - 1)))
        
        # Print rows
        for row in data:
            print(" | ".join(str(v) for v in row))


def print_panel(content: str, title: Optional[str] = None, style: str = "blue") -> None:
    """Print content in a panel."""
    console = get_console()
    
    if console and RICH_AVAILABLE:
        console.print(Panel(content, title=title, style=style))
    else:
        # Plain text fallback
        if title:
            print(f"\n{title}")
            print("=" * len(title))
        print(content)
        print()


def create_progress_context(total: Optional[int] = None, description: str = "Processing"):
    """Create a progress context manager."""
    if RICH_AVAILABLE and Progress:
        console = Console()
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        )
        return progress
    else:
        # Fallback context manager that does nothing
        class DummyContext:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def add_task(self, *args, **kwargs):
                pass
            def update(self, *args, **kwargs):
                pass
        
        return DummyContext()


def prompt_user(
    message: str,
    choices: Optional[list] = None,
    default: Optional[str] = None,
) -> str:
    """Prompt user for input."""
    if RICH_AVAILABLE and Prompt:
        if choices:
            return Prompt.ask(message, choices=choices, default=default)
        else:
            return Prompt.ask(message, default=default)
    else:
        # Plain text fallback
        prompt_msg = message
        if choices:
            prompt_msg += f" ({'/'.join(choices)})"
        if default:
            prompt_msg += f" [{default}]"
        prompt_msg += ": "
        
        while True:
            response = input(prompt_msg).strip()
            if not response and default:
                return default
            if not choices or response in choices:
                return response
            print(f"Invalid choice. Please choose from: {', '.join(choices)}")


def confirm_user(message: str, default: bool = True) -> bool:
    """Prompt user for yes/no confirmation."""
    if RICH_AVAILABLE and Confirm:
        return Confirm.ask(message, default=default)
    else:
        # Plain text fallback
        prompt_msg = message
        prompt_msg += " (y/n)" + (" [Y]" if default else " [n]") + ": "
        
        while True:
            response = input(prompt_msg).strip().lower()
            if not response:
                return default
            if response in ("y", "yes"):
                return True
            if response in ("n", "no"):
                return False
            print("Please enter 'y' or 'n'")


def format_currency(value: float, currency: str = "$") -> str:
    """Format value as currency."""
    if value >= 0:
        return f"{currency}{value:,.2f}"
    else:
        return f"-{currency}{abs(value):,.2f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """Format value as percentage."""
    return f"{value * 100:.{decimals}f}%"


def format_number(value: float, decimals: int = 2) -> str:
    """Format number with commas."""
    return f"{value:,.{decimals}f}"
