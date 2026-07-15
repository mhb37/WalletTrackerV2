"""
Client pour l'API Helius (transactions parsées + RPC).
Doc: https://docs.helius.dev/
"""
import asyncio
import time
import httpx
from datetime import datetime, timezone
from config import config

# Throttle global: partagé par TOUTES les boucles (discovery, scoring, monitor,
# paper trading) pour qu'elles ne dépassent jamais ensemble le rate limit Helius
# free tier, même si elles tournent en parallèle.
_throttle_lock = asyncio.Lock()
_last_call_time = 0.0
MIN_INTERVAL_SECONDS = 0.25


class HeliusRateLimited(Exception):
    """Levée quand Helius répond 429 après épuisement des tentatives (rate limit ou quota)."""
    pass


async def _throttle():
    global _last_call_time
    async with _throttle_lock:
        now = time.monotonic()
        wait = MIN_INTERVAL_SECONDS - (now - _last_call_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_time = time.monotonic()


async def _get_with_retry(client: httpx.AsyncClient, url: str, params: dict, max_retries: int = 2) -> httpx.Response:
    """GET avec throttle global + retry avec backoff. Lève HeliusRateLimited si tout échoue en 429."""
    for attempt in range(max_retries + 1):
        await _throttle()
        resp = await client.get(url, params=params)
        if resp.status_code != 429:
            return resp
        if attempt < max_retries:
            await asyncio.sleep(1.5 * (attempt + 1))
    raise HeliusRateLimited(f"429 persistant après {max_retries + 1} tentatives sur {url}")


class HeliusClient:
    def __init__(self):
        self.api_key = config.HELIUS_API_KEY
        self.timeout = 20.0

    async def get_wallet_transactions(self, address: str, limit: int = 100) -> list[dict]:
        """Récupère les transactions parsées les plus récentes d'un wallet."""
        url = config.HELIUS_TX_URL.format(address=address)
        params = {"api-key": self.api_key, "limit": limit}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await _get_with_retry(client, url, params)
            if resp.status_code != 200:
                return []
            return resp.json()

    async def get_token_early_buyers(
        self, token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int,
        max_pages: int = 15,
    ) -> tuple[list[dict], str]:
        """
        Récupère les premiers acheteurs d'un token dans une fenêtre de temps donnée
        après sa création, via les transactions du token (swaps).

        Retourne (liste_de_buyers, raison_arret) où raison_arret explique pourquoi
        la pagination s'est arrêtée (utile pour diagnostiquer sans dépendre des logs
        serveur, renvoyé directement dans les réponses Telegram).
        """
        url = config.HELIUS_TX_URL.format(address=token_address)
        mint_ts = mint_timestamp.timestamp()
        cutoff = mint_ts + (window_minutes * 60)

        matched_txs = []
        before_sig = None
        import asyncio as _asyncio

        stop_reason = "max_pages_reached"
        types_seen = set()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(max_pages):
                params = {"api-key": self.api_key, "limit": 100}
                if before_sig:
                    params["before"] = before_sig

                resp = await _get_with_retry(client, url, params)
                if resp.status_code != 200:
                    stop_reason = f"http_{resp.status_code}"
                    break
                txs = resp.json()
                if not txs:
                    stop_reason = "empty_page"
                    break

                page_timestamps = [t.get("timestamp") for t in txs if t.get("timestamp") is not None]
                if not page_timestamps:
                    stop_reason = "no_timestamps"
                    break
                oldest_ts_in_page = min(page_timestamps)

                for tx in txs:
                    tx_type = tx.get("type")
                    if tx_type:
                        types_seen.add(tx_type)
                    ts = tx.get("timestamp")
                    if ts is None:
                        continue
                    if mint_ts <= ts <= cutoff and tx_type == "SWAP":
                        matched_txs.append(tx)

                before_sig = txs[-1].get("signature")

                if oldest_ts_in_page < mint_ts:
                    stop_reason = "passed_mint"
                    break
                if oldest_ts_in_page <= cutoff:
                    stop_reason = "reached_window"
                    break

        buyers = []
        for tx in matched_txs:
            ts = tx.get("timestamp")
            for transfer in tx.get("tokenTransfers", []):
                if transfer.get("mint") == token_address and transfer.get("toUserAccount"):
                    buyers.append({
                        "wallet": transfer["toUserAccount"],
                        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                        "token_amount": transfer.get("tokenAmount", 0),
                        "tx_signature": tx.get("signature"),
                    })

        # trie par timestamp croissant, garde les N premiers wallets uniques
        buyers.sort(key=lambda b: b["timestamp"])
        seen = set()
        early_buyers = []
        for b in buyers:
            if b["wallet"] in seen:
                continue
            seen.add(b["wallet"])
            early_buyers.append(b)
            if len(early_buyers) >= max_buyers:
                break

        detail = f"{stop_reason}, tx_matchees={len(matched_txs)}, types={list(types_seen)[:3]}"
        return early_buyers, detail

    async def get_wallet_transaction_history(
        self, address: str, max_pages: int = 5, page_size: int = 100
    ) -> list[dict]:
        """
        Récupère un historique plus large qu'une seule page en paginant en arrière
        (jusqu'à max_pages x page_size transactions). S'arrête tôt une fois qu'on a
        clairement dépassé l'ancienneté minimale attendue pour un wallet, histoire de
        ne pas gaspiller des appels API sur les wallets très actifs qui ont des
        milliers de transactions.
        """
        import asyncio as _asyncio

        all_txs = []
        before_sig = None
        now_ts = datetime.now(timezone.utc).timestamp()
        comfortable_age_seconds = config.MIN_WALLET_AGE_DAYS * 3 * 86400

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(max_pages):
                params = {"api-key": self.api_key, "limit": page_size}
                if before_sig:
                    params["before"] = before_sig

                resp = await _get_with_retry(
                    client, config.HELIUS_TX_URL.format(address=address), params
                )
                if resp.status_code != 200:
                    break
                txs = resp.json()
                if not txs:
                    break

                all_txs.extend(txs)
                before_sig = txs[-1].get("signature")

                timestamps = [t.get("timestamp") for t in txs if t.get("timestamp") is not None]
                if not timestamps:
                    break
                oldest_ts = min(timestamps)

                if (now_ts - oldest_ts) >= comfortable_age_seconds:
                    break  # ancienneté largement prouvée, pas la peine de creuser plus loin

        return all_txs

    async def get_wallet_first_activity(self, address: str) -> datetime | None:
        """Estime l'âge du wallet via sa plus vieille transaction connue (approximation sur les 100 dernières)."""
        txs = await self.get_wallet_transactions(address, limit=100)
        if not txs:
            return None
        timestamps = [tx["timestamp"] for tx in txs if tx.get("timestamp")]
        if not timestamps:
            return None
        return datetime.fromtimestamp(min(timestamps), tz=timezone.utc)


helius_client = HeliusClient()
