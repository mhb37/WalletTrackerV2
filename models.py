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
