"""
Bot Telegram minimal: envoie des alertes et répond à quelques commandes
(/top, /wallet <address>, /watchlist). Polling simple, pas de webhook nécessaire.
"""
import asyncio
import httpx
from sqlalchemy import desc
from database import SessionLocal
from models import Wallet
from config import config

API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

COMMANDS_MENU = [
    {"command": "discovery", "description": "Lancer un cycle de découverte"},
    {"command": "scoring", "description": "Lancer un cycle de scoring"},
    {"command": "monitor", "description": "Vérifier les wallets watchlistés maintenant"},
    {"command": "paper", "description": "Voir le portefeuille de paper trading"},
    {"command": "trades", "description": "Journal détaillé des trades + espérance"},
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
            ["📡 Monitor", "💼 Paper"],
            ["📒 Trades", "❓ Aide"],
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
    return f"• <code>{w.address}</code>\n  score {w.score:.0f} | WR {w.win_rate:.0%} | ROI x{w.avg_roi_multiple:.2f}"


BUTTON_TO_COMMAND = {
    "🔍 discovery": "/discovery",
    "📊 scoring": "/scoring",
    "📡 monitor": "/monitor",
    "💼 paper": "/paper",
    "📒 trades": "/trades",
    "🏆 top wallets": "/top",
    "👀 watchlist": "/watchlist",
    "❓ aide": "/help",
}

HELP_TEXT = (
    "🤖 <b>Wallet Scorer — commandes</b>\n\n"
    "🔍 /discovery — lance un cycle de découverte de nouveaux wallets\n"
    "📊 /scoring — recalcule le score de tous les wallets suivis\n"
    "📡 /monitor — vérifie les wallets watchlistés maintenant (nouveaux achats)\n"
    "💼 /paper — portefeuille de paper trading (positions, PnL)\n"
    "📒 /trades — journal détaillé des trades + espérance mathématique\n"
    "🏆 /top — top 10 wallets scorés\n"
    "👀 /watchlist — wallets actuellement suivis (score ≥ seuil)\n"
    "📍 /wallet &lt;adresse&gt; — détails d'un wallet précis\n\n"
    "Utilise les boutons en bas de l'écran, ou tape les commandes directement.\n\n"
    "📡 Le monitoring tourne aussi automatiquement en fond: dès qu'un wallet "
    "watchlisté fait un nouvel achat, un signal est mis en observation, et une "
    "position paper s'ouvre seulement si un bon point d'entrée se présente."
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

            lines = [
                f"✅ Discovery terminée",
                f"Tokens scannés: {result['tokens_scanned']}",
                f"Nouveaux wallets trouvés: {result['new_wallets_found']}",
            ]

            source_counts = result.get("source_counts")
            if source_counts:
                lines.append(
                    f"\n<b>Sources DexScreener:</b>\n"
                    f"• latest_boosted: {source_counts.get('latest_boosted', '?')}\n"
                    f"• top_boosted: {source_counts.get('top_boosted', '?')}\n"
                    f"• profiles: {source_counts.get('profiles', '?')}\n"
                    f"• combiné unique (Solana): {source_counts.get('combined_solana', '?')}"
                )

            diagnostics = result.get("diagnostics", [])
            if diagnostics:
                lines.append("\n<b>Détail par token:</b>")
                for d in diagnostics[:30]:
                    lines.append(f"• {d}")
                if len(diagnostics) > 30:
                    lines.append(f"... et {len(diagnostics) - 30} de plus")

            await send_message("\n".join(lines), chat_id)

        elif normalized.startswith("/scoring"):
            await send_message("📊 Scoring en cours...", chat_id)
            from services.scoring import run_scoring_cycle
            results = await run_scoring_cycle()
            passed = [r for r in results if r.get("passed")]

            tally = {}
            for r in results:
                if r.get("passed"):
                    continue
                for reason in r.get("reasons", []):
                    category = reason.split(" ")[0]
                    tally[category] = tally.get(category, 0) + 1

            lines = [
                f"✅ Scoring terminé",
                f"Wallets traités: {len(results)}",
                f"Wallets qui passent les filtres: {len(passed)}",
            ]
            if tally:
                lines.append("\nRaisons de rejet:")
                for category, count in sorted(tally.items(), key=lambda x: -x[1]):
                    lines.append(f"• {category}: {count}")

            await send_message("\n".join(lines), chat_id)

        elif normalized.startswith("/trades"):
            from services.paper_trading import get_trade_journal, get_expectancy_stats
            journal = await get_trade_journal(limit=10)
            stats = await get_expectancy_stats()

            lines = ["📒 <b>Journal des trades</b>\n"]

            if stats["sample_size"] == 0:
                lines.append("Aucun trade clôturé pour l'instant.")
            else:
                reliability = "✅ fiable" if stats["reliable"] else f"⚠️ encore trop peu de données (seuil: 20)"
                lines.append(
                    f"<b>Espérance mathématique</b> ({stats['sample_size']} trades clôturés, {reliability})\n"
                    f"Win rate: {stats['win_rate']}%\n"
                    f"Gain moyen: {stats['avg_win_pct']:+.1f}%\n"
                    f"Perte moyenne: {stats['avg_loss_pct']:+.1f}%\n"
                    f"Espérance par trade: {stats['expectancy_pct']:+.2f}%\n"
                )

            if journal:
                lines.append("\n<b>10 derniers trades:</b>")
                for t in journal:
                    status_icon = "🟢" if t["status"] == "closed" and t["pnl_pct"] > 0 else (
                        "🔴" if t["status"] == "closed" else "🟡"
                    )
                    retr = f"{t['retracement_pct_at_entry']:.0%}" if t['retracement_pct_at_entry'] is not None else "?"
                    delay = f"{t['entry_delay_seconds']/60:.0f}min" if t['entry_delay_seconds'] is not None else "?"
                    score = f"{t['wallet_score_at_entry']:.0f}" if t['wallet_score_at_entry'] is not None else "?"
                    lines.append(
                        f"{status_icon} <code>{t['token_address'][:6]}...</code> "
                        f"{t['pnl_pct']:+.1f}% | retr={retr} délai={delay} score={score}"
                    )

            await send_message("\n".join(lines), chat_id)

        elif normalized.startswith("/paper"):
            from services.paper_trading import get_portfolio_summary
            summary = await get_portfolio_summary()
            pnl = summary["realized_pnl_usd"]
            pnl_sign = "🟢" if pnl >= 0 else "🔴"
            await send_message(
                f"💼 <b>Portefeuille paper trading</b>\n\n"
                f"Capital initial: ${summary['initial_capital_usd']:.2f}\n"
                f"Capital actuel: ${summary['current_capital_usd']:.2f}\n"
                f"{pnl_sign} PnL réalisé: ${pnl:+.2f}\n\n"
                f"Positions ouvertes: {summary['open_positions']}\n"
                f"Positions clôturées: {summary['closed_positions']}\n"
                f"Capital déployé actuellement: ${summary['capital_deployed_usd']:.2f}",
                chat_id,
            )

        elif normalized.startswith("/monitor"):
            await send_message("📡 Vérification des wallets watchlistés...", chat_id)
            from services.monitor import run_monitor_cycle
            result = await run_monitor_cycle()
            await send_message(
                f"✅ Monitoring terminé\n"
                f"Wallets vérifiés: {result['wallets_checked']}\n"
                f"Alertes envoyées: {result['alerts_sent']}",
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
            # tâche de fond: une commande lente (scoring en mode dégradé, etc.)
            # ne doit jamais empêcher de lire les messages/boutons suivants
            asyncio.create_task(handle_command(text, chat_id))

    return new_offset
