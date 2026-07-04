"""
Phase 2: surveillance temps réel des wallets watchlistés (score >= seuil).
Détecte leurs nouveaux achats et envoie une alerte Telegram avec les infos du token.
"""
from database import SessionLocal
from models import Wallet
from services.helius_client import helius_client
from services.dexscreener_client import dexscreener_client
from services.telegram_bot import send_message
from config import config

LAMPORTS_PER_SOL = 1_000_000_000


def _extract_buys(txs: list[dict], wallet_address: str) -> list[dict]:
    """Repère les achats (SOL sortant, token entrant) dans une liste de tx parsées Helius."""
    buys = []
    for tx in txs:
        if tx.get("type") != "SWAP":
            continue

        sol_out = 0.0
        for nt in tx.get("nativeTransfers", []):
            if nt.get("fromUserAccount") == wallet_address:
                sol_out += nt.get("amount", 0) / LAMPORTS_PER_SOL
        if sol_out <= 0:
            continue

        for tt in tx.get("tokenTransfers", []):
            if tt.get("toUserAccount") == wallet_address and tt.get("mint"):
                buys.append({
                    "token_address": tt["mint"],
                    "sol_spent": sol_out,
                    "signature": tx.get("signature"),
                })
    return buys


async def _build_alert_text(wallet: Wallet, buy: dict) -> str:
    token_address = buy["token_address"]
    pairs = await dexscreener_client.get_token_pairs(token_address)

    symbol = "?"
    price_usd = None
    liquidity_usd = None
    if pairs:
        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
        symbol = best.get("baseToken", {}).get("symbol", "?")
        price_usd = best.get("priceUsd")
        liquidity_usd = (best.get("liquidity") or {}).get("usd")

    lines = [
        "🟢 <b>Nouvel achat détecté</b>",
        f"Wallet: <code>{wallet.address[:6]}...{wallet.address[-4:]}</code> (score {wallet.score:.0f})",
        f"Token: {symbol} — <code>{token_address}</code>",
        f"Montant: {buy['sol_spent']:.3f} SOL",
    ]
    if price_usd is not None:
        lines.append(f"Prix: ${price_usd}")
    if liquidity_usd is not None:
        lines.append(f"Liquidité: ${liquidity_usd:,.0f}")
        if liquidity_usd < config.RUG_LIQUIDITY_THRESHOLD_USD:
            lines.append("⚠️ Liquidité très faible, prudence.")

    return "\n".join(lines)


async def run_monitor_cycle() -> dict:
    db = SessionLocal()
    try:
        wallets = db.query(Wallet).filter(Wallet.is_watchlisted == True).all()  # noqa: E712
        alerts_sent = 0

        for wallet in wallets:
            txs = await helius_client.get_wallet_transactions(wallet.address, limit=20)
            if not txs:
                continue

            newest_signature = txs[0].get("signature")

            if wallet.last_seen_signature:
                cutoff_index = None
                for i, tx in enumerate(txs):
                    if tx.get("signature") == wallet.last_seen_signature:
                        cutoff_index = i
                        break
                relevant_txs = txs[:cutoff_index] if cutoff_index is not None else txs
            else:
                # Première vérification pour ce wallet: pas d'alerte rétroactive,
                # on initialise juste le curseur.
                relevant_txs = []

            new_buys = _extract_buys(relevant_txs, wallet.address)

            for buy in new_buys:
                text = await _build_alert_text(wallet, buy)
                await send_message(text)
                alerts_sent += 1

            wallet.last_seen_signature = newest_signature
            db.commit()

        return {"wallets_checked": len(wallets), "alerts_sent": alerts_sent}
    finally:
        db.close()
