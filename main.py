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
    elif key == "symbol_map":
        try:
            mapping = json.loads(value)
            set_config(key, mapping)
            print(f"Set symbol_map = {mapping}")
        except json.JSONDecodeError:
            print("symbol_map must be JSON object, e.g. {\"0xabc...\": \"BTC\"}", file=sys.stderr)
            sys.exit(1)
    else:
        set_config(key, value)
        print(f"Set {key} = {value}")


# -----------------------------------------------------------------------------
# Commands: add-symbol-label
# -----------------------------------------------------------------------------
def cmd_add_symbol_label(args) -> None:
    hash_hex = args.hash
    label = args.label
    if not hash_hex or not label:
        print("Provide --hash (bytes32 hex) and --label", file=sys.stderr)
        sys.exit(1)
    c = load_config()
    m = c.get("symbol_map") or {}
    m[hash_hex] = label
    c["symbol_map"] = m
    save_config(c)
    print(f"Mapped {hash_hex} -> {label}")


# -----------------------------------------------------------------------------
# Helpers: validation and conversion
# -----------------------------------------------------------------------------
def price_float_to_e8(price: float) -> int:
    """Convert human price to E8 integer."""
    return int(round(price * E8))


def price_e8_to_float(price_e8: int) -> float:
    """Convert E8 integer to float."""
    return price_e8 / E8


def validate_address(addr: Optional[str]) -> bool:
    if not addr:
        return False
    try:
        from web3 import Web3
        return Web3.is_address(addr)
    except Exception:
        return len(addr) == 42 and addr.startswith("0x")


def validate_bytes32_hex(s: str) -> bool:
    return isinstance(s, str) and len(s) == 66 and s.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in s[2:])


# -----------------------------------------------------------------------------
# ASCII thermometer bar
# -----------------------------------------------------------------------------
def band_bar(band: int, width: int = 20) -> str:
    """Return a simple ASCII bar for band level (0-4)."""
    if width < 5:
        width = 5
    fill = int((band + 1) / 5 * width)
    fill = min(fill, width)
    return "[" + "#" * fill + "." * (width - fill) + "]"


def band_color_name(band: int) -> str:
    """Label suitable for terminal color."""
    names = ("cold", "mild", "warm", "hot", "critical")
    return names[band] if 0 <= band < 5 else "unknown"


# -----------------------------------------------------------------------------
# Table formatting
# -----------------------------------------------------------------------------
def table_row(columns: list[str], widths: Optional[list[int]] = None) -> str:
    if not widths:
        widths = [max(8, len(c) + 2) for c in columns]
    parts = []
    for i, c in enumerate(columns):
        w = widths[i] if i < len(widths) else len(c) + 2
        parts.append(str(c)[: w - 2].ljust(w - 2)[: w - 2])
    return "  ".join(parts)


def table_sep(widths: list[int], char: str = "-") -> str:
    return "  ".join(char * w for w in widths)


# -----------------------------------------------------------------------------
# Commands: status
# -----------------------------------------------------------------------------
def cmd_status(w3, contract, args) -> None:
    try:
        balance = contract.functions.getContractBalance().call()
        paused = contract.functions.platformPaused().call()
        seq = contract.functions.getGlobalReportSequence().call()
        count = contract.functions.getSlotsCount().call()
        fee = contract.functions.getReportFeeWei().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print("\n  Contract status")
    print("  " + "-" * 40)
    print(f"  Contract balance:  {fmt_eth(balance)}")
    print(f"  Paused:            {paused}")
    print(f"  Global report seq: {seq}")
    print(f"  Thermometer count: {count}")
    print(f"  Report fee:        {fee} wei ({fmt_eth(fee)})")
    print("  " + "-" * 40)


# -----------------------------------------------------------------------------
# Commands: can-report
# -----------------------------------------------------------------------------
def cmd_can_report(w3, contract, args) -> None:
    sym = args.symbol
    if not sym:
        print("Provide --symbol", file=sys.stderr)
        sys.exit(1)
    try:
        h = contract.functions.symbolHashFromString(sym).call()
        can = contract.functions.canReport(h).call()
        halted = contract.functions.isHalted(h).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  Symbol: {sym}")
    print(f"  Can report: {can}")
    print(f"  Halted:     {halted}\n")


# -----------------------------------------------------------------------------
# Commands: thermometer (full slot)
# -----------------------------------------------------------------------------
def cmd_thermometer(w3, contract, args) -> None:
    sym = args.symbol
    if not sym:
        print("Provide --symbol", file=sys.stderr)
        sys.exit(1)
    try:
        h = contract.functions.symbolHashFromString(sym).call()
        out = contract.functions.getThermometer(h).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    (window_blocks, cooldown_blocks, last_report_block, current_band, current_volatility_e8,
     current_price_e8, halted, registered_at_block, history_length) = out
    print(f"\n  Thermometer: {sym}")
    print("  " + "-" * 50)
    print(f"  Window blocks:      {window_blocks}")
    print(f"  Cooldown blocks:     {cooldown_blocks}")
    print(f"  Last report block:   {last_report_block}")
    print(f"  Current band:        {current_band} ({band_name(current_band)}) {band_bar(current_band)}")
    print(f"  Current volatility: {current_volatility_e8} ({fmt_volatility_bps(current_volatility_e8)})")
    print(f"  Current price E8:   {current_price_e8} ({fmt_price_e8(current_price_e8)})")
    print(f"  Halted:              {halted}")
    print(f"  Registered at block: {registered_at_block}")
    print(f"  History length:     {history_length}")
    print("  " + "-" * 50)


# -----------------------------------------------------------------------------
# Commands: slots (paginated)
# -----------------------------------------------------------------------------
def cmd_slots(w3, contract, args) -> None:
    offset = args.offset or 0
    limit = args.limit or 20
    try:
        total = contract.functions.getSlotsCount().call()
        hashes = contract.functions.getRegisteredSymbols().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if offset >= len(hashes):
        print("No slots in range.")
        return
    end = min(offset + limit, len(hashes))
    subset = hashes[offset:end]
    symbol_map = get_config("symbol_map") or {}
    print(f"\n  Slots {offset + 1}-{end} of {total}\n")
    for h in subset:
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        label = symbol_map.get(hex_h, hex_h[:18] + "..")
        try:
            band = contract.functions.getCurrentBand(h).call()
            vol = contract.functions.getVolatilityE8(h).call()
            pr = contract.functions.getCurrentPriceE8(h).call()
        except Exception:
            band, vol, pr = 0, 0, 0
        print(f"    {label:24}  band={band_name(band):10}  vol={fmt_volatility_bps(vol):12}  price={fmt_price_e8(pr)}")
    print()


# -----------------------------------------------------------------------------
# Commands: dashboard (box summary)
# -----------------------------------------------------------------------------
def cmd_dashboard(w3, contract, args) -> None:
    try:
        hashes, bands, vols, prices = contract.functions.getHeatSummary().call()
        cold, mild, warm, hot, critical = contract.functions.getBandStats().call()
        seq = contract.functions.getGlobalReportSequence().call()
        paused = contract.functions.platformPaused().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    symbol_map = get_config("symbol_map") or {}
    width = 70
    print("\n  +" + "-" * (width - 2) + "+")
    print("  | Therminos dashboard" + " " * (width - 24) + "|")
    print("  | sequence=%s  paused=%s" % (seq, paused) + " " * (width - 35) + "|")
    print("  +" + "-" * (width - 2) + "+")
    print("  | Band distribution: cold=%s mild=%s warm=%s hot=%s critical=%s" % (cold, mild, warm, hot, critical) + " " * max(0, width - 65) + "|")
    print("  +" + "-" * (width - 2) + "+")
    for i, h in enumerate(hashes):
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        label = symbol_map.get(hex_h, hex_h[:14] + "..")
        band = bands[i] if i < len(bands) else 0
        vol = vols[i] if i < len(vols) else 0
        pr = prices[i] if i < len(prices) else 0
        bar = band_bar(band, 12)
        line = "  | %s  %s  %s  %s  %s" % (label[:18].ljust(18), band_name(band).ljust(10), fmt_volatility_bps(vol).ljust(10), fmt_price_e8(pr).ljust(12), bar)
        print(line[: width + 4] + " " * max(0, width - len(line) + 4) + "|")
    print("  +" + "-" * (width - 2) + "+\n")


# -----------------------------------------------------------------------------
# Commands: export-csv
# -----------------------------------------------------------------------------
def cmd_export_csv(w3, contract, args) -> None:
    out_path = args.output or "cocoa_cev_export.csv"
    try:
        hashes = contract.functions.getRegisteredSymbols().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    symbol_map = get_config("symbol_map") or {}
    rows = [["symbol_hash_hex", "label", "current_band", "band_name", "current_price_e8", "current_volatility_e8", "halted", "last_report_block"]]
    for h in hashes:
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        try:
            summary = contract.functions.getSummaryForSymbol(h).call()
        except Exception:
            continue
        (cur_price, cur_vol, cur_band, min_p, max_p, hist_len, halted, last_block) = summary
        rows.append([hex_h, symbol_map.get(hex_h, hex_h), str(cur_band), band_name(cur_band), str(cur_price), str(cur_vol), str(halted), str(last_block)])
    import csv
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"Exported CSV to {out_path}")


# -----------------------------------------------------------------------------
# Commands: alerts
# -----------------------------------------------------------------------------
def cmd_alerts(w3, contract, args) -> None:
    try:
        hashes = contract.functions.getRegisteredSymbols().call()
        paused = contract.functions.platformPaused().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    symbol_map = get_config("symbol_map") or {}
    hot_or_critical = []
    halted_list = []
    for h in hashes:
        try:
            band = contract.functions.getCurrentBand(h).call()
            is_halt = contract.functions.isHalted(h).call()
        except Exception:
            continue
        if band >= BAND_HOT:
            hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
            hot_or_critical.append((symbol_map.get(hex_h, hex_h[:16] + ".."), band_name(band)))
        if is_halt:
            hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
            halted_list.append(symbol_map.get(hex_h, hex_h[:16] + ".."))
    print("\n  Alerts")
    print("  " + "-" * 40)
    print(f"  Platform paused: {paused}")
    print(f"  Hot or critical: {len(hot_or_critical)}")
    for label, b in hot_or_critical:
        print(f"    - {label}  ({b})")
    print(f"  Halted symbols: {len(halted_list)}")
    for label in halted_list:
        print(f"    - {label}")
    print("  " + "-" * 40 + "\n")


# -----------------------------------------------------------------------------
# Commands: price-at
# -----------------------------------------------------------------------------
def cmd_price_at(w3, contract, args) -> None:
    sym = getattr(args, "symbol", None) or (args.symbol if hasattr(args, "symbol") else None)
    block_num = getattr(args, "block", None)
    if not sym or block_num is None:
        print("Provide symbol and block (positional or --block)", file=sys.stderr)
        sys.exit(1)
    try:
        block_num = int(block_num)
        h = contract.functions.symbolHashFromString(sym).call()
        price_e8, found = contract.functions.getPriceAtBlock(h, block_num).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not found:
        print(f"No price for {sym} at or before block {block_num}.")
        return
    print(f"\n  {sym} at block {block_num}: price_e8={price_e8} ({fmt_price_e8(price_e8)})\n")


# -----------------------------------------------------------------------------
# Commands: compare
# -----------------------------------------------------------------------------
def cmd_compare(w3, contract, args) -> None:
    sym = getattr(args, "symbol", None)
    from_block = getattr(args, "from_block", None)
    to_block = getattr(args, "to_block", None)
    if not sym or from_block is None or to_block is None:
        print("Provide symbol, --from-block, --to-block", file=sys.stderr)
        sys.exit(1)
    try:
        from_block = int(from_block)
        to_block = int(to_block)
        h = contract.functions.symbolHashFromString(sym).call()
        change_bps, from_found, to_found = contract.functions.getPriceChangeBps(h, from_block, to_block).call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  {sym}  block {from_block} -> {to_block}")
    print(f"  From found: {from_found}, To found: {to_found}")
    print(f"  Change (bps): {change_bps}\n")


# -----------------------------------------------------------------------------
# Commands: report with float price
# -----------------------------------------------------------------------------
def cmd_report_float(w3, contract, args) -> None:
    sym = args.symbol
    price_float = args.price_float
    if not sym or price_float is None:
        print("Provide --symbol and --price-float (e.g. 45000.5)", file=sys.stderr)
        sys.exit(1)
    try:
        price_e8 = price_float_to_e8(float(price_float))
    except (ValueError, TypeError):
        print("--price-float must be a number.", file=sys.stderr)
        sys.exit(1)
    args.price = str(price_e8)
    cmd_report(w3, contract, args)


# -----------------------------------------------------------------------------
# Commands: info
# -----------------------------------------------------------------------------
def cmd_info(w3, contract, args) -> None:
    """Print app and contract connection info."""
    rpc = get_rpc()
    addr = get_contract_address()
    try:
        genesis = contract.functions.getGenesisHash().call()
        deploy_block = contract.functions.getDeployBlock().call()
        chain_id = w3.eth.chain_id
    except Exception:
        genesis = deploy_block = chain_id = None
    print(f"\n  {APP_NAME} {VERSION}")
    print("  " + "-" * 40)
    print(f"  RPC:          {rpc[:50]}..." if len(rpc) > 50 else f"  RPC:          {rpc}")
    print(f"  Contract:     {addr or '(not set)'}")
    if chain_id is not None:
        print(f"  Chain ID:     {chain_id}")
    if deploy_block is not None:
        print(f"  Deploy block: {deploy_block}")
    if genesis is not None:
        hex_genesis = genesis.hex() if hasattr(genesis, "hex") else str(genesis)
        print(f"  Genesis hash: {hex_genesis[:24]}...")
    print("  " + "-" * 40 + "\n")


# -----------------------------------------------------------------------------
# CocoaCEV: risk-score — aggregate risk score (0–100) from band distribution
# -----------------------------------------------------------------------------
def cmd_risk_score(w3, contract, args) -> None:
    """Compute a single risk score 0–100 from current bands (critical=100, hot=75, warm=50, mild=25, cold=0)."""
    try:
        hashes, bands, vols, prices = contract.functions.getHeatSummary().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not bands:
        print("  Risk score: N/A (no thermometers)\n")
        return
    weights = (0, 25, 50, 75, 100)  # cold, mild, warm, hot, critical
    total = sum(weights[b] if b < 5 else 100 for b in bands)
    score = total // len(bands)
    hot_count = sum(1 for b in bands if b >= BAND_HOT)
    print("\n  Risk score (0–100)")
    print("  " + "-" * 40)
    print(f"  Aggregate score: {score}")
    print(f"  Thermometers in hot/critical: {hot_count} / {len(bands)}")
    print("  " + "-" * 40 + "\n")


# -----------------------------------------------------------------------------
# CocoaCEV: snapshot save/load — save or load heat summary to named file
# -----------------------------------------------------------------------------
def cmd_snapshot_save(w3, contract, args) -> None:
    name = getattr(args, "name", None) or "default"
    path = Path(get_config("snapshot_dir") or ".") / f"cocoa_cev_snapshot_{name}.json"
    try:
        hashes, bands, vols, prices = contract.functions.getHeatSummary().call()
        seq = contract.functions.getGlobalReportSequence().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    symbol_map = get_config("symbol_map") or {}
    data = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sequence": seq,
        "thermometers": [],
    }
    for i, h in enumerate(hashes):
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        data["thermometers"].append({
            "symbol_hash_hex": hex_h,
            "label": symbol_map.get(hex_h, hex_h),
            "band": int(bands[i]) if i < len(bands) else 0,
            "volatility_e8": int(vols[i]) if i < len(vols) else 0,
            "price_e8": int(prices[i]) if i < len(prices) else 0,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Snapshot saved to {path}")


def cmd_snapshot_load(args) -> None:
    name = getattr(args, "name", None) or "default"
    path = Path(get_config("snapshot_dir") or ".") / f"cocoa_cev_snapshot_{name}.json"
    if not path.exists():
        print(f"Snapshot not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"\n  Snapshot: {name} (saved {data.get('saved_at', '?')}, seq={data.get('sequence', '?')})\n")
    for t in data.get("thermometers", []):
        label = t.get("label", t.get("symbol_hash_hex", "?")[:16])
        band = t.get("band", 0)
        print(f"    {label:22}  band={band_name(band):10}  vol={fmt_volatility_bps(t.get('volatility_e8', 0)):12}  price={fmt_price_e8(t.get('price_e8', 0))}")
    print()


# -----------------------------------------------------------------------------
# CocoaCEV: volatility-rank — symbols sorted by volatility (desc) with rank
# -----------------------------------------------------------------------------
def cmd_volatility_rank(w3, contract, args) -> None:
    try:
        hashes = contract.functions.getRegisteredSymbols().call()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    symbol_map = get_config("symbol_map") or {}
    rows = []
    for h in hashes:
        try:
            vol = contract.functions.getVolatilityE8(h).call()
            band = contract.functions.getCurrentBand(h).call()
            pr = contract.functions.getCurrentPriceE8(h).call()
        except Exception:
            continue
        hex_h = hash_to_hex(h) if hasattr(h, "hex") else str(h)
        rows.append((symbol_map.get(hex_h, hex_h[:16] + ".."), vol, band, pr))
    rows.sort(key=lambda x: x[1], reverse=True)
    print("\n  Volatility rank (highest first)\n")
    for rank, (label, vol, band, pr) in enumerate(rows, 1):
        print(f"    #{rank:2}  {label:22}  {fmt_volatility_bps(vol):12}  band={band_name(band):10}  {fmt_price_e8(pr)}")
    print()


# -----------------------------------------------------------------------------
# CocoaCEV: band-timeline — band history as compact timeline string
# -----------------------------------------------------------------------------
def cmd_band_timeline(w3, contract, args) -> None:
    sym = getattr(args, "symbol", None)
    limit = getattr(args, "limit", None) or 40
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
    chars = "CmwhC"  # cold mild warm hot critical
    timeline = "".join(chars[b] if b < 5 else "?" for b in reversed(bands))
    print(f"\n  Band timeline for {sym} (oldest left, newest right; C=cold m=mild w=warm h=hot C=critical)\n")
    print(f"  {timeline}\n")


# -----------------------------------------------------------------------------
# CocoaCEV: health — one-shot connectivity and contract health
# -----------------------------------------------------------------------------
def cmd_health(w3, contract, args) -> None:
    ok = True
    print("\n  Health check")
    print("  " + "-" * 40)
    try:
        count = contract.functions.getSlotsCount().call()
        paused = contract.functions.platformPaused().call()
        seq = contract.functions.getGlobalReportSequence().call()
    except Exception as e:
        print(f"  Contract read: FAIL — {e}")
        ok = False
    else:
        print(f"  Contract read: OK (thermometers={count}, paused={paused}, seq={seq})")
    if not get_contract_address():
        print("  Config: contract_address not set")
        ok = False
    else:
        print("  Config: contract_address set")
    print("  " + "-" * 40)
    print("  Overall:", "OK" if ok else "FAIL")
    print()


# -----------------------------------------------------------------------------
# CocoaCEV: report-from-file — batch report from JSON file
# -----------------------------------------------------------------------------
def cmd_report_from_file(w3, contract, args) -> None:
    path = getattr(args, "file", None)
    if not path or not Path(path).exists():
        print("Provide --file (path to JSON: [{\"symbol\": \"BTC\", \"price_e8\": 4500000000000}, ...])", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        items = [items]
    symbols = []
    prices_e8 = []
    for item in items:
        sym = item.get("symbol") or item.get("symbol_hash")
        pe = item.get("price_e8") or item.get("priceE8")
        if sym is None or pe is None:
            continue
        if isinstance(pe, float):
            pe = int(round(pe * E8)) if pe >= 1 else int(pe)
        symbols.append(sym)
        prices_e8.append(int(pe))
    if not symbols:
        print("No valid symbol/price_e8 entries in file.", file=sys.stderr)
        sys.exit(1)
    args.symbols = ",".join(symbols)
    args.prices = ",".join(map(str, prices_e8))
    args.wait = getattr(args, "wait", False)
    cmd_batch_report(w3, contract, args)


# -----------------------------------------------------------------------------
# CocoaCEV: simulate — offline band from list of prices and thresholds (bps)
# -----------------------------------------------------------------------------
def _volatility_bps_from_prices(prices_e8: list[int]) -> int:
    if len(prices_e8) < 2:
        return 0
    changes = []
    for i in range(1, len(prices_e8)):
        p0, p1 = prices_e8[i - 1], prices_e8[i]
        if p0 == 0:
            continue
        ch = abs(int(p1) - int(p0)) * BPS_BASE // p0
        changes.append(ch)
    return sum(changes) // len(changes) if changes else 0


def _band_from_bps(vol_bps: int, cold: int, mild: int, warm: int, hot: int) -> int:
    if vol_bps <= cold:
        return BAND_COLD
    if vol_bps <= mild:
        return BAND_MILD
    if vol_bps <= warm:
        return BAND_WARM
    if vol_bps <= hot:
        return BAND_HOT
    return BAND_CRITICAL


def cmd_simulate(args) -> None:
    """Offline: compute band from comma-separated prices (E8) and optional threshold bps."""
    prices_str = getattr(args, "prices", None)
    if not prices_str:
        print("Provide --prices (comma-separated E8 values, e.g. 100000000,102000000,99000000)", file=sys.stderr)
        sys.exit(1)
    try:
        prices_e8 = [int(x.strip()) for x in prices_str.split(",")]
    except ValueError:
        print("--prices must be comma-separated integers.", file=sys.stderr)
        sys.exit(1)
    cold = int(getattr(args, "cold_bps", None) or 500)
    mild = int(getattr(args, "mild_bps", None) or 1500)
    warm = int(getattr(args, "warm_bps", None) or 3500)
    hot = int(getattr(args, "hot_bps", None) or 7000)
    vol_bps = _volatility_bps_from_prices(prices_e8)
    band = _band_from_bps(vol_bps, cold, mild, warm, hot)
    print(f"\n  Simulate (offline)")
    print(f"  Volatility (bps): {vol_bps}")
    print(f"  Band: {band} ({band_name(band)})")
    print(f"  Thresholds: cold<={cold}, mild<={mild}, warm<={warm}, hot<={hot}\n")


# -----------------------------------------------------------------------------
# CocoaCEV: diff-exports — compare two export JSON files
# -----------------------------------------------------------------------------
def cmd_diff_exports(args) -> None:
    a_path = getattr(args, "file_a", None)
    b_path = getattr(args, "file_b", None)
    if not a_path or not b_path:
        print("Provide --file-a and --file-b (cocoa_cev_export.json or snapshot)", file=sys.stderr)
        sys.exit(1)
    for p in (a_path, b_path):
        if not Path(p).exists():
            print(f"File not found: {p}", file=sys.stderr)
            sys.exit(1)
    with open(a_path, "r", encoding="utf-8") as f:
        a = json.load(f)
    with open(b_path, "r", encoding="utf-8") as f:
        b = json.load(f)
    therm_a = {t.get("symbol_hash_hex") or t.get("label", ""): t for t in a.get("thermometers", [])}
    therm_b = {t.get("symbol_hash_hex") or t.get("label", ""): t for t in b.get("thermometers", [])}
    all_keys = set(therm_a) | set(therm_b)
    print("\n  Diff (A vs B)")
    print("  " + "-" * 60)
    for k in sorted(all_keys):
        ta, tb = therm_a.get(k), therm_b.get(k)
        label = (ta or tb).get("label", k[:16])
        band_a = ta.get("current_band", ta.get("band")) if ta else None
        band_b = tb.get("current_band", tb.get("band")) if tb else None
        if band_a != band_b:
            print(f"    {label:24}  band: {band_a} -> {band_b}")
    print("  " + "-" * 60 + "\n")


# -----------------------------------------------------------------------------
# CocoaCEV: chain-presets — list available chain preset names and URLs
# -----------------------------------------------------------------------------
def cmd_chain_presets(args) -> None:
    print("\n  Chain presets (set rpc_url or COCOACEV_RPC to preset name)")
    print("  " + "-" * 50)
    for name, url in CHAIN_PRESETS.items():
        print(f"    {name:12}  {url}")
    print("  " + "-" * 50 + "\n")


def _ensure_contract_and_rpc(args) -> None:
    """Apply --rpc and --contract from args to config so later get_rpc/get_contract_address use them."""
    if getattr(args, "rpc", None):
        set_config("rpc_url", args.rpc)
    if getattr(args, "contract", None):
        set_config("contract_address", args.contract)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> None:
    from web3 import Web3

    parser = argparse.ArgumentParser(prog=APP_NAME, description="CocoaCEV — Therminos temperature checker CLI")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    parser.add_argument("--rpc", default=None, help="RPC URL (overrides config)")
    parser.add_argument("--contract", default=None, help="Therminos contract address (overrides config)")
    sub = parser.add_subparsers(dest="command", help="Commands")

    p_summary = sub.add_parser("summary", help="Show heat summary for all thermometers")
    p_summary.set_defaults(func=lambda w3, c, a: cmd_summary(w3, c, a))

    p_band_stats = sub.add_parser("band-stats", help="Show band distribution")
    p_band_stats.set_defaults(func=lambda w3, c, a: cmd_band_stats(w3, c, a))

    p_symbol = sub.add_parser("symbol", help="Show details for one symbol")
    p_symbol.add_argument("symbol", nargs="?", default=None)
    p_symbol.set_defaults(func=lambda w3, c, a: cmd_symbol(w3, c, a))

    p_list = sub.add_parser("list", help="List registered symbol hashes")
    p_list.set_defaults(func=lambda w3, c, a: cmd_list(w3, c, a))

    p_thresholds = sub.add_parser("thresholds", help="Show volatility band thresholds")
    p_thresholds.set_defaults(func=lambda w3, c, a: cmd_thresholds(w3, c, a))

    p_config_snap = sub.add_parser("config", help="Show contract config snapshot")
    p_config_snap.set_defaults(func=lambda w3, c, a: cmd_config_snapshot(w3, c, a))

    p_report = sub.add_parser("report", help="Report price (updater; requires private key)")
    p_report.add_argument("symbol", nargs="?", default=None)
    p_report.add_argument("price", nargs="?", default=None, help="Price in E8")
    p_report.add_argument("--wait", action="store_true", help="Wait for tx receipt")
    p_report.set_defaults(func=lambda w3, c, a: cmd_report(w3, c, a))

    p_batch = sub.add_parser("batch-report", help="Batch report prices")
    p_batch.add_argument("--symbols", type=str, help="Comma-separated symbols")
    p_batch.add_argument("--prices", type=str, help="Comma-separated E8 prices")
    p_batch.add_argument("--wait", action="store_true")
    p_batch.set_defaults(func=lambda w3, c, a: cmd_batch_report(w3, c, a))

    p_history = sub.add_parser("history", help="Show price history for symbol")
    p_history.add_argument("symbol", nargs="?", default=None)
    p_history.add_argument("--limit", type=int, default=24)
    p_history.set_defaults(func=lambda w3, c, a: cmd_history(w3, c, a))

    p_band_hist = sub.add_parser("band-history", help="Show band history for symbol")
    p_band_hist.add_argument("symbol", nargs="?", default=None)
    p_band_hist.add_argument("--limit", type=int, default=20)
    p_band_hist.set_defaults(func=lambda w3, c, a: cmd_band_history(w3, c, a))

    p_hottest = sub.add_parser("hottest", help="Show hottest symbol")
    p_hottest.set_defaults(func=lambda w3, c, a: cmd_hottest(w3, c, a))

    p_coldest = sub.add_parser("coldest", help="Show coldest symbol")
    p_coldest.set_defaults(func=lambda w3, c, a: cmd_coldest(w3, c, a))

    p_watch = sub.add_parser("watch", help="Watch heat summary (refresh loop)")
    p_watch.add_argument("--interval", type=int, default=12)
    p_watch.set_defaults(func=lambda w3, c, a: cmd_watch(w3, c, a))

    p_export = sub.add_parser("export", help="Export summary to JSON")
    p_export.add_argument("--output", "-o", default=None)
    p_export.set_defaults(func=lambda w3, c, a: cmd_export(w3, c, a))

    p_status = sub.add_parser("status", help="Contract status (balance, paused, sequence)")
    p_status.set_defaults(func=lambda w3, c, a: cmd_status(w3, c, a))

    p_can = sub.add_parser("can-report", help="Check if symbol can be reported")
    p_can.add_argument("symbol", nargs="?", default=None)
    p_can.set_defaults(func=lambda w3, c, a: cmd_can_report(w3, c, a))

    p_thermo = sub.add_parser("thermometer", help="Full thermometer details for symbol")
    p_thermo.add_argument("symbol", nargs="?", default=None)
    p_thermo.set_defaults(func=lambda w3, c, a: cmd_thermometer(w3, c, a))

    p_slots = sub.add_parser("slots", help="Paginated list of slots")
    p_slots.add_argument("--offset", type=int, default=0)
    p_slots.add_argument("--limit", type=int, default=20)
    p_slots.set_defaults(func=lambda w3, c, a: cmd_slots(w3, c, a))

    p_dash = sub.add_parser("dashboard", help="Dashboard-style box summary")
    p_dash.set_defaults(func=lambda w3, c, a: cmd_dashboard(w3, c, a))

    p_csv = sub.add_parser("export-csv", help="Export thermometers to CSV")
    p_csv.add_argument("--output", "-o", default=None)
    p_csv.set_defaults(func=lambda w3, c, a: cmd_export_csv(w3, c, a))

    p_alerts = sub.add_parser("alerts", help="Hot/critical and halted symbols")
    p_alerts.set_defaults(func=lambda w3, c, a: cmd_alerts(w3, c, a))

    p_price_at = sub.add_parser("price-at", help="Price at or before block")
    p_price_at.add_argument("symbol", nargs="?", default=None)
    p_price_at.add_argument("block", nargs="?", default=None)
    p_price_at.set_defaults(func=lambda w3, c, a: cmd_price_at(w3, c, a))

    p_compare = sub.add_parser("compare", help="Price change bps between two blocks")
    p_compare.add_argument("symbol", nargs="?", default=None)
    p_compare.add_argument("--from-block", type=int)
    p_compare.add_argument("--to-block", type=int)
    p_compare.set_defaults(func=lambda w3, c, a: cmd_compare(w3, c, a))

    p_report_float = sub.add_parser("report-float", help="Report price using float (e.g. 45000.5)")
    p_report_float.add_argument("symbol", nargs="?", default=None)
    p_report_float.add_argument("price_float", nargs="?", default=None)
    p_report_float.add_argument("--wait", action="store_true")
    p_report_float.set_defaults(func=lambda w3, c, a: cmd_report_float(w3, c, a))

    p_info = sub.add_parser("info", help="App and contract connection info")
    p_info.set_defaults(func=lambda w3, c, a: cmd_info(w3, c, a))

    p_risk = sub.add_parser("risk-score", help="Aggregate risk score 0–100 from bands")
    p_risk.set_defaults(func=lambda w3, c, a: cmd_risk_score(w3, c, a))

    p_snap_save = sub.add_parser("snapshot-save", help="Save heat summary to named snapshot file")
    p_snap_save.add_argument("name", nargs="?", default="default")
    p_snap_save.set_defaults(func=lambda w3, c, a: cmd_snapshot_save(w3, c, a))

    p_snap_load = sub.add_parser("snapshot-load", help="Load and print a saved snapshot (no RPC)")
    p_snap_load.add_argument("name", nargs="?", default="default")
    p_snap_load.set_defaults(func=None)

    p_vol_rank = sub.add_parser("volatility-rank", help="Symbols sorted by volatility with rank")
    p_vol_rank.set_defaults(func=lambda w3, c, a: cmd_volatility_rank(w3, c, a))

    p_band_tl = sub.add_parser("band-timeline", help="Band history as compact timeline (C/m/w/h)")
    p_band_tl.add_argument("symbol", nargs="?", default=None)
    p_band_tl.add_argument("--limit", type=int, default=40)
    p_band_tl.set_defaults(func=lambda w3, c, a: cmd_band_timeline(w3, c, a))

    p_health = sub.add_parser("health", help="One-shot contract and config health check")
    p_health.set_defaults(func=lambda w3, c, a: cmd_health(w3, c, a))

    p_report_file = sub.add_parser("report-from-file", help="Batch report from JSON file")
    p_report_file.add_argument("file", nargs="?", default=None)
    p_report_file.add_argument("--wait", action="store_true")
    p_report_file.set_defaults(func=lambda w3, c, a: cmd_report_from_file(w3, c, a))

    p_simulate = sub.add_parser("simulate", help="Offline: band from comma-separated prices E8")
    p_simulate.add_argument("prices", nargs="?", default=None)
    p_simulate.add_argument("--cold-bps", type=int, default=500)
    p_simulate.add_argument("--mild-bps", type=int, default=1500)
    p_simulate.add_argument("--warm-bps", type=int, default=3500)
    p_simulate.add_argument("--hot-bps", type=int, default=7000)
    p_simulate.set_defaults(func=None)

    p_diff = sub.add_parser("diff-exports", help="Compare two export/snapshot JSON files")
