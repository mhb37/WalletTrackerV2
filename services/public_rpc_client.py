"""
Client de secours utilisant le RPC public Solana (gratuit, sans clé, sans compte).
Utilisé automatiquement quand Helius est rate-limité ou à quota (voir data_provider.py).

Contrairement à Helius, ce RPC ne classe pas les transactions ni ne les décode:
il faut reconstruire nous-mêmes les achats/ventes à partir des changements de
solde SOL et de tokens. C'est aussi 1 appel par transaction (au lieu d'un appel
groupé), donc plus lent. On reste volontairement prudent sur les volumes
demandés (moins de pages, moins de tokens vérifiés) pour ne pas se faire
rate-limiter à notre tour sur un point d'accès partagé par tout le monde.
"""
import asyncio
import time
import httpx
from datetime import datetime, timezone

PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"

_throttle_lock = asyncio.Lock()
_last_call_time = 0.0
MIN_INTERVAL_SECONDS = 0.5


async def _throttle():
    global _last_call_time
    async with _throttle_lock:
        now = time.monotonic()
        wait = MIN_INTERVAL_SECONDS - (now - _last_call_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_time = time.monotonic()


async def _rpc_call(client: httpx.AsyncClient, method: str, params: list):
    await _throttle()
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        resp = await client.post(PUBLIC_RPC_URL, json=payload, timeout=20.0)
    except httpx.RequestError:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get("result")


async def get_signatures(client: httpx.AsyncClient, address: str, limit: int = 100, before: str | None = None) -> list[dict]:
    opts = {"limit": limit}
    if before:
        opts["before"] = before
    result = await _rpc_call(client, "getSignaturesForAddress", [address, opts])
    return result or []


async def get_parsed_transaction(client: httpx.AsyncClient, signature: str) -> dict | None:
    return await _rpc_call(
        client, "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    )


def _account_keys(tx_result: dict) -> list[str]:
    keys = tx_result["transaction"]["message"].get("accountKeys", [])
    return [k["pubkey"] if isinstance(k, dict) else k for k in keys]


def normalize_for_wallet(tx_result: dict, signature: str, wallet_address: str) -> dict | None:
    """Reconstruit un format proche de Helius, mais uniquement pour ce wallet précis."""
    if not tx_result or not tx_result.get("meta"):
        return None
    meta = tx_result["meta"]
    block_time = tx_result.get("blockTime")
    account_keys = _account_keys(tx_result)

    native_transfers = []
    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])
    try:
        idx = account_keys.index(wallet_address)
    except ValueError:
        idx = None
    if idx is not None and idx < len(pre_balances) and idx < len(post_balances):
        delta = post_balances[idx] - pre_balances[idx]
        if delta < 0:
            native_transfers.append({"fromUserAccount": wallet_address, "amount": abs(delta)})
        elif delta > 0:
            native_transfers.append({"toUserAccount": wallet_address, "amount": delta})

    def _token_map(entries):
        m = {}
        for e in entries or []:
            if e.get("owner") != wallet_address:
                continue
            mint = e.get("mint")
            ui = e.get("uiTokenAmount", {}) or {}
            amount = float(ui.get("uiAmount") or 0)
            m[mint] = amount
        return m

    pre_map = _token_map(meta.get("preTokenBalances"))
    post_map = _token_map(meta.get("postTokenBalances"))

    token_transfers = []
    for mint in set(pre_map) | set(post_map):
        delta = post_map.get(mint, 0) - pre_map.get(mint, 0)
        if delta > 0:
            token_transfers.append({"mint": mint, "toUserAccount": wallet_address, "tokenAmount": delta})
        elif delta < 0:
            token_transfers.append({"mint": mint, "fromUserAccount": wallet_address, "tokenAmount": abs(delta)})

    return {
        "timestamp": block_time,
        "type": "SWAP" if (native_transfers and token_transfers) else "UNKNOWN",
        "nativeTransfers": native_transfers,
        "tokenTransfers": token_transfers,
        "signature": signature,
    }


def extract_token_buyers(tx_result: dict, signature: str, token_address: str) -> list[dict]:
    """Pour une tx touchant un MINT: renvoie les comptes dont le solde de ce token a augmenté."""
    if not tx_result or not tx_result.get("meta"):
        return []
    meta = tx_result["meta"]
    block_time = tx_result.get("blockTime")

    def _map(entries):
        m = {}
        for e in entries or []:
            if e.get("mint") != token_address:
                continue
            owner = e.get("owner")
            ui = e.get("uiTokenAmount", {}) or {}
            m[owner] = float(ui.get("uiAmount") or 0)
        return m

    pre_map = _map(meta.get("preTokenBalances"))
    post_map = _map(meta.get("postTokenBalances"))

    buyers = []
    for owner in set(pre_map) | set(post_map):
        if not owner:
            continue
        delta = post_map.get(owner, 0) - pre_map.get(owner, 0)
        if delta > 0:
            buyers.append({
                "wallet": owner,
                "timestamp": datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None,
                "token_amount": delta,
                "tx_signature": signature,
            })
    return buyers


async def get_wallet_transactions(address: str, limit: int = 30) -> list[dict]:
    """Équivalent dégradé de helius_client.get_wallet_transactions."""
    async with httpx.AsyncClient() as client:
        sigs = await get_signatures(client, address, limit=limit)
        txs = []
        for s in sigs:
            tx = await get_parsed_transaction(client, s["signature"])
            normalized = normalize_for_wallet(tx, s["signature"], address)
            if normalized:
                txs.append(normalized)
        return txs


async def get_token_early_buyers(
    token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int, max_pages: int = 5,
) -> tuple[list[dict], str]:
    mint_ts = mint_timestamp.timestamp()
    cutoff = mint_ts + (window_minutes * 60)

    all_buyers = []
    before_sig = None
    stop_reason = "max_pages_reached"

    async with httpx.AsyncClient() as client:
        for _ in range(max_pages):
            sigs = await get_signatures(client, token_address, limit=100, before=before_sig)
            if not sigs:
                stop_reason = "empty_page"
                break

            oldest_ts = min((s.get("blockTime") or mint_ts) for s in sigs)

            for s in sigs:
                ts = s.get("blockTime")
                if ts is None or not (mint_ts <= ts <= cutoff):
                    continue
                tx = await get_parsed_transaction(client, s["signature"])
                all_buyers.extend(extract_token_buyers(tx, s["signature"], token_address))

            before_sig = sigs[-1]["signature"]

            if oldest_ts < mint_ts:
                stop_reason = "passed_mint"
                break
            if oldest_ts <= cutoff:
                stop_reason = "reached_window"
                break

    all_buyers.sort(key=lambda b: b["timestamp"] or datetime.min.replace(tzinfo=timezone.utc))
    seen = set()
    early_buyers = []
    for b in all_buyers:
        if b["wallet"] in seen:
            continue
        seen.add(b["wallet"])
        early_buyers.append(b)
        if len(early_buyers) >= max_buyers:
            break

    return early_buyers, f"rpc_public:{stop_reason}"


async def get_wallet_transaction_history(address: str, max_pages: int = 3, page_size: int = 50) -> list[dict]:
    """Version dégradée et plus prudente (1 appel par tx = coûteux sur un RPC partagé)."""
    all_txs = []
    before_sig = None

    async with httpx.AsyncClient() as client:
        for _ in range(max_pages):
            sigs = await get_signatures(client, address, limit=page_size, before=before_sig)
            if not sigs:
                break
            for s in sigs:
                tx = await get_parsed_transaction(client, s["signature"])
                normalized = normalize_for_wallet(tx, s["signature"], address)
                if normalized:
                    all_txs.append(normalized)
            before_sig = sigs[-1]["signature"]

    return all_txs
