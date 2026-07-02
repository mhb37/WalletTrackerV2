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

COMMANDS_MENU = [
    {"command": "discovery", "description": "Lancer un cycle de découverte"},
    {"command": "scoring", "description": "Lancer un cycle de scoring"},
    {"command": "top", "description": "Top 10 wallets scorés"},
    {"command": "watchlist", "description": "Wallets actuellement suivis"},
    {"command": "help", "description": "Voir toutes les commandes"},
]


def main_keyboard() -> dict:
    """Clavier de boutons toujours visible en bas de Telegram."""
    return {
        "keyboard": [
            ["🔍 Discovery", "📊 Scoring"],
            ["🏆 Top wallets", "👀 Watchlist"],
            ["❓ Aide"],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


async def set_bot_commands():
    """Enregistre le menu '/' natif de Telegram avec descriptions (autocomplete)."""
    if not config.TELEGRAM_BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{API_BASE}/setMyCommands", json={"commands": COMMANDS_MENU})


async def send_message(text: str, chat_id: str | None = None, reply_markup: dict | None = None):
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not config.TELEGRAM_BOT_TOKEN or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{API_BASE}/sendMessage", json=payload)


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


BUTTON_TO_COMMAND = {
    "🔍 discovery": "/discovery",
    "📊 scoring": "/scoring",
    "🏆 top wallets": "/top",
    "👀 watchlist": "/watchlist",
    "❓ aide": "/help",
}

HELP_TEXT = (
    "🤖 <b>Wallet Scorer — commandes</b>\n\n"
    "🔍 /discovery — lance un cycle de découverte de nouveaux wallets\n"
    "📊 /scoring — recalcule le score de tous les wallets suivis\n"
    "🏆 /top — top 10 wallets scorés\n"
    "👀 /watchlist — wallets actuellement suivis (score ≥ seuil)\n"
    "📍 /wallet &lt;adresse&gt; — détails d'un wallet précis\n\n"
    "Utilise les boutons en bas de l'écran, ou tape les commandes directement."
)


async def handle_command(command: str, chat_id: str) -> None:
    # les boutons du clavier envoient leur libellé exact -> on le convertit en commande
    normalized = BUTTON_TO_COMMAND.get(command.strip().lower(), command.strip())

    db = SessionLocal()
    try:
        if normalized.startswith("/start") or normalized.startswith("/help"):
            await send_message(HELP_TEXT, chat_id, reply_markup=main_keyboard())

        elif normalized.startswith("/discovery"):
            await send_message("🔍 Discovery en cours...", chat_id)
            from services.discovery import run_discovery_cycle
            result = await run_discovery_cycle()
            await send_message(
                f"✅ Discovery terminée\n"
                f"Tokens scannés: {result['tokens_scanned']}\n"
                f"Nouveaux wallets trouvés: {result['new_wallets_found']}",
                chat_id,
            )

        elif normalized.startswith("/scoring"):
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

        elif normalized.startswith("/top"):
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

        elif normalized.startswith("/watchlist"):
            wallets = db.query(Wallet).filter(Wallet.is_watchlisted == True).all()  # noqa: E712
            if not wallets:
                await send_message("Watchlist vide pour l'instant.", chat_id)
                return
            lines = [_format_wallet_line(w) for w in wallets]
            await send_message("👀 <b>Watchlist</b>\n" + "\n".join(lines), chat_id)

        elif normalized.startswith("/wallet"):
            parts = normalized.split()
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
            await send_message(HELP_TEXT, chat_id, reply_markup=main_keyboard())
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
        if text.startswith("/") or text.strip().lower() in BUTTON_TO_COMMAND:
            await handle_command(text, chat_id)

    return new_offset
