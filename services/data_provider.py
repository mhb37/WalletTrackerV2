"""
Façade unique utilisée par discovery/scoring/monitor. Ordre de priorité:
  1. Helius (le plus riche, le plus rapide)
  2. Shyft (rapide aussi, données parsées, mais historique limité à ~3-4 jours)
  3. RPC public Solana (dernier recours: gratuit sans compte, mais lent)

Si un niveau échoue (rate limit / pas configuré / pas de données), on bascule
automatiquement au suivant, sans jamais faire planter le bot. Dès qu'un niveau
plus prioritaire redevient disponible (ex: quota Helius renouvelé), les appels
y retournent automatiquement -- pas de "mode" à réactiver à la main.
"""
import logging
from datetime import datetime
from services.helius_client import helius_client, HeliusRateLimited
from services import shyft_client
from services import public_rpc_client
from config import config

logger = logging.getLogger("wallet-scorer")


async def get_wallet_transactions(address: str, limit: int = 100) -> list[dict]:
    try:
        return await helius_client.get_wallet_transactions(address, limit=limit)
    except HeliusRateLimited:
        logger.warning(f"[fallback] Helius rate-limité -> Shyft (get_wallet_transactions {address[:8]}...)")
        if config.SHYFT_API_KEY:
            txs = await shyft_client.get_wallet_transactions(address, limit=limit)
            if txs:
                return txs
        logger.warning(f"[fallback] Shyft indisponible -> RPC public (get_wallet_transactions {address[:8]}...)")
        return await public_rpc_client.get_wallet_transactions(address, limit=min(limit, 10))


async def get_token_early_buyers(
    token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int, max_pages: int = 15,
) -> tuple[list[dict], str]:
    try:
        return await helius_client.get_token_early_buyers(
            token_address, mint_timestamp, window_minutes, max_buyers, max_pages=max_pages
        )
    except HeliusRateLimited:
        logger.warning(f"[fallback] Helius rate-limité -> Shyft (early_buyers {token_address[:8]}...)")
        if config.SHYFT_API_KEY:
            buyers, detail = await shyft_client.get_token_early_buyers(
                token_address, mint_timestamp, window_minutes, max_buyers, max_pages=5
            )
            if buyers or "empty_page" not in detail:
                return buyers, detail
        logger.warning(f"[fallback] Shyft indisponible -> RPC public (early_buyers {token_address[:8]}...)")
        return await public_rpc_client.get_token_early_buyers(
            token_address, mint_timestamp, window_minutes, max_buyers, max_pages=2
        )


async def get_wallet_transaction_history(address: str, max_pages: int = 5) -> tuple[list[dict], bool]:
    """Retourne (transactions, a_utilise_le_fallback_degrade). Shyft compte comme
    fallback "riche" (pas dégradé) car les données sont toujours parsées et
    fiables, juste limitées dans le temps -- seul le RPC public est marqué
    dégradé, car c'est là que la qualité des données devient vraiment incertaine."""
    try:
        txs = await helius_client.get_wallet_transaction_history(address, max_pages=max_pages)
        return txs, False
    except HeliusRateLimited:
        logger.warning(f"[fallback] Helius rate-limité -> Shyft (history {address[:8]}...)")
        if config.SHYFT_API_KEY:
            txs = await shyft_client.get_wallet_transaction_history(address, max_pages=3, page_size=100)
            if txs:
                return txs, False
        logger.warning(f"[fallback] Shyft indisponible -> RPC public (history {address[:8]}...)")
        txs = await public_rpc_client.get_wallet_transaction_history(address, max_pages=2, page_size=20)
        return txs, True
