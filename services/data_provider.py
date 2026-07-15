"""
Façade unique utilisée par discovery/scoring/monitor. Essaie toujours Helius en
premier (plus riche, plus rapide). Si Helius est rate-limité ou à quota, bascule
automatiquement et de façon transparente sur le RPC public Solana pour CET appel
précis, sans jamais faire planter le bot. Dès que le quota Helius revient, tous
les appels y retournent automatiquement (pas de "mode" à réactiver à la main).
"""
import logging
from datetime import datetime
from services.helius_client import helius_client, HeliusRateLimited
from services import public_rpc_client

logger = logging.getLogger("wallet-scorer")


async def get_wallet_transactions(address: str, limit: int = 100) -> list[dict]:
    try:
        return await helius_client.get_wallet_transactions(address, limit=limit)
    except HeliusRateLimited:
        logger.warning(f"[fallback] Helius rate-limité -> RPC public (get_wallet_transactions {address[:8]}...)")
        return await public_rpc_client.get_wallet_transactions(address, limit=min(limit, 30))


async def get_token_early_buyers(
    token_address: str, mint_timestamp: datetime, window_minutes: int, max_buyers: int, max_pages: int = 15,
) -> tuple[list[dict], str]:
    try:
        return await helius_client.get_token_early_buyers(
            token_address, mint_timestamp, window_minutes, max_buyers, max_pages=max_pages
        )
    except HeliusRateLimited:
        logger.warning(f"[fallback] Helius rate-limité -> RPC public (early_buyers {token_address[:8]}...)")
        return await public_rpc_client.get_token_early_buyers(
            token_address, mint_timestamp, window_minutes, max_buyers, max_pages=5
        )


async def get_wallet_transaction_history(address: str, max_pages: int = 5) -> list[dict]:
    try:
        return await helius_client.get_wallet_transaction_history(address, max_pages=max_pages)
    except HeliusRateLimited:
        logger.warning(f"[fallback] Helius rate-limité -> RPC public (history {address[:8]}...)")
        return await public_rpc_client.get_wallet_transaction_history(address, max_pages=3, page_size=50)
