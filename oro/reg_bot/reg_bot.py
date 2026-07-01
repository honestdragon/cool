#!/usr/bin/env python3
"""Daemon that registers hotkeys on SN15 when burn cost is below a threshold.

Registers hotkeys sequentially in the configured order. Each hotkey is registered
only when the on-chain recycle (burn) price is strictly below the max cost.

Price safety:
  - Pre-submit recycle read avoids unnecessary transactions.
  - register_limit enforces the max price on-chain at execution time.
  - Post-submit balance check confirms actual recycle paid < max.

Usage:
  python reg_bot/reg_bot.py
  REG_BOT_WALLET_PASSWORD='...' python reg_bot/reg_bot.py
  ./reg_bot/start.sh
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bittensor.utils.balance import Balance
    from bittensor_wallet import Wallet

ARCHIVE_ENDPOINT = "wss://archive.chain.opentensor.ai:443"
DEFAULT_NETWORK = "finney"
DEFAULT_NETUID = 15
DEFAULT_WALLET_NAME = "honestdragon"
DEFAULT_HOTKEYS = ("oro-08", "oro-09", "oro-10", "oro-11", "oro-12", "oro-13", "oro-14")
DEFAULT_MAX_REG_COST_TAO = 0.142
DEFAULT_WALLET_PASSWORD = ""
DEFAULT_POLL_SECONDS = 12.0
DEFAULT_RETRY_SECONDS = 30.0
DEFAULT_RESULTS_LOG = Path(__file__).resolve().parent / "logs" / "reg_bot_results.jsonl"

AttemptStatus = Literal[
    "success",
    "already_registered",
    "wait_price",
    "price_rejected",
    "failed",
]

LOGGER = logging.getLogger("reg_bot")
_SHUTDOWN = False


@dataclass(frozen=True)
class AttemptResult:
    status: AttemptStatus
    message: str
    uid: int | None = None
    recycle_paid_tao: float | None = None


@dataclass(frozen=True)
class HotkeyReport:
    hotkey: str
    hotkey_ss58: str
    netuid: int
    status: AttemptStatus
    message: str
    uid: int | None
    recycle_paid_tao: float | None
    index: int
    total: int
    remaining_hotkeys: tuple[str, ...]
    completed_at: str


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _handle_signal(signum: int, _frame: object) -> None:
    global _SHUTDOWN
    LOGGER.info("Received signal %s, shutting down after current step", signum)
    _SHUTDOWN = True


def _wallet_password() -> str:
    return os.environ.get("REG_BOT_WALLET_PASSWORD", DEFAULT_WALLET_PASSWORD)


def _load_wallet(name: str, hotkey: str) -> "Wallet":
    import bittensor as bt

    wallet = bt.Wallet(name=name, hotkey=hotkey)
    if not wallet.hotkey_file.exists_on_device():
        raise FileNotFoundError(f"Hotkey file not found: {wallet.hotkey_file.path}")
    return wallet


def _unlock_wallet(wallet: "Wallet", password: str) -> None:
    # get_coldkey(password=...) decrypts once but does not satisfy register_limit's
    # internal unlock_coldkey()/unlock_hotkey() calls, which prompt interactively.
    wallet.coldkey_file.save_password_to_env(password)
    wallet.unlock_coldkey()
    if wallet.hotkey_file.is_encrypted():
        wallet.hotkey_file.save_password_to_env(password)
        wallet.unlock_hotkey()


def _create_subtensor(network: str):
    import bittensor as bt

    return bt.Subtensor(network=network, archive_endpoints=[ARCHIVE_ENDPOINT])


def _max_limit_price(max_reg_cost_tao: float) -> "Balance":
    from bittensor.utils.balance import Balance

    # Strict upper bound: on-chain price must be <= this (below max_reg_cost_tao).
    return Balance.from_rao(int(max_reg_cost_tao * 1_000_000_000) - 1)


def _reg_cost_tao(subtensor, netuid: int) -> float | None:
    recycle = subtensor.recycle(netuid=netuid)
    if recycle is None:
        return None
    return float(recycle.tao)


def _is_registered(subtensor, netuid: int, hotkey_ss58: str) -> bool:
    return subtensor.is_hotkey_registered(netuid=netuid, hotkey_ss58=hotkey_ss58)


def _is_price_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "registrationpricelimitexceeded" in lowered or "price limit" in lowered


def _verify_registration_cost(
    balance_before: "Balance | None",
    balance_after: "Balance | None",
    extrinsic_fee: "Balance | None",
    max_reg_cost_tao: float,
) -> tuple[bool, str, float | None]:
    from bittensor.utils.balance import Balance

    if balance_before is None or balance_after is None:
        return False, "missing balance data for cost verification", None

    total_spent = balance_before - balance_after
    fee = extrinsic_fee if extrinsic_fee is not None else Balance.from_rao(0)
    recycle_paid = total_spent - fee
    paid_tao = float(recycle_paid.tao)
    if paid_tao >= max_reg_cost_tao:
        return (
            False,
            f"recycle paid {paid_tao:.6f} TAO >= max {max_reg_cost_tao:.6f} TAO",
            paid_tao,
        )
    return True, f"recycle paid {paid_tao:.6f} TAO", paid_tao


def _lookup_uid(subtensor, netuid: int, hotkey_ss58: str) -> int | None:
    neuron = subtensor.get_neuron_for_pubkey_and_subnet(
        netuid=netuid,
        hotkey_ss58=hotkey_ss58,
    )
    if neuron.is_null:
        return None
    return int(neuron.uid)


def _remaining_hotkeys(
    subtensor,
    netuid: int,
    wallet_name: str,
    hotkeys: tuple[str, ...],
) -> tuple[str, ...]:
    pending: list[str] = []
    for hotkey in hotkeys:
        wallet = _load_wallet(wallet_name, hotkey)
        if not _is_registered(subtensor, netuid, wallet.hotkey.ss58_address):
            pending.append(hotkey)
    return tuple(pending)


def _build_hotkey_report(
    *,
    hotkey: str,
    wallet: "Wallet",
    netuid: int,
    result: AttemptResult,
    hotkeys: tuple[str, ...],
    remaining_hotkeys: tuple[str, ...],
) -> HotkeyReport:
    index = hotkeys.index(hotkey) + 1
    return HotkeyReport(
        hotkey=hotkey,
        hotkey_ss58=wallet.hotkey.ss58_address,
        netuid=netuid,
        status=result.status,
        message=result.message,
        uid=result.uid,
        recycle_paid_tao=result.recycle_paid_tao,
        index=index,
        total=len(hotkeys),
        remaining_hotkeys=remaining_hotkeys,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


def _format_hotkey_report(report: HotkeyReport) -> str:
    status_label = {
        "success": "REGISTERED",
        "already_registered": "ALREADY REGISTERED",
    }.get(report.status, report.status.upper())
    lines = [
        "=" * 60,
        f"HOTKEY RESULT: {report.hotkey} ({report.index}/{report.total})",
        f"Status: {status_label}",
        f"Netuid: {report.netuid}",
        f"Hotkey: {report.hotkey_ss58}",
    ]
    if report.uid is not None:
        lines.append(f"UID: {report.uid}")
    if report.recycle_paid_tao is not None:
        lines.append(f"Recycle paid: {report.recycle_paid_tao:.6f} TAO")
    lines.append(f"Detail: {report.message}")
    if report.remaining_hotkeys:
        lines.append(f"Remaining: {', '.join(report.remaining_hotkeys)}")
    else:
        lines.append("Remaining: none (all hotkeys registered)")
    lines.append(f"Time (UTC): {report.completed_at}")
    lines.append("=" * 60)
    return "\n".join(lines)


def _save_hotkey_report(report: HotkeyReport, results_log: Path) -> None:
    results_log.parent.mkdir(parents=True, exist_ok=True)
    with results_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(report), ensure_ascii=True) + "\n")


def _webhook_notify(report: HotkeyReport) -> None:
    webhook_url = os.environ.get("REG_BOT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    status_label = {
        "success": "registered",
        "already_registered": "already registered",
    }.get(report.status, report.status)
    text = (
        f"[reg_bot] {report.hotkey} ({report.index}/{report.total}) "
        f"{status_label} on netuid {report.netuid}"
    )
    if report.uid is not None:
        text += f", uid={report.uid}"
    if report.recycle_paid_tao is not None:
        text += f", paid={report.recycle_paid_tao:.6f} TAO"
    if report.remaining_hotkeys:
        text += f", remaining={', '.join(report.remaining_hotkeys)}"
    else:
        text += ", all hotkeys done"

    payload = json.dumps({"content": text}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            pass
    except urllib.error.URLError as exc:
        LOGGER.warning("Webhook notification failed: %s", exc)


def _notify_hotkey_complete(report: HotkeyReport, results_log: Path) -> None:
    summary = _format_hotkey_report(report)
    LOGGER.info("\n%s", summary)
    print(summary, flush=True)
    _save_hotkey_report(report, results_log)
    _webhook_notify(report)


def _attempt_register(
    subtensor,
    wallet: "Wallet",
    netuid: int,
    max_reg_cost_tao: float,
    password: str,
) -> AttemptResult:
    hotkey_ss58 = wallet.hotkey.ss58_address
    if _is_registered(subtensor, netuid, hotkey_ss58):
        uid = _lookup_uid(subtensor, netuid, hotkey_ss58)
        return AttemptResult(
            "already_registered",
            "already registered on chain",
            uid=uid,
        )

    recycle_tao = _reg_cost_tao(subtensor, netuid)
    if recycle_tao is None:
        return AttemptResult("failed", f"subnet {netuid} does not exist or recycle unavailable")
    if recycle_tao >= max_reg_cost_tao:
        return AttemptResult(
            "wait_price",
            f"recycle {recycle_tao:.6f} TAO >= max {max_reg_cost_tao:.6f} TAO",
        )

    coldkey_ss58 = wallet.coldkeypub.ss58_address
    balance_before = subtensor.get_balance(address=coldkey_ss58)
    limit_price = _max_limit_price(max_reg_cost_tao)

    _unlock_wallet(wallet, password)
    LOGGER.info(
        "Submitting register_limit for %s: observed recycle %.6f TAO, on-chain limit %.9f TAO",
        wallet.hotkey_str,
        recycle_tao,
        float(limit_price.tao),
    )
    response = subtensor.register_limit(
        wallet=wallet,
        netuid=netuid,
        limit_price=limit_price,
        wait_for_inclusion=True,
        wait_for_finalization=False,
    )

    balance_after = subtensor.get_balance(address=coldkey_ss58)
    registered = _is_registered(subtensor, netuid, hotkey_ss58)
    message = response.message or "registration failed"

    if not response.success:
        if _is_price_limit_error(message):
            return AttemptResult(
                "price_rejected",
                f"on-chain price limit rejected tx (observed recycle was {recycle_tao:.6f} TAO): {message}",
            )
        if registered:
            ok, cost_msg, paid_tao = _verify_registration_cost(
                balance_before,
                balance_after,
                response.extrinsic_fee,
                max_reg_cost_tao,
            )
            if ok:
                uid = _lookup_uid(subtensor, netuid, hotkey_ss58)
                return AttemptResult(
                    "success",
                    f"registered; {cost_msg}",
                    uid=uid,
                    recycle_paid_tao=paid_tao,
                )
            return AttemptResult(
                "failed",
                f"registered but cost check failed: {cost_msg}",
            )
        return AttemptResult("failed", message)

    if not registered:
        return AttemptResult("failed", f"tx succeeded but hotkey not registered: {message}")

    ok, cost_msg, paid_tao = _verify_registration_cost(
        balance_before,
        balance_after,
        response.extrinsic_fee,
        max_reg_cost_tao,
    )
    if not ok:
        return AttemptResult("failed", f"registered but cost check failed: {cost_msg}")
    uid = _lookup_uid(subtensor, netuid, hotkey_ss58)
    return AttemptResult(
        "success",
        cost_msg,
        uid=uid,
        recycle_paid_tao=paid_tao,
    )


def _next_pending_hotkey(
    subtensor,
    netuid: int,
    wallet_name: str,
    hotkeys: tuple[str, ...],
) -> tuple[str, "Wallet"] | None:
    for hotkey in hotkeys:
        wallet = _load_wallet(wallet_name, hotkey)
        if not _is_registered(subtensor, netuid, wallet.hotkey.ss58_address):
            return hotkey, wallet
    return None


def run_daemon(
    *,
    netuid: int,
    wallet_name: str,
    hotkeys: tuple[str, ...],
    max_reg_cost_tao: float,
    network: str,
    poll_seconds: float,
    retry_seconds: float,
    results_log: Path,
) -> int:
    password = _wallet_password()
    subtensor = _create_subtensor(network)
    LOGGER.info(
        "Starting reg bot: netuid=%s wallet=%s hotkeys=%s max_cost=%.6f TAO network=%s",
        netuid,
        wallet_name,
        ", ".join(hotkeys),
        max_reg_cost_tao,
        network,
    )

    while not _SHUTDOWN:
        pending = _next_pending_hotkey(subtensor, netuid, wallet_name, hotkeys)
        if pending is None:
            summary = "\n".join(
                [
                    "=" * 60,
                    "REG BOT COMPLETE",
                    f"All {len(hotkeys)} hotkeys registered on netuid {netuid}",
                    f"Hotkeys: {', '.join(hotkeys)}",
                    f"Time (UTC): {datetime.now(timezone.utc).isoformat()}",
                    "=" * 60,
                ]
            )
            LOGGER.info("\n%s", summary)
            print(summary, flush=True)
            return 0

        hotkey, wallet = pending
        try:
            result = _attempt_register(
                subtensor,
                wallet,
                netuid,
                max_reg_cost_tao,
                password,
            )
        except Exception:
            LOGGER.exception("Registration attempt failed for %s", hotkey)
            time.sleep(retry_seconds)
            continue

        if result.status in ("success", "already_registered"):
            remaining = _remaining_hotkeys(subtensor, netuid, wallet_name, hotkeys)
            report = _build_hotkey_report(
                hotkey=hotkey,
                wallet=wallet,
                netuid=netuid,
                result=result,
                hotkeys=hotkeys,
                remaining_hotkeys=remaining,
            )
            _notify_hotkey_complete(report, results_log)
            continue

        if result.status == "wait_price":
            LOGGER.info("Waiting for %s: %s", hotkey, result.message)
            time.sleep(poll_seconds)
            continue

        if result.status == "price_rejected":
            LOGGER.info(
                "Price rose before execution for %s, no overpay: %s",
                hotkey,
                result.message,
            )
            time.sleep(poll_seconds)
            continue

        LOGGER.warning("Registration not completed for %s: %s", hotkey, result.message)
        time.sleep(retry_seconds)

    LOGGER.info("Shutdown requested before all hotkeys were registered")
    return 130


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register hotkeys on Bittensor subnet when burn cost is low enough."
    )
    parser.add_argument("--netuid", type=int, default=DEFAULT_NETUID)
    parser.add_argument("--wallet-name", default=DEFAULT_WALLET_NAME)
    parser.add_argument(
        "--hotkeys",
        nargs="+",
        default=list(DEFAULT_HOTKEYS),
        help="Hotkeys to register, in order",
    )
    parser.add_argument(
        "--max-reg-cost",
        type=float,
        default=DEFAULT_MAX_REG_COST_TAO,
        help="Register only when recycle cost is strictly below this amount (TAO)",
    )
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="Seconds between recycle price checks while waiting",
    )
    parser.add_argument(
        "--retry-seconds",
        type=float,
        default=DEFAULT_RETRY_SECONDS,
        help="Seconds to wait after a failed registration attempt",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--results-log",
        type=Path,
        default=DEFAULT_RESULTS_LOG,
        help="Append one JSON line per completed hotkey registration",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.verbose)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.max_reg_cost <= 0:
        LOGGER.error("--max-reg-cost must be positive")
        return 2

    try:
        return run_daemon(
            netuid=args.netuid,
            wallet_name=args.wallet_name,
            hotkeys=tuple(args.hotkeys),
            max_reg_cost_tao=args.max_reg_cost,
            network=args.network,
            poll_seconds=args.poll_seconds,
            retry_seconds=args.retry_seconds,
            results_log=args.results_log,
        )
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.info("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
