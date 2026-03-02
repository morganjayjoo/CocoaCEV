#!/usr/bin/env python3
"""
CocoaCEV — CLI and dashboard for Therminos on-chain temperature checker (crypto price and volatility).
Read heat bands, report prices as updater, export history. Upgraded from Kika with risk scoring,
snapshots, volatility rank, health checks, report-from-file, and chain presets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
APP_NAME = "CocoaCEV"
VERSION = "1.0.0"
CONFIG_FILENAME = "cocoa_cev_config.json"
DEFAULT_RPC = "https://eth.llamarpc.com"

# Chain presets: name -> RPC URL (use rpc_url or COCOACEV_RPC with preset name)
CHAIN_PRESETS: dict[str, str] = {
    "mainnet": "https://eth.llamarpc.com",
    "sepolia": "https://rpc.sepolia.org",
    "polygon": "https://polygon-rpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "base": "https://mainnet.base.org",
    "optimism": "https://mainnet.optimism.io",
}

BAND_NAMES = ("cold", "mild", "warm", "hot", "critical")
BAND_COLD, BAND_MILD, BAND_WARM, BAND_HOT, BAND_CRITICAL = 0, 1, 2, 3, 4
E8 = 10**8
BPS_BASE = 10_000

# Minimal ABI for Therminos (view + reportPrice + batchReportPrices + config)
THRMINOS_ABI = [
    {"inputs": [], "name": "getRegisteredSymbols", "outputs": [{"internalType": "bytes32[]", "name": "", "type": "bytes32[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getHeatSummary", "outputs": [
        {"internalType": "bytes32[]", "name": "symbolHashes", "type": "bytes32[]"},
        {"internalType": "uint8[]", "name": "bands", "type": "uint8[]"},
        {"internalType": "uint256[]", "name": "volatilitiesE8", "type": "uint256[]"},
        {"internalType": "uint256[]", "name": "pricesE8", "type": "uint256[]"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "getThermometer", "outputs": [
        {"internalType": "uint256", "name": "windowBlocks", "type": "uint256"},
        {"internalType": "uint256", "name": "cooldownBlocks", "type": "uint256"},
        {"internalType": "uint256", "name": "lastReportBlock", "type": "uint256"},
        {"internalType": "uint8", "name": "currentBand", "type": "uint8"},
        {"internalType": "uint256", "name": "currentVolatilityE8", "type": "uint256"},
        {"internalType": "uint256", "name": "currentPriceE8", "type": "uint256"},
        {"internalType": "bool", "name": "halted", "type": "bool"},
        {"internalType": "uint256", "name": "registeredAtBlock", "type": "uint256"},
        {"internalType": "uint256", "name": "historyLength", "type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}, {"internalType": "uint256", "name": "priceE8", "type": "uint256"}], "name": "reportPrice", "outputs": [], "stateMutability": "payable", "type": "function"},
    {"inputs": [
        {"internalType": "bytes32[]", "name": "symbolHashes", "type": "bytes32[]"},
        {"internalType": "uint256[]", "name": "pricesE8", "type": "uint256[]"}
    ], "name": "batchReportPrices", "outputs": [], "stateMutability": "payable", "type": "function"},
    {"inputs": [{"internalType": "string", "name": "symbol", "type": "string"}], "name": "symbolHashFromString", "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}], "stateMutability": "pure", "type": "function"},
    {"inputs": [], "name": "getThresholds", "outputs": [
        {"internalType": "uint256", "name": "_coldBps", "type": "uint256"},
        {"internalType": "uint256", "name": "_mildBps", "type": "uint256"},
        {"internalType": "uint256", "name": "_warmBps", "type": "uint256"},
        {"internalType": "uint256", "name": "_hotBps", "type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "getCurrentBand", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "getCurrentPriceE8", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "getVolatilityE8", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}, {"internalType": "uint256", "name": "offset", "type": "uint256"}, {"internalType": "uint256", "name": "limit", "type": "uint256"}], "name": "getPriceHistory", "outputs": [
        {"internalType": "uint256[]", "name": "pricesE8", "type": "uint256[]"},
        {"internalType": "uint256[]", "name": "blocks", "type": "uint256[]"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}, {"internalType": "uint256", "name": "offset", "type": "uint256"}, {"internalType": "uint256", "name": "limit", "type": "uint256"}], "name": "getBandHistory", "outputs": [
        {"internalType": "uint8[]", "name": "bands", "type": "uint8[]"},
        {"internalType": "uint256[]", "name": "blocks", "type": "uint256[]"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getBandStats", "outputs": [
        {"internalType": "uint256", "name": "coldCount", "type": "uint256"},
        {"internalType": "uint256", "name": "mildCount", "type": "uint256"},
        {"internalType": "uint256", "name": "warmCount", "type": "uint256"},
        {"internalType": "uint256", "name": "hotCount", "type": "uint256"},
        {"internalType": "uint256", "name": "criticalCount", "type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "platformPaused", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "isHalted", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getReportFeeWei", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getContractBalance", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "uint8", "name": "band", "type": "uint8"}], "name": "bandLabel", "outputs": [{"internalType": "string", "name": "", "type": "string"}], "stateMutability": "pure", "type": "function"},
    {"inputs": [], "name": "getHottestSymbol", "outputs": [
        {"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"},
        {"internalType": "uint8", "name": "band", "type": "uint8"},
        {"internalType": "uint256", "name": "volatilityE8", "type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getColdestSymbol", "outputs": [
        {"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"},
        {"internalType": "uint8", "name": "band", "type": "uint8"},
        {"internalType": "uint256", "name": "volatilityE8", "type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "canReport", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getConfigSnapshot", "outputs": [
        {"internalType": "address", "name": "ownerAddr", "type": "address"},
        {"internalType": "address", "name": "treasuryAddr", "type": "address"},
        {"internalType": "address", "name": "guardianAddr", "type": "address"},
        {"internalType": "address", "name": "updaterAddr", "type": "address"},
        {"internalType": "uint256", "name": "deployBlk", "type": "uint256"},
        {"internalType": "uint256", "name": "coldBpsVal", "type": "uint256"},
        {"internalType": "uint256", "name": "mildBpsVal", "type": "uint256"},
        {"internalType": "uint256", "name": "warmBpsVal", "type": "uint256"},
        {"internalType": "uint256", "name": "hotBpsVal", "type": "uint256"},
        {"internalType": "uint256", "name": "reportFee", "type": "uint256"},
        {"internalType": "uint256", "name": "maxHistLen", "type": "uint256"},
        {"internalType": "bool", "name": "paused", "type": "bool"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}], "name": "getSummaryForSymbol", "outputs": [
        {"internalType": "uint256", "name": "currentPriceE8", "type": "uint256"},
        {"internalType": "uint256", "name": "currentVolatilityE8", "type": "uint256"},
        {"internalType": "uint8", "name": "currentBand", "type": "uint8"},
        {"internalType": "uint256", "name": "minPriceE8", "type": "uint256"},
        {"internalType": "uint256", "name": "maxPriceE8", "type": "uint256"},
        {"internalType": "uint256", "name": "historyLength", "type": "uint256"},
        {"internalType": "bool", "name": "halted", "type": "bool"},
        {"internalType": "uint256", "name": "lastReportBlock", "type": "uint256"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getSlotsCount", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getGlobalReportSequence", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}, {"internalType": "uint256", "name": "blockNum", "type": "uint256"}], "name": "getPriceAtBlock", "outputs": [{"internalType": "uint256", "name": "priceE8", "type": "uint256"}, {"internalType": "bool", "name": "found", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "symbolHash", "type": "bytes32"}, {"internalType": "uint256", "name": "fromBlock", "type": "uint256"}, {"internalType": "uint256", "name": "toBlock", "type": "uint256"}], "name": "getPriceChangeBps", "outputs": [{"internalType": "int256", "name": "changeBps", "type": "int256"}, {"internalType": "bool", "name": "fromFound", "type": "bool"}, {"internalType": "bool", "name": "toFound", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getGenesisHash", "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getDeployBlock", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
def config_path() -> Path:
    base = os.environ.get("COCOACEV_CONFIG_DIR") or os.path.expanduser("~")
    return Path(base) / CONFIG_FILENAME


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict[str, Any]) -> bool:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        return False


def get_config(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def set_config(key: str, value: Any) -> None:
    c = load_config()
    c[key] = value
    save_config(c)


# -----------------------------------------------------------------------------
# Web3
# -----------------------------------------------------------------------------
def get_rpc() -> str:
    url = get_config("rpc_url") or os.environ.get("COCOACEV_RPC")
    if url and url in CHAIN_PRESETS:
        url = CHAIN_PRESETS[url]
    return url or DEFAULT_RPC


def get_contract_address() -> Optional[str]:
    return get_config("contract_address") or os.environ.get("COCOACEV_CONTRACT")


def get_private_key() -> Optional[str]:
    return get_config("private_key") or os.environ.get("COCOACEV_PRIVATE_KEY")


def connect_web3():
    try:
        from web3 import Web3
    except ImportError:
        print("Install web3: pip install web3", file=sys.stderr)
        sys.exit(1)
    rpc = get_rpc()
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {rpc}")
    return w3


def get_contract(w3):
    addr = get_contract_address()
    if not addr:
        raise ValueError("Contract address not set. Use --contract or set contract_address in config.")
    return w3.eth.contract(address=Web3.to_checksum_address(addr), abi=THRMINOS_ABI)


def symbol_to_hash(w3, symbol: str) -> bytes:
    contract = w3.eth.contract(address=Web3.to_checksum_address(get_contract_address()), abi=THRMINOS_ABI)
    return contract.functions.symbolHashFromString(symbol).call()


# -----------------------------------------------------------------------------
# Formatting
# -----------------------------------------------------------------------------
def fmt_price_e8(price_e8: int) -> str:
    if price_e8 == 0:
        return "0"
    return f"{price_e8 / E8:.8f}"


def fmt_volatility_bps(vol_e8: int) -> str:
    if vol_e8 == 0:
        return "0"
    bps = (vol_e8 * BPS_BASE) // E8
    return f"{bps} bps"


def fmt_eth(wei: int | str) -> str:
    try:
        w = int(wei)
        return f"{w / 1e18:.6f} ETH"
    except (ValueError, TypeError):
        return str(wei)


def truncate_addr(addr: str, head: int = 6, tail: int = 4) -> str:
    if not addr or len(addr) < head + tail + 2:
        return addr or ""
    return f"{addr[: head + 2]}...{addr[-tail:]}"


def band_name(band: int) -> str:
    if 0 <= band < len(BAND_NAMES):
        return BAND_NAMES[band]
    return f"band_{band}"


def hash_to_hex(h: bytes) -> str:
    if hasattr(h, "hex"):
        return "0x" + h.hex()
    return str(h)


# -----------------------------------------------------------------------------
# Commands: summary
# -----------------------------------------------------------------------------
def cmd_summary(w3, contract, args) -> None:
    try:
        hashes, bands, vols, prices = contract.functions.getHeatSummary().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not hashes:
        print("No thermometers registered.")
        return
    symbol_map = get_config("symbol_map") or {}
    print(f"\n  Heat summary ({len(hashes)} thermometers)\n")
    print("  " + "-" * 72)
    for i, h in enumerate(hashes):
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        label = symbol_map.get(hex_h, hex_h[:18] + ".." if len(hex_h) > 18 else hex_h)
        band = bands[i] if i < len(bands) else 0
        vol = vols[i] if i < len(vols) else 0
        pr = prices[i] if i < len(prices) else 0
        print(f"  {label:24}  band={band_name(band):10}  vol={fmt_volatility_bps(vol):12}  price={fmt_price_e8(pr)}")
    print("  " + "-" * 72)


# -----------------------------------------------------------------------------
# Commands: band-stats
# -----------------------------------------------------------------------------
def cmd_band_stats(w3, contract, args) -> None:
    try:
        cold, mild, warm, hot, critical = contract.functions.getBandStats().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print("\n  Band distribution")
    print("  " + "-" * 40)
    print(f"  cold:     {cold}")
    print(f"  mild:     {mild}")
    print(f"  warm:     {warm}")
    print(f"  hot:     {hot}")
    print(f"  critical: {critical}")
    print("  " + "-" * 40)


# -----------------------------------------------------------------------------
# Commands: symbol
# -----------------------------------------------------------------------------
def cmd_symbol(w3, contract, args) -> None:
    sym = args.symbol
    if not sym:
        print("Provide --symbol", file=sys.stderr)
        sys.exit(1)
    try:
        h = contract.functions.symbolHashFromString(sym).call()
        summary = contract.functions.getSummaryForSymbol(h).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    (current_price, current_vol, current_band, min_p, max_p, hist_len, halted, last_block) = summary
    print(f"\n  Symbol: {sym}")
    print("  " + "-" * 50)
    print(f"  Current price (E8):  {current_price}  ({fmt_price_e8(current_price)})")
    print(f"  Volatility (E8):    {current_vol}  ({fmt_volatility_bps(current_vol)})")
    print(f"  Band:               {current_band} ({band_name(current_band)})")
    print(f"  Min price (window): {min_p}  ({fmt_price_e8(min_p)})")
    print(f"  Max price (window): {max_p}  ({fmt_price_e8(max_p)})")
    print(f"  History length:     {hist_len}")
    print(f"  Halted:             {halted}")
    print(f"  Last report block:  {last_block}")
    print("  " + "-" * 50)


# -----------------------------------------------------------------------------
# Commands: list
# -----------------------------------------------------------------------------
def cmd_list(w3, contract, args) -> None:
    try:
        hashes = contract.functions.getRegisteredSymbols().call()
        count = contract.functions.getSlotsCount().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  Registered symbols ({count})\n")
    symbol_map = get_config("symbol_map") or {}
    for h in hashes:
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        label = symbol_map.get(hex_h, hex_h[:20] + "..")
        print(f"    {label}")
    print()


# -----------------------------------------------------------------------------
# Commands: thresholds
# -----------------------------------------------------------------------------
def cmd_thresholds(w3, contract, args) -> None:
    try:
        cold, mild, warm, hot = contract.functions.getThresholds().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print("\n  Volatility band thresholds (bps)")
    print("  " + "-" * 40)
    print(f"  cold:     0 - {cold}")
    print(f"  mild:     {cold} - {mild}")
    print(f"  warm:     {mild} - {warm}")
    print(f"  hot:      {warm} - {hot}")
    print(f"  critical: {hot} - 10000")
    print("  " + "-" * 40)


# -----------------------------------------------------------------------------
# Commands: config (contract)
# -----------------------------------------------------------------------------
def cmd_config_snapshot(w3, contract, args) -> None:
    try:
        out = contract.functions.getConfigSnapshot().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    (owner_addr, treasury_addr, guardian_addr, updater_addr, deploy_blk, cold_bps, mild_bps, warm_bps, hot_bps, report_fee, max_hist_len, paused) = out
    print("\n  Contract config")
    print("  " + "-" * 50)
    print(f"  Owner:         {owner_addr}")
    print(f"  Treasury:      {treasury_addr}")
    print(f"  Guardian:      {guardian_addr}")
    print(f"  Updater:       {updater_addr}")
    print(f"  Deploy block:  {deploy_blk}")
    print(f"  Cold bps:      {cold_bps}")
    print(f"  Mild bps:      {mild_bps}")
    print(f"  Warm bps:     {warm_bps}")
    print(f"  Hot bps:      {hot_bps}")
    print(f"  Report fee:    {report_fee} wei ({fmt_eth(report_fee)})")
    print(f"  Max history:   {max_hist_len}")
    print(f"  Paused:       {paused}")
    print("  " + "-" * 50)


# -----------------------------------------------------------------------------
# Commands: report
# -----------------------------------------------------------------------------
def cmd_report(w3, contract, args) -> None:
    sym = args.symbol
    price = args.price
    if not sym or price is None:
        print("Provide --symbol and --price (price in E8 units, e.g. 45000_00000000 for 45000)", file=sys.stderr)
        sys.exit(1)
    try:
        price_e8 = int(price)
    except ValueError:
        print("--price must be an integer (E8)", file=sys.stderr)
        sys.exit(1)
    pk = get_private_key()
    if not pk:
        print("Private key required for report. Set COCOACEV_PRIVATE_KEY or private_key in config.", file=sys.stderr)
        sys.exit(1)
    try:
        from web3 import Web3
        account = w3.eth.account.from_key(pk)
    except Exception as e:
        print(f"Invalid key or web3: {e}", file=sys.stderr)
        sys.exit(1)
    fee = contract.functions.getReportFeeWei().call()
    tx_params = {"from": account.address, "gas": 200_000}
    if fee > 0:
        tx_params["value"] = fee
    try:
        h = contract.functions.symbolHashFromString(sym).call()
        can = contract.functions.canReport(h).call()
        if not can:
            print("Cannot report: cooldown or symbol halted.", file=sys.stderr)
            sys.exit(1)
        tx = contract.functions.reportPrice(h, price_e8).build_transaction(tx_params)
        tx["nonce"] = w3.eth.get_transaction_count(account.address)
        signed = w3.eth.account.sign_transaction(tx, account.key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"Tx sent: {tx_hash.hex()}")
        if args.wait:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"Block: {receipt['blockNumber']}, status: {receipt['status']}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------------
# Commands: batch-report
# -----------------------------------------------------------------------------
def cmd_batch_report(w3, contract, args) -> None:
    symbols = args.symbols
    prices = args.prices
    if not symbols or not prices:
        print("Provide --symbols and --prices (comma-separated; counts must match)", file=sys.stderr)
        sys.exit(1)
    sym_list = [s.strip() for s in symbols.split(",")]
    try:
        price_list = [int(p.strip()) for p in prices.split(",")]
    except ValueError:
        print("--prices must be comma-separated integers (E8)", file=sys.stderr)
        sys.exit(1)
    if len(sym_list) != len(price_list):
        print("Symbol and price counts must match.", file=sys.stderr)
        sys.exit(1)
    pk = get_private_key()
    if not pk:
        print("Private key required.", file=sys.stderr)
        sys.exit(1)
    try:
        from web3 import Web3
        account = w3.eth.account.from_key(pk)
    except Exception as e:
        print(f"Invalid key: {e}", file=sys.stderr)
        sys.exit(1)
    hashes = []
    for s in sym_list:
        h = contract.functions.symbolHashFromString(s).call()
        hashes.append(h)
    fee_per = contract.functions.getReportFeeWei().call()
    total_fee = fee_per * len(hashes)
    tx_params = {"from": account.address, "gas": 150_000 * len(hashes)}
    if total_fee > 0:
        tx_params["value"] = total_fee
    try:
        tx = contract.functions.batchReportPrices(hashes, price_list).build_transaction(tx_params)
        tx["nonce"] = w3.eth.get_transaction_count(account.address)
        signed = w3.eth.account.sign_transaction(tx, account.key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"Batch tx sent: {tx_hash.hex()}")
        if args.wait:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"Block: {receipt['blockNumber']}, status: {receipt['status']}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------------
# Commands: history
# -----------------------------------------------------------------------------
def cmd_history(w3, contract, args) -> None:
    sym = args.symbol
    limit = args.limit or 24
    if not sym:
        print("Provide --symbol", file=sys.stderr)
        sys.exit(1)
    try:
        h = contract.functions.symbolHashFromString(sym).call()
        prices, blocks = contract.functions.getPriceHistory(h, 0, limit).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not prices:
        print(f"No history for {sym}.")
        return
    print(f"\n  Price history for {sym} (last {len(prices)} points)\n")
    for i in range(len(prices) - 1, -1, -1):
        print(f"    block {blocks[i]:8}  price_e8={prices[i]:14}  ({fmt_price_e8(prices[i])})")
    print()


# -----------------------------------------------------------------------------
# Commands: band-history
# -----------------------------------------------------------------------------
def cmd_band_history(w3, contract, args) -> None:
    sym = args.symbol
    limit = args.limit or 20
    if not sym:
        print("Provide --symbol", file=sys.stderr)
        sys.exit(1)
    try:
        h = contract.functions.symbolHashFromString(sym).call()
        bands, blocks = contract.functions.getBandHistory(h, 0, limit).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not bands:
        print(f"No band history for {sym}.")
        return
    print(f"\n  Band history for {sym} (last {len(bands)})\n")
    for i in range(len(bands) - 1, -1, -1):
        print(f"    block {blocks[i]:8}  band={band_name(bands[i])}")
    print()


# -----------------------------------------------------------------------------
# Commands: hottest / coldest
# -----------------------------------------------------------------------------
def cmd_hottest(w3, contract, args) -> None:
    try:
        h, band, vol = contract.functions.getHottestSymbol().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
    symbol_map = get_config("symbol_map") or {}
    label = symbol_map.get(hex_h, hex_h[:20] + "..")
    print(f"\n  Hottest: {label}  band={band_name(band)}  volatility_e8={vol} ({fmt_volatility_bps(vol)})\n")


def cmd_coldest(w3, contract, args) -> None:
    try:
        h, band, vol = contract.functions.getColdestSymbol().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
    symbol_map = get_config("symbol_map") or {}
    label = symbol_map.get(hex_h, hex_h[:20] + "..")
    print(f"\n  Coldest: {label}  band={band_name(band)}  volatility_e8={vol} ({fmt_volatility_bps(vol)})\n")


# -----------------------------------------------------------------------------
# Commands: watch
# -----------------------------------------------------------------------------
def cmd_watch(w3, contract, args) -> None:
    interval = args.interval or 12
    print(f"Refreshing every {interval}s (Ctrl+C to stop)\n")
    try:
        while True:
            try:
                hashes, bands, vols, prices = contract.functions.getHeatSummary().call()
                seq = contract.functions.getGlobalReportSequence().call()
                paused = contract.functions.platformPaused().call()
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                time.sleep(interval)
                continue
            print("\033[2J\033[H", end="")
            print(f"  Therminos heat (sequence={seq}, paused={paused})  refresh={interval}s\n")
            symbol_map = get_config("symbol_map") or {}
            for i, h in enumerate(hashes):
                hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
                label = symbol_map.get(hex_h, hex_h[:16] + "..")
                band = bands[i] if i < len(bands) else 0
                vol = vols[i] if i < len(vols) else 0
                pr = prices[i] if i < len(prices) else 0
                print(f"  {label:22}  {band_name(band):10}  {fmt_volatility_bps(vol):12}  {fmt_price_e8(pr)}")
            print("\n  Ctrl+C to stop.")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


# -----------------------------------------------------------------------------
# Commands: export
# -----------------------------------------------------------------------------
def cmd_export(w3, contract, args) -> None:
    out_path = args.output or "cocoa_cev_export.json"
    try:
        hashes = contract.functions.getRegisteredSymbols().call()
        cold, mild, warm, hot, critical = contract.functions.getBandStats().call()
        seq = contract.functions.getGlobalReportSequence().call()
        config = contract.functions.getConfigSnapshot().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    data = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contract": get_contract_address(),
        "global_report_sequence": seq,
        "band_stats": {"cold": cold, "mild": mild, "warm": warm, "hot": hot, "critical": critical},
        "config": {
            "owner": config[0],
            "treasury": config[1],
            "guardian": config[2],
            "updater": config[3],
            "deploy_block": config[4],
            "cold_bps": config[5],
            "mild_bps": config[6],
            "warm_bps": config[7],
            "hot_bps": config[8],
            "report_fee_wei": config[9],
            "max_history_len": config[10],
            "paused": config[11],
        },
        "thermometers": [],
    }
    symbol_map = get_config("symbol_map") or {}
    for h in hashes:
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        try:
            summary = contract.functions.getSummaryForSymbol(h).call()
        except Exception:
            continue
        (cur_price, cur_vol, cur_band, min_p, max_p, hist_len, halted, last_block) = summary
        data["thermometers"].append({
            "symbol_hash_hex": hex_h,
            "label": symbol_map.get(hex_h, hex_h),
            "current_price_e8": cur_price,
            "current_volatility_e8": cur_vol,
            "current_band": cur_band,
            "band_name": band_name(cur_band),
            "min_price_e8": min_p,
            "max_price_e8": max_p,
            "history_length": hist_len,
            "halted": halted,
            "last_report_block": last_block,
        })
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Exported to {out_path}")


# -----------------------------------------------------------------------------
# Commands: set-config (local config)
# -----------------------------------------------------------------------------
def cmd_set_config(args) -> None:
    key = args.key
    value = args.value
    if not key:
        print("Provide --key and --value", file=sys.stderr)
        sys.exit(1)
    if key == "contract_address":
        set_config(key, value)
        print(f"Set contract_address = {value}")
    elif key == "rpc_url":
        set_config(key, value)
        print(f"Set rpc_url = {value}")
