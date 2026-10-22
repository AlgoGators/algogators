"""
Tests for CLI utilities.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.tools.cli_utils import (
    get_console,
    print_table,
    print_panel,
    create_progress_context,
    prompt_user,
    confirm_user,
    format_currency,
    format_percentage,
    format_number,
)


def test_get_console():
    """Test console retrieval."""
    console = get_console()
    # Should return None or Console instance
    assert console is None or hasattr(console, "print")


def test_print_table_basic(capsys):
    """Test basic table printing."""
    data = [
        ["Alice", 25, "Engineer"],
        ["Bob", 30, "Designer"],
        ["Charlie", 35, "Manager"],
    ]
    headers = ["Name", "Age", "Role"]
    
    print_table(data, headers, title="Team Members")
    
    captured = capsys.readouterr()
    assert "Team Members" in captured.out or "Name" in captured.out


def test_print_table_empty(capsys):
    """Test table printing with empty data."""
    print_table([], ["Col1", "Col2"])
    
    captured = capsys.readouterr()
    # Should not crash
    assert True


def test_print_panel_basic(capsys):
    """Test panel printing."""
    content = "This is test content"
    print_panel(content, title="Test Panel")
    
    captured = capsys.readouterr()
    assert "test content" in captured.out.lower() or "Test Panel" in captured.out


def test_create_progress_context():
    """Test progress context creation."""
    context = create_progress_context(total=100, description="Processing")
    
    # Should be a context manager
    assert hasattr(context, "__enter__")
    assert hasattr(context, "__exit__")


def test_format_currency():
    """Test currency formatting."""
    assert format_currency(1000.50) == "$1,000.50"
    assert format_currency(-500.25) == "-$500.25"
    assert format_currency(0) == "$0.00"
    assert format_currency(1234567.89) == "$1,234,567.89"


def test_format_currency_custom():
    """Test currency formatting with custom currency."""
    assert format_currency(1000.50, currency="€") == "€1,000.50"
    assert format_currency(500.25, currency="£") == "£500.25"


def test_format_percentage():
    """Test percentage formatting."""
    assert format_percentage(0.5) == "50.00%"
    assert format_percentage(0.1234) == "12.34%"
    assert format_percentage(1.0) == "100.00%"
    assert format_percentage(0.001) == "0.10%"


def test_format_percentage_decimals():
    """Test percentage formatting with custom decimals."""
    assert format_percentage(0.1234, decimals=1) == "12.3%"
    assert format_percentage(0.1234, decimals=3) == "12.340%"


def test_format_number():
    """Test number formatting."""
    assert format_number(1000.50) == "1,000.50"
    assert format_number(1234567.89) == "1,234,567.89"
    assert format_number(0) == "0.00"


def test_format_number_decimals():
    """Test number formatting with custom decimals."""
    assert format_number(1000.5, decimals=0) == "1,000"  # Truncates, doesn't round
    assert format_number(1000.567, decimals=3) == "1,000.567"


@patch("builtins.input", return_value="test_input")
def test_prompt_user_basic(mock_input, capsys):
    """Test basic user prompt."""
    result = prompt_user("Enter value:")
    
    assert result == "test_input"
    mock_input.assert_called_once()


@patch("builtins.input", return_value="")
def test_prompt_user_default(mock_input):
    """Test user prompt with default value."""
    result = prompt_user("Enter value:", default="default_value")
    
    assert result == "default_value"


@patch("builtins.input", side_effect=["invalid", "option_a"])
def test_prompt_user_choices(mock_input):
    """Test user prompt with choices."""
    result = prompt_user(
        "Choose option:",
        choices=["option_a", "option_b"],
    )
    
    assert result == "option_a"
    assert mock_input.call_count == 2


@patch("builtins.input", return_value="y")
def test_confirm_user_yes(mock_input):
    """Test confirmation with yes."""
    result = confirm_user("Continue?")
    
    assert result is True


@patch("builtins.input", return_value="n")
def test_confirm_user_no(mock_input):
    """Test confirmation with no."""
    result = confirm_user("Continue?")
    
    assert result is False


@patch("builtins.input", return_value="")
def test_confirm_user_default(mock_input):
    """Test confirmation with default."""
    result = confirm_user("Continue?", default=True)
    
    assert result is True
    
    result = confirm_user("Continue?", default=False)
    
    assert result is False


@patch("builtins.input", side_effect=["invalid", "maybe", "y"])
def test_confirm_user_invalid_input(mock_input):
    """Test confirmation handles invalid input."""
    result = confirm_user("Continue?")
    
    assert result is True
    assert mock_input.call_count == 3
