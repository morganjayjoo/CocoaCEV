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
