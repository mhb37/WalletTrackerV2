from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Wallet(Base):
    __tablename__ = "wallets"

    address = Column(String, primary_key=True)
    first_seen = Column(DateTime, default=utcnow)
    last_updated = Column(DateTime, default=utcnow, onupdate=utcnow)
    last_active_at = Column(DateTime, nullable=True)

    # Stats brutes calculées depuis wallet_transactions
    total_trades = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    avg_roi_multiple = Column(Float, default=0.0)
    total_realized_pnl_sol = Column(Float, default=0.0)
    top_trade_pnl_sol = Column(Float, default=0.0)
    rug_hits = Column(Integer, default=0)
    avg_entry_percentile = Column(Float, default=0.0)  # 0 = toujours très tôt, 1 = toujours tard

    # Résultat du scoring
    score = Column(Float, default=0.0)
    passed_hard_filters = Column(Boolean, default=False)
    rejection_reason = Column(String, nullable=True)
    is_watchlisted = Column(Boolean, default=False)
    last_seen_signature = Column(String, nullable=True)  # curseur pour le tracking temps réel
    last_scored_at = Column(DateTime, nullable=True)  # pour prioriser les wallets pas encore revus

    transactions = relationship("WalletTransaction", back_populates="wallet")


class Token(Base):
    __tablename__ = "tokens"

    address = Column(String, primary_key=True)
    symbol = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=True)
    first_price_usd = Column(Float, nullable=True)
    peak_price_usd = Column(Float, nullable=True)
    current_price_usd = Column(Float, nullable=True)
    pump_multiple = Column(Float, nullable=True)
    is_rug = Column(Boolean, default=False)
    used_for_discovery = Column(Boolean, default=False)
    discovered_at = Column(DateTime, default=utcnow)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, ForeignKey("wallets.address"))
    token_address = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "buy" ou "sell"
    sol_amount = Column(Float, default=0.0)
    token_amount = Column(Float, default=0.0)
    price_usd = Column(Float, nullable=True)
    entry_percentile = Column(Float, nullable=True)  # position dans les early buyers (0=1er, 1=dernier)
    tx_signature = Column(String, unique=True)
    timestamp = Column(DateTime, default=utcnow)

    wallet = relationship("Wallet", back_populates="transactions")


class WatchlistAlert(Base):
    __tablename__ = "watchlist_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, nullable=False)
    token_address = Column(String, nullable=False)
    action = Column(String, nullable=False)
    sent_at = Column(DateTime, default=utcnow)


class PendingSignal(Base):
    """Un achat détecté chez un wallet watchlisté, en attente d'un bon point d'entrée."""
    __tablename__ = "pending_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, nullable=False)
    token_address = Column(String, nullable=False)
    signal_price_usd = Column(Float, nullable=False)
    peak_price_usd = Column(Float, nullable=False)  # pic observé depuis le signal
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String, default="pending")  # pending | entered | expired | rejected


class PaperPosition(Base):
    """Une position simulée (paper trading) ouverte suite à un signal validé."""
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, nullable=False)  # wallet source du signal
    token_address = Column(String, nullable=False)
    symbol = Column(String, nullable=True)

    entry_price_usd = Column(Float, nullable=False)
    entry_time = Column(DateTime, default=utcnow)
    initial_size_usd = Column(Float, nullable=False)

    remaining_fraction = Column(Float, default=1.0)  # part de la position pas encore vendue
    peak_price_usd = Column(Float, nullable=False)  # pour le trailing stop
    tp1_hit = Column(Boolean, default=False)
    tp2_hit = Column(Boolean, default=False)
    tp3_hit = Column(Boolean, default=False)
    stop_armed = Column(Boolean, default=False)  # trailing stop actif ou pas encore

    status = Column(String, default="open")  # open | closed
    realized_pnl_usd = Column(Float, default=0.0)
    closed_at = Column(DateTime, nullable=True)

    # Contexte capturé à l'entrée, pour analyser CE QUI a marché ou pas
    signal_created_at = Column(DateTime, nullable=True)
    entry_delay_seconds = Column(Float, nullable=True)  # temps entre le signal et l'entrée réelle
    retracement_pct_at_entry = Column(Float, nullable=True)  # creux observé avant d'entrer
    wallet_score_at_entry = Column(Float, nullable=True)
    liquidity_usd_at_entry = Column(Float, nullable=True)


class PaperFill(Base):
    """Chaque vente partielle ou totale d'une position simulée, pour le reporting."""
    __tablename__ = "paper_fills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("paper_positions.id"))
    fill_type = Column(String, nullable=False)  # tp1 | tp2 | tp3 | initial_stop | trailing_stop
    price_usd = Column(Float, nullable=False)
    fraction_sold = Column(Float, nullable=False)
    pnl_usd = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=utcnow)
