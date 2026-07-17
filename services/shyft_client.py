"""
Client Shyft: fournisseur alternatif gratuit (compte requis, contrairement au
RPC public) avec des données déjà décodées comme Helius. Utilisé comme secours
RAPIDE avant de tomber sur le RPC public (beaucoup plus lent, 1 appel par
transaction). Réutilise la logique de normalisation de public_rpc_client.py,
car Shyft peut renvoyer la transaction brute (raw) dans le même format que
l'API Solana standard (enable_raw=true).

Limite connue du tier gratuit Shyft: l'historique n'est conservé que ~3-4
jours. Largement suffisant pour la découverte d'early buyers et le monitoring
temps réel, plus limité pour juger l'ancienneté complète d'un wallet.
"""
import httpx
from datetime import datetime, timezone
from config import config
from services.public_rpc_client import normalize_for_wallet, extract_token_buyers

SHYFT_BASE_URL = "https://api.shyft.to/sol/v1/wallet/transaction_history"


async def _fetch_page(address: str, tx_num: int = 100, before_tx_signature: str | None = None) -> list[dict]:
    if not config.SHYFT_API_KEY:
        return []

    params = {
        "network": "mainnet-beta",
        "wallet": address,
        "tx_num": tx_num,
        "enable_raw": "true",
    }
    if before_tx_signature:
        params["before_tx_signature"] = before_tx_signature

    headers = {"x-api-key": config.SHYFT_API_KEY}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(SHYFT_BASE_URL, params=params, headers=headers)
        except httpx.RequestError:
            return []
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        return data.get("result", []) or []


def _to_timestamp(item: dict) -> int | None:
    raw = item.get("raw") or {}
    if raw.get("blockTime"):
        return raw["blockTime"]
    ts = item.get("timestamp")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return None


def _signature(item: dict) -> str | None:
    sigs = item.get("signatures") or []
    return sigs[0] if sigs else None


async def get_wallet_transactions(address: str, limit: int = 100) -> list[dict]:
    items = await _fetch_page(address, tx_num=min(limit, 100))
    txs = []
    for item in items:
        raw = item.get("raw")
        sig = _signature(item)
        if not raw or not sig:
            continue
        normalized = normalize_for_wallet(raw, sig, address)
        if normalized:
            txs.append(normalized)
    return txs


async def get_wallet_transaction_history(address: str, max_pages: int = 3, page_size: int = 100) -> list[dict]:
    all_txs = []
    before_sig = None

    for _ in range(max_pages):
        items = await _fetch_page(address, tx_num=page_size, before_tx_signature=before_sig)
        if not items:
            break

        for item in items:
            raw = item.get("raw")
            sig = _signature(item)
            if not raw or not sig:
                continue
            normalized = normalize_for_wallet(raw, sig, address)
            if normalized:
                all_txs.append(normalized)

        last_sig = _signature(items[-1])
        if not last_sig:
            break
        before_sig = last_sig

    return all_txs


async def get_token_early_buyers(
    token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int, max_pages: int = 5,
) -> tuple[list[dict], str]:
    mint_ts = mint_timestamp.timestamp()
    cutoff = mint_ts + (window_minutes * 60)

    all_buyers = []
    before_sig = None
    stop_reason = "max_pages_reached"

    for _ in range(max_pages):
        items = await _fetch_page(token_address, tx_num=100, before_tx_signature=before_sig)
        if not items:
            stop_reason = "empty_page"
            break

        page_timestamps = [t for t in (_to_timestamp(i) for i in items) if t is not None]
        if not page_timestamps:
            stop_reason = "no_timestamps"
            break
        oldest_ts = min(page_timestamps)

        for item in items:
            ts = _to_timestamp(item)
            if ts is None or not (mint_ts <= ts <= cutoff):
                continue
            raw = item.get("raw")
            sig = _signature(item)
            if not raw or not sig:
                continue
            all_buyers.extend(extract_token_buyers(raw, sig, token_address))

        last_sig = _signature(items[-1])
        if not last_sig:
            stop_reason = "no_signature"
            break
        before_sig = last_sig

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

    return early_buyers, f"shyft:{stop_reason}"
