"""
Client DexScreener (pas de clé API requise).
Doc: https://docs.dexscreener.com/api/reference
"""
import httpx
from datetime import datetime, timezone
from config import config


class DexScreenerClient:
    def __init__(self):
        self.timeout = 20.0

    async def get_boosted_tokens(self) -> list[dict]:
        """Tokens récemment boostés/en avant sur DexScreener (bon proxy pour 'ça bouge')."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(config.DEXSCREENER_TOKEN_BOOSTS_URL)
            if resp.status_code != 200:
                return []
            return resp.json()

    async def get_top_boosted_tokens(self) -> list[dict]:
        """Deuxième flux DexScreener (différent de 'latest'): plus de variété de candidats."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(config.DEXSCREENER_TOKEN_BOOSTS_TOP_URL)
            if resp.status_code != 200:
                return []
            return resp.json()

    async def get_token_profiles(self) -> list[dict]:
        """Troisième flux DexScreener: tokens dont le profil vient d'être rempli (souvent tout jeunes)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(config.DEXSCREENER_TOKEN_PROFILES_URL)
            if resp.status_code != 200:
                return []
            return resp.json()

    async def get_token_pairs(self, token_address: str) -> list[dict]:
        """Détails de marché (prix, liquidité, volume, mcap) pour un token Solana."""
        url = config.DEXSCREENER_PAIRS_URL.format(address=token_address)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            pairs = data.get("pairs") or []
            return [p for p in pairs if p.get("chainId") == "solana"]

    async def find_pumped_solana_tokens(self, min_pump_multiple: float) -> list[dict]:
        """
        Combine plusieurs flux DexScreener (latest boosts, top boosts, profiles)
        avec leurs données de marché pour ne garder que ceux qui ont réellement
        pump (priceChange sur 24h en positif fort). Plusieurs flux = plus de
        candidats par cycle, sans jamais assouplir le seuil de qualité.
        """
        latest_boosted = await self.get_boosted_tokens()
        top_boosted = await self.get_top_boosted_tokens()
        profiles = await self.get_token_profiles()

        # dédoublonne par adresse de token, en gardant l'ordre d'apparition
        seen_addresses = set()
        combined = []
        for item in latest_boosted + top_boosted + profiles:
            token_address = item.get("tokenAddress")
            if not token_address or token_address in seen_addresses:
                continue
            seen_addresses.add(token_address)
            combined.append(item)

        candidates = []

        for item in combined:
            if item.get("chainId") != "solana":
                continue
            token_address = item.get("tokenAddress")
            if not token_address:
                continue

            pairs = await self.get_token_pairs(token_address)
            if not pairs:
                continue

            # prend la pair avec la plus grosse liquidité
            best_pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
            price_change = (best_pair.get("priceChange") or {}).get("h24", 0) or 0
            pump_multiple = 1 + (price_change / 100)

            if pump_multiple < min_pump_multiple:
                continue

            pair_created_at = best_pair.get("pairCreatedAt")
            created_dt = (
                datetime.fromtimestamp(pair_created_at / 1000, tz=timezone.utc)
                if pair_created_at else None
            )

            candidates.append({
                "token_address": token_address,
                "symbol": best_pair.get("baseToken", {}).get("symbol"),
                "created_at": created_dt,
                "pump_multiple": pump_multiple,
                "liquidity_usd": (best_pair.get("liquidity") or {}).get("usd", 0),
                "current_price_usd": float(best_pair.get("priceUsd", 0) or 0),
            })

        return candidates


dexscreener_client = DexScreenerClient()
