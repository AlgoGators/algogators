#!/usr/bin/env python3
"""
Trade Alert Bot (Telegram/Discord)

Watches trade log files (JSONL) and sends notifications for fills.

Supported sources:
- data/paper_trading_log.jsonl (order_filled, position_closed)
- data/execution_log.jsonl (execution records)
"""

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, List, Tuple

import requests


DEFAULT_LOGS = [
    "data/paper_trading_log.jsonl",
    "data/execution_log.jsonl",
]

DEFAULT_STATE_PATH = "data/trade_alert_state.json"


def _normalize_side(value: Optional[str]) -> str:
    if not value:
        return "UNKNOWN"
    if "." in value:
        value = value.split(".")[-1]
    return value.replace("OrderSide.", "").replace("OrderSide", "").upper()


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${value:,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{value:.4f}"
    except (TypeError, ValueError):
        return "n/a"


@dataclass
class DiscordNotifier:
    webhook_url: Optional[str] = None
    token: Optional[str] = None
    channel_id: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url or (self.token and self.channel_id))

    def send_message(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            if self.webhook_url:
                response = requests.post(
                    self.webhook_url,
                    json={"content": message},
                    timeout=10,
                )
                return response.status_code in (200, 204)

            url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
            headers = {
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
            }
            response = requests.post(url, headers=headers, json={"content": message}, timeout=10)
            return response.status_code in (200, 201)
        except requests.RequestException:
            return False


@dataclass
class TelegramNotifier:
    token: Optional[str] = None
    chat_id: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "disable_web_page_preview": True,
            }
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except requests.RequestException:
            return False


class AlertSender:
    def __init__(self, discord: DiscordNotifier, telegram: TelegramNotifier):
        self.discord = discord
        self.telegram = telegram

    @property
    def enabled(self) -> bool:
        return self.discord.enabled or self.telegram.enabled

    def send(self, message: str) -> None:
        if self.discord.enabled:
            self.discord.send_message(message)
        if self.telegram.enabled:
            self.telegram.send_message(message)


def _format_paper_order(event: Dict) -> str:
    side = _normalize_side(event.get("side"))
    size = event.get("size_usd") or event.get("filled_size") or event.get("filled_size_usd")
    price = event.get("filled_price")
    market_id = event.get("market_id", "unknown")
    order_id = event.get("order_id", "n/a")
    signal = event.get("signal_source", "n/a")
    notes = event.get("notes", "")
    timestamp = event.get("timestamp", "n/a")

    msg = [
        "Trade filled (PAPER)",
        f"Order: {order_id}",
        f"Side: {side}",
        f"Size: {_fmt_money(size)}",
        f"Price: {_fmt_price(price)}",
        f"Market: {market_id}",
        f"Signal: {signal}",
        f"Time: {timestamp}",
    ]
    if notes:
        msg.append(f"Notes: {notes}")
    return "\n".join(msg)


def _format_position_closed(event: Dict) -> str:
    position_id = event.get("position_id", "n/a")
    pnl = event.get("pnl")
    entry_price = event.get("entry_price")
    exit_price = event.get("exit_price")
    timestamp = event.get("timestamp", "n/a")

    return "\n".join([
        "Position closed (PAPER)",
        f"Position: {position_id}",
        f"PnL: {_fmt_money(pnl)}",
        f"Entry: {_fmt_price(entry_price)}",
        f"Exit: {_fmt_price(exit_price)}",
        f"Time: {timestamp}",
    ])


def _format_execution(event: Dict) -> str:
    side = _normalize_side(event.get("side"))
    size = event.get("filled_size_usd")
    price = event.get("actual_price")
    market_id = event.get("market_id", "unknown")
    execution_id = event.get("execution_id", "n/a")
    strategy = event.get("strategy", "n/a")
    signal_source = event.get("signal_source", "n/a")
    fees = event.get("fees")
    timestamp = event.get("timestamp", "n/a")

    return "\n".join([
        "Trade executed (LIVE)",
        f"Execution: {execution_id}",
        f"Side: {side}",
        f"Size: {_fmt_money(size)}",
        f"Price: {_fmt_price(price)}",
        f"Fees: {_fmt_money(fees)}",
        f"Market: {market_id}",
        f"Strategy: {strategy}",
        f"Signal: {signal_source}",
        f"Time: {timestamp}",
    ])


def _load_state(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {k: int(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _save_state(path: Path, state: Dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def _iter_logs(paths: Iterable[Path]) -> Dict[Path, None]:
    return {path: None for path in paths}


def _read_new_lines(path: Path, offset: int) -> Tuple[int, List[str]]:
    if not path.exists():
        return offset, []
    with open(path, "r", encoding="utf-8") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        new_offset = handle.tell()
    return new_offset, lines


def _should_notify(event: Dict, min_size: Optional[float]) -> bool:
    if min_size is None:
        return True
    size = (
        event.get("size_usd")
        or event.get("filled_size_usd")
        or event.get("filled_size")
        or 0
    )
    try:
        return float(size) >= min_size
    except (TypeError, ValueError):
        return False


def _handle_event(event: Dict, min_size: Optional[float]) -> Optional[str]:
    event_type = event.get("type")
    if event_type == "order_filled":
        if not _should_notify(event, min_size):
            return None
        return _format_paper_order(event)
    if event_type == "position_closed":
        return _format_position_closed(event)

    if "execution_id" in event and "actual_price" in event:
        if not _should_notify(event, min_size):
            return None
        return _format_execution(event)

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram/Discord trade alert bot.")
    parser.add_argument(
        "--log",
        action="append",
        help="Path to JSONL log file (repeatable). Defaults to paper + execution logs.",
    )
    parser.add_argument("--from-start", action="store_true", help="Read logs from start.")
    parser.add_argument("--poll", type=float, default=1.0, help="Polling interval (seconds).")
    parser.add_argument("--min-size", type=float, default=None, help="Minimum trade size USD.")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="State file to store offsets.")

    parser.add_argument("--discord-webhook", default=None, help="Discord webhook URL.")
    parser.add_argument("--discord-token", default=None, help="Discord bot token.")
    parser.add_argument("--discord-channel", default=None, help="Discord channel ID.")
    parser.add_argument("--telegram-token", default=None, help="Telegram bot token.")
    parser.add_argument("--telegram-chat", default=None, help="Telegram chat ID.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logs = [Path(p) for p in (args.log or DEFAULT_LOGS)]
    state_path = Path(args.state)
    state = _load_state(state_path)

    discord = DiscordNotifier(
        webhook_url=args.discord_webhook or os.environ.get("DISCORD_WEBHOOK_URL"),
        token=args.discord_token or os.environ.get("DISCORD_BOT_TOKEN"),
        channel_id=args.discord_channel or os.environ.get("DISCORD_CHANNEL_ID"),
    )
    telegram = TelegramNotifier(
        token=args.telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN"),
        chat_id=args.telegram_chat or os.environ.get("TELEGRAM_CHAT_ID"),
    )
    sender = AlertSender(discord, telegram)

    if not sender.enabled:
        raise SystemExit("No notification channels configured.")

    for path in logs:
        if path.as_posix() not in state:
            if args.from_start:
                state[path.as_posix()] = 0
            else:
                if path.exists():
                    state[path.as_posix()] = path.stat().st_size
                else:
                    state[path.as_posix()] = 0

    print("Trade alert bot started.")
    print(f"Logs: {', '.join(p.as_posix() for p in logs)}")

    try:
        while True:
            changed = False
            for path in logs:
                key = path.as_posix()
                offset = state.get(key, 0)
                new_offset, lines = _read_new_lines(path, offset)
                if new_offset != offset:
                    state[key] = new_offset
                    changed = True

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = _handle_event(event, args.min_size)
                    if message:
                        sender.send(message)

            if changed:
                _save_state(state_path, state)

            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("Trade alert bot stopped.")
    finally:
        _save_state(state_path, state)


if __name__ == "__main__":
    main()
