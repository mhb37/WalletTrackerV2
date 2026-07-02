"""
Client pour l'API Helius (transactions parsées + RPC).
Doc: https://docs.helius.dev/
"""
import httpx
from datetime import datetime, timezone
from config import config


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

        matched_txs = []
        before_sig = None
        import asyncio as _asyncio

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(max_pages):
                params = {"api-key": self.api_key, "limit": 100}
                if before_sig:
                    params["before"] = before_sig

                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    break
                txs = resp.json()
                if not txs:
                    break

                page_timestamps = [t.get("timestamp") for t in txs if t.get("timestamp") is not None]
                if not page_timestamps:
                    break
                oldest_ts_in_page = min(page_timestamps)

                for tx in txs:
                    ts = tx.get("timestamp")
                    if ts is None:
                        continue
                    if mint_ts <= ts <= cutoff and tx.get("type") == "SWAP":
                        matched_txs.append(tx)

                before_sig = txs[-1].get("signature")

                # on a dépassé la création du token -> stop
                if oldest_ts_in_page < mint_ts:
                    break
                # on a atteint (et dépassé vers le bas) la fenêtre d'early buy -> stop
                if oldest_ts_in_page <= cutoff:
                    break

                await _asyncio.sleep(0.15)  # ménage le rate limit Helius free tier

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
