"""
Détection de rugs (Phase 2, heuristique simple): un token dont la liquidité
actuelle est tombée sous un seuil est considéré comme mort/rug, quel que soit
son historique. Utilisé pour affiner rug_avoidance_score dans le scoring.
"""
import asyncio
from services.dexscreener_client import dexscreener_client
from config import config


async def get_current_liquidity_usd(token_address: str) -> float | None:
    pairs = await dexscreener_client.get_token_pairs(token_address)
    if not pairs:
        return None
    best_pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
    return (best_pair.get("liquidity") or {}).get("usd", 0)


async def is_likely_rug(token_address: str) -> bool | None:
    """
    Retourne True si le token semble mort (liquidité très faible), False s'il
    semble encore vivant, ou None si aucune donnée n'est disponible (on ne
    pénalise pas dans le doute).
    """
    liquidity = await get_current_liquidity_usd(token_address)
    if liquidity is None:
        return None
    return liquidity < config.RUG_LIQUIDITY_THRESHOLD_USD


async def compute_rug_stats(token_addresses: list[str]) -> dict:
    """
    Vérifie une liste de tokens (limitée à MAX_TOKENS_TO_CHECK_FOR_RUGS pour
    contrôler le budget d'appels API) et retourne le nombre de rugs détectés
    ainsi que le nombre de tokens réellement vérifiés (pour calculer un ratio).
    """
    checked = token_addresses[: config.MAX_TOKENS_TO_CHECK_FOR_RUGS]
    rug_count = 0
    checked_count = 0

    for token_address in checked:
        result = await is_likely_rug(token_address)
        if result is None:
            continue  # pas de donnée -> on ne compte ni pour ni contre
        checked_count += 1
        if result:
            rug_count += 1
        await asyncio.sleep(0.1)  # ménage le rate limit DexScreener

    return {"rug_count": rug_count, "checked_count": checked_count}
