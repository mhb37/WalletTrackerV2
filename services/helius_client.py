"""
Client pour l'API Helius (transactions parsées + RPC).
Doc: https://docs.helius.dev/
"""
import logging
import httpx
from datetime import datetime, timezone
from config import config

logger = logging.getLogger("wallet-scorer")


class HeliusClient:
    def __init__(self):
        self.api_key = config.HELIUS_API_KEY
        self.timeout = 20.0

    async def get_wallet_transactions(self, address: str, limit: int = 100) -> list[dict]:
        """Récupère les transactions parsées les plus récentes d'un wallet."""
        url = config.HELIUS_TX_URL.format(address=address)
        params = {"api-key": self.api_key, "limit": limit}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            return resp.json()

    async def get_token_early_buyers(
        self, token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int,
        max_pages: int = 15,
    ) -> list[dict]:
        """
        Récupère les premiers acheteurs d'un token dans une fenêtre de temps donnée
        après sa création, via les transactions du token (swaps).

        Helius renvoie les transactions du plus récent au plus ancien. Si le token
        existe depuis longtemps, la fenêtre "early" n'est PAS dans les 100 dernières
        transactions -> il faut paginer en arrière (paramètre "before") jusqu'à
        atteindre la période de création du token.

        Retourne une liste de {wallet, timestamp, token_amount, tx_signature}.
        """
        url = config.HELIUS_TX_URL.format(address=token_address)
        mint_ts = mint_timestamp.timestamp()
        cutoff = mint_ts + (window_minutes * 60)
        age_hours = (datetime.now(timezone.utc).timestamp() - mint_ts) / 3600
        logger.info(
            f"[early_buyers] {token_address[:8]}...: mint_time={mint_timestamp.isoformat()}, "
            f"age_h={age_hours:.1f}, fenetre_min={window_minutes}"
        )

        matched_txs = []
        before_sig = None
        import asyncio as _asyncio

        stop_reason = "max_pages_reached"
        swap_type_seen = set()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for page_num in range(max_pages):
                params = {"api-key": self.api_key, "limit": 100}
                if before_sig:
                    params["before"] = before_sig

                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    stop_reason = f"http_error_{resp.status_code}"
                    logger.warning(
                        f"[early_buyers] {token_address[:8]}... page {page_num}: "
                        f"HTTP {resp.status_code}, arrêt"
                    )
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
                        swap_type_seen.add(tx_type)
                    ts = tx.get("timestamp")
                    if ts is None:
                        continue
                    if mint_ts <= ts <= cutoff and tx_type == "SWAP":
                        matched_txs.append(tx)

                before_sig = txs[-1].get("signature")

                if oldest_ts_in_page < mint_ts:
                    stop_reason = "passed_mint_time"
                    break
                if oldest_ts_in_page <= cutoff:
                    stop_reason = "reached_window"
                    break

                await _asyncio.sleep(0.15)  # ménage le rate limit Helius free tier

        logger.info(
            f"[early_buyers] {token_address[:8]}...: raison_arret={stop_reason}, "
            f"tx_matchees={len(matched_txs)}, types_vus={list(swap_type_seen)[:5]}"
        )

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

        return early_buyers

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

                resp = await client.get(
                    config.HELIUS_TX_URL.format(address=address), params=params
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

                await _asyncio.sleep(0.12)

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
