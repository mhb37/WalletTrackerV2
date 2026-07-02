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
        self, token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int
    ) -> list[dict]:
        """
        Récupère les premiers acheteurs d'un token dans une fenêtre de temps donnée
        après sa création, via les transactions du token (swaps).
        Retourne une liste de {wallet, timestamp, sol_amount, tx_signature}.
        """
        url = config.HELIUS_TX_URL.format(address=token_address)
        params = {"api-key": self.api_key, "limit": 100}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            txs = resp.json()

        buyers = []
        cutoff = mint_timestamp.timestamp() + (window_minutes * 60)

        for tx in txs:
            ts = tx.get("timestamp")
            if ts is None or ts > cutoff:
                continue
            if tx.get("type") not in ("SWAP",):
                continue

            # Helius renvoie tokenTransfers pour identifier acheteur + montants
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
