"""
Bot Telegram minimal: envoie des alertes et répond à quelques commandes
(/top, /wallet <address>, /watchlist). Polling simple, pas de webhook nécessaire.
"""
import httpx
from sqlalchemy import desc
from database import SessionLocal
from models import Wallet
from config import config

API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


async def send_message(text: str, chat_id: str | None = None):
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not config.TELEGRAM_BOT_TOKEN or not chat_id:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"{API_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )


async def notify_new_watchlist_wallet(wallet: Wallet):
    text = (
        f"🎯 <b>Nouveau wallet watchlist</b>\n"
        f"Adresse: <code>{wallet.address}</code>\n"
        f"Score: {wallet.score}/100\n"
        f"Win rate: {wallet.win_rate:.0%}\n"
        f"ROI moyen: x{wallet.avg_roi_multiple:.2f}\n"
        f"Trades: {wallet.total_trades}"
    )
    await send_message(text)


def _format_wallet_line(w: Wallet) -> str:
    return f"• <code>{w.address[:6]}...{w.address[-4:]}</code> — score {w.score:.0f} | WR {w.win_rate:.0%} | ROI x{w.avg_roi_multiple:.2f}"


async def handle_command(command: str, chat_id: str) -> None:
    db = SessionLocal()
    try:
        if command.startswith("/discovery"):
            await send_message("🔍 Discovery en cours...", chat_id)
            from services.discovery import run_discovery_cycle
            result = await run_discovery_cycle()
            await send_message(
                f"✅ Discovery terminée\n"
                f"Tokens scannés: {result['tokens_scanned']}\n"
                f"Nouveaux wallets trouvés: {result['new_wallets_found']}",
                chat_id,
            )

        elif command.startswith("/scoring"):
            await send_message("📊 Scoring en cours...", chat_id)
            from services.scoring import run_scoring_cycle
            results = await run_scoring_cycle()
            passed = [r for r in results if r.get("passed")]
            await send_message(
                f"✅ Scoring terminé\n"
                f"Wallets traités: {len(results)}\n"
                f"Wallets qui passent les filtres: {len(passed)}",
                chat_id,
            )

        elif command.startswith("/top"):
            wallets = (
                db.query(Wallet)
                .filter(Wallet.passed_hard_filters == True)  # noqa: E712
                .order_by(desc(Wallet.score))
                .limit(10)
                .all()
            )
            if not wallets:
                await send_message("Aucun wallet scoré pour l'instant.", chat_id)
                return
            lines = [_format_wallet_line(w) for w in wallets]
            await send_message("🏆 <b>Top wallets</b>\n" + "\n".join(lines), chat_id)

        elif command.startswith("/watchlist"):
            wallets = db.query(Wallet).filter(Wallet.is_watchlisted == True).all()  # noqa: E712
            if not wallets:
                await send_message("Watchlist vide pour l'instant.", chat_id)
                return
            lines = [_format_wallet_line(w) for w in wallets]
            await send_message("👀 <b>Watchlist</b>\n" + "\n".join(lines), chat_id)

        elif command.startswith("/wallet"):
            parts = command.split()
            if len(parts) < 2:
                await send_message("Usage: /wallet <adresse>", chat_id)
                return
            address = parts[1]
            w = db.query(Wallet).filter(Wallet.address == address).first()
            if not w:
                await send_message("Wallet inconnu.", chat_id)
                return
            await send_message(
                f"<code>{w.address}</code>\nScore: {w.score}\nWin rate: {w.win_rate:.0%}\n"
                f"ROI moyen: x{w.avg_roi_multiple:.2f}\nTrades: {w.total_trades}\n"
                f"PnL réalisé: {w.total_realized_pnl_sol:.2f} SOL",
                chat_id,
            )
        else:
            await send_message(
                "Commandes disponibles:\n"
                "/discovery — lance un cycle de découverte\n"
                "/scoring — lance un cycle de scoring\n"
                "/top — top 10 wallets scorés\n"
                "/watchlist — wallets actuellement suivis\n"
                "/wallet <adresse> — détails d'un wallet",
                chat_id,
            )
    finally:
        db.close()


async def poll_updates_once(offset: int | None = None) -> int | None:
    """Récupère les nouveaux messages Telegram et traite les commandes. Retourne le nouvel offset."""
    if not config.TELEGRAM_BOT_TOKEN:
        return offset
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_BASE}/getUpdates", params=params)
        if resp.status_code != 200:
            return offset
        data = resp.json()

    updates = data.get("result", [])
    new_offset = offset
    for update in updates:
        new_offset = update["update_id"] + 1
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))
        if text.startswith("/"):
            await handle_command(text, chat_id)

    return new_offset
