"""
Phase 3: paper trading. Ne copie jamais un achat à l'instant du signal (on perdrait
le temps de la transaction et on entrerait souvent sur un pic). À la place:
  1. Le signal (achat d'un wallet watchlisté) ouvre une fenêtre d'observation.
  2. On attend un retracement depuis le pic local avant d'entrer réellement.
  3. Une fois en position, sortie par paliers de take-profit + stop-loss qui
     devient "trailing" (suit le prix à la hausse) une fois en profit confirmé.
"""
from datetime import datetime, timezone, timedelta
from database import SessionLocal
from models import PendingSignal, PaperPosition, PaperFill
from services.dexscreener_client import dexscreener_client
from services.rug_detector import is_likely_rug
from services.telegram_bot import send_message
from config import config


async def _get_price_and_liquidity(token_address: str) -> tuple[float | None, float | None]:
    pairs = await dexscreener_client.get_token_pairs(token_address)
    if not pairs:
        return None, None
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
    price = float(best.get("priceUsd", 0) or 0) or None
    liquidity = (best.get("liquidity") or {}).get("usd")
    return price, liquidity


async def create_pending_signal(wallet_address: str, token_address: str, current_price_usd: float):
    """Appelé par monitor.py quand un wallet watchlisté vient d'acheter."""
    db = SessionLocal()
    try:
        signal = PendingSignal(
            wallet_address=wallet_address,
            token_address=token_address,
            signal_price_usd=current_price_usd,
            peak_price_usd=current_price_usd,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.PAPER_ENTRY_WINDOW_MINUTES),
        )
        db.add(signal)
        db.commit()
    finally:
        db.close()


async def _open_position(
    db, signal: PendingSignal, entry_price: float, retracement_pct: float, liquidity_usd: float | None
):
    from models import Wallet
    wallet = db.query(Wallet).filter(Wallet.address == signal.wallet_address).first()
    wallet_score = wallet.score if wallet else None

    now = datetime.now(timezone.utc)
    signal_created_at = signal.created_at
    if signal_created_at.tzinfo is None:
        signal_created_at = signal_created_at.replace(tzinfo=timezone.utc)
    entry_delay_seconds = (now - signal_created_at).total_seconds()

    position = PaperPosition(
        wallet_address=signal.wallet_address,
        token_address=signal.token_address,
        entry_price_usd=entry_price,
        initial_size_usd=config.PAPER_INITIAL_CAPITAL_USD * config.PAPER_POSITION_SIZE_PCT,
        peak_price_usd=entry_price,
        signal_created_at=signal.created_at,
        entry_delay_seconds=entry_delay_seconds,
        retracement_pct_at_entry=retracement_pct,
        wallet_score_at_entry=wallet_score,
        liquidity_usd_at_entry=liquidity_usd,
    )
    db.add(position)
    signal.status = "entered"
    db.commit()

    await send_message(
        f"🟩 <b>Position paper ouverte</b>\n"
        f"Token: <code>{signal.token_address}</code>\n"
        f"Entrée: ${entry_price:.8f}\n"
        f"Taille: ${position.initial_size_usd:.2f}\n"
        f"Retracement observé: {retracement_pct:.1%}\n"
        f"Délai signal→entrée: {entry_delay_seconds/60:.1f} min\n"
        f"Signal: <code>{signal.wallet_address[:6]}...{signal.wallet_address[-4:]}</code> (score {wallet_score or '?'})"
    )


async def process_pending_signals():
    """Vérifie les signaux en attente: cherche un retracement pour entrer, ou expire la fenêtre."""
    db = SessionLocal()
    try:
        signals = db.query(PendingSignal).filter(PendingSignal.status == "pending").all()
        now = datetime.now(timezone.utc)

        for signal in signals:
            expires_at = signal.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if now >= expires_at:
                signal.status = "expired"
                db.commit()
                continue

            price, liquidity = await _get_price_and_liquidity(signal.token_address)
            if price is None:
                continue  # pas de données ce coup-ci, on retente au prochain cycle

            if price > signal.peak_price_usd:
                signal.peak_price_usd = price
                db.commit()

            if liquidity is not None and liquidity < config.PAPER_MIN_LIQUIDITY_USD:
                continue  # liquidité insuffisante, on attend encore (peut remonter)

            drawdown_from_signal = (signal.signal_price_usd - price) / signal.signal_price_usd
            if drawdown_from_signal > config.PAPER_MAX_DRAWDOWN_FROM_SIGNAL_PCT:
                signal.status = "rejected"  # momentum mort, pas la peine d'entrer
                db.commit()
                continue

            retracement_from_peak = (signal.peak_price_usd - price) / signal.peak_price_usd
            if retracement_from_peak >= config.PAPER_RETRACEMENT_PCT:
                rug = await is_likely_rug(signal.token_address)
                if rug:
                    signal.status = "rejected"
                    db.commit()
                    continue
                await _open_position(db, signal, price, retracement_from_peak, liquidity)

        # nettoie les signaux expirés depuis longtemps pour ne pas gonfler la table
        db.query(PendingSignal).filter(
            PendingSignal.status.in_(["expired", "rejected", "entered"]),
            PendingSignal.created_at < now - timedelta(hours=6),
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


async def _close_fraction(db, position: PaperPosition, fraction: float, price: float, fill_type: str):
    sold_size_usd = position.initial_size_usd * fraction
    pnl_usd = sold_size_usd * ((price - position.entry_price_usd) / position.entry_price_usd)

    fill = PaperFill(
        position_id=position.id,
        fill_type=fill_type,
        price_usd=price,
        fraction_sold=fraction,
        pnl_usd=pnl_usd,
    )
    db.add(fill)

    position.remaining_fraction = round(position.remaining_fraction - fraction, 4)
    position.realized_pnl_usd += pnl_usd

    pct_change = (price - position.entry_price_usd) / position.entry_price_usd * 100
    label = {
        "tp1": "🟢 TP1 (+30%)", "tp2": "🟢 TP2 (+75%)", "tp3": "🟢 TP3 (+150%)",
        "initial_stop": "🔴 Stop-loss initial", "trailing_stop": "🟡 Trailing stop",
    }.get(fill_type, fill_type)

    closed_fully = position.remaining_fraction <= 0.001
    if closed_fully:
        position.status = "closed"
        position.closed_at = datetime.now(timezone.utc)

    db.commit()

    await send_message(
        f"{label}\n"
        f"Token: <code>{position.token_address}</code>\n"
        f"Prix: ${price:.8f} ({pct_change:+.1f}%)\n"
        f"Vendu: {fraction:.0%} de la position\n"
        f"PnL sur ce fill: ${pnl_usd:+.2f}\n"
        f"PnL total position: ${position.realized_pnl_usd:+.2f}"
        + ("\n\n✅ Position clôturée." if closed_fully else "")
    )


async def manage_open_positions():
    """Vérifie chaque position ouverte: paliers de TP, stop initial, trailing stop."""
    db = SessionLocal()
    try:
        positions = db.query(PaperPosition).filter(PaperPosition.status == "open").all()

        for position in positions:
            price, _ = await _get_price_and_liquidity(position.token_address)
            if price is None:
                continue

            if price > position.peak_price_usd:
                position.peak_price_usd = price
                db.commit()

            pct_change = (price - position.entry_price_usd) / position.entry_price_usd

            # arme le trailing stop une fois le seuil de profit atteint
            if not position.stop_armed and pct_change >= config.PAPER_TRAILING_ARM_PCT:
                position.stop_armed = True
                db.commit()

            # paliers de take-profit (du plus haut au plus bas pour ne pas les rater si le prix saute)
            if not position.tp3_hit and pct_change >= config.PAPER_TP3_PCT:
                position.tp3_hit = True
                db.commit()
                await _close_fraction(db, position, config.PAPER_TP3_FRACTION, price, "tp3")
                continue
            if not position.tp2_hit and pct_change >= config.PAPER_TP2_PCT:
                position.tp2_hit = True
                db.commit()
                await _close_fraction(db, position, config.PAPER_TP2_FRACTION, price, "tp2")
                continue
            if not position.tp1_hit and pct_change >= config.PAPER_TP1_PCT:
                position.tp1_hit = True
                db.commit()
                await _close_fraction(db, position, config.PAPER_TP1_FRACTION, price, "tp1")
                continue

            # stop-loss: initial (fixe depuis l'entrée) ou trailing (depuis le pic) selon l'état
            if position.stop_armed:
                drop_from_peak = (position.peak_price_usd - price) / position.peak_price_usd
                if drop_from_peak >= config.PAPER_TRAILING_STOP_PCT:
                    await _close_fraction(db, position, position.remaining_fraction, price, "trailing_stop")
                    continue
            else:
                if pct_change <= -config.PAPER_INITIAL_STOP_PCT:
                    await _close_fraction(db, position, position.remaining_fraction, price, "initial_stop")
                    continue
    finally:
        db.close()


async def get_portfolio_summary() -> dict:
    db = SessionLocal()
    try:
        open_positions = db.query(PaperPosition).filter(PaperPosition.status == "open").all()
        closed_positions = db.query(PaperPosition).filter(PaperPosition.status == "closed").all()

        realized_pnl = sum(p.realized_pnl_usd for p in closed_positions)
        realized_pnl += sum(p.realized_pnl_usd for p in open_positions)  # inclut les TP partiels déjà pris

        capital_deployed = sum(p.initial_size_usd * p.remaining_fraction for p in open_positions)

        return {
            "initial_capital_usd": config.PAPER_INITIAL_CAPITAL_USD,
            "realized_pnl_usd": round(realized_pnl, 2),
            "current_capital_usd": round(config.PAPER_INITIAL_CAPITAL_USD + realized_pnl, 2),
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "capital_deployed_usd": round(capital_deployed, 2),
        }
    finally:
        db.close()


async def get_trade_journal(limit: int = 15) -> list[dict]:
    """Détail par trade: contexte d'entrée + résultat, pour comprendre CE QUI marche ou pas."""
    db = SessionLocal()
    try:
        positions = (
            db.query(PaperPosition)
            .order_by(PaperPosition.entry_time.desc())
            .limit(limit)
            .all()
        )
        journal = []
        for p in positions:
            pnl_pct = (p.realized_pnl_usd / p.initial_size_usd * 100) if p.initial_size_usd else 0
            journal.append({
                "id": p.id,
                "token_address": p.token_address,
                "status": p.status,
                "entry_time": p.entry_time,
                "realized_pnl_usd": p.realized_pnl_usd,
                "pnl_pct": pnl_pct,
                "retracement_pct_at_entry": p.retracement_pct_at_entry,
                "entry_delay_seconds": p.entry_delay_seconds,
                "wallet_score_at_entry": p.wallet_score_at_entry,
                "liquidity_usd_at_entry": p.liquidity_usd_at_entry,
                "tp1_hit": p.tp1_hit,
                "tp2_hit": p.tp2_hit,
                "tp3_hit": p.tp3_hit,
            })
        return journal
    finally:
        db.close()


async def get_expectancy_stats(min_reliable_sample: int = 20) -> dict:
    """
    Espérance mathématique: (% gagnants x gain moyen) - (% perdants x perte moyenne).
    C'est la vraie mesure de rentabilité d'une stratégie, plus fiable qu'un simple
    win rate brut. `reliable=False` tant qu'on n'a pas assez de trades clôturés:
    en dessous du seuil, mieux vaut observer que d'ajuster les paramètres.
    """
    db = SessionLocal()
    try:
        closed = db.query(PaperPosition).filter(PaperPosition.status == "closed").all()
        n = len(closed)
        if n == 0:
            return {"sample_size": 0, "reliable": False}

        pnl_pcts = [
            (p.realized_pnl_usd / p.initial_size_usd) * 100
            for p in closed if p.initial_size_usd
        ]
        wins = [p for p in pnl_pcts if p > 0]
        losses = [p for p in pnl_pcts if p <= 0]

        win_rate = len(wins) / n if n else 0
        avg_win_pct = (sum(wins) / len(wins)) if wins else 0
        avg_loss_pct = (sum(losses) / len(losses)) if losses else 0
        expectancy_pct = (win_rate * avg_win_pct) + ((1 - win_rate) * avg_loss_pct)

        return {
            "sample_size": n,
            "win_rate": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win_pct, 1),
            "avg_loss_pct": round(avg_loss_pct, 1),
            "expectancy_pct": round(expectancy_pct, 2),
            "reliable": n >= min_reliable_sample,
        }
    finally:
        db.close()
