"""
Calcule les stats de performance d'un wallet à partir de son historique on-chain,
applique des filtres durs, puis calcule un score 0-100.
"""
from datetime import datetime, timezone
from collections import defaultdict
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Wallet
from services.helius_client import helius_client
from config import config


LAMPORTS_PER_SOL = 1_000_000_000


def _extract_sol_flows_by_token(txs: list[dict], wallet_address: str) -> dict:
    """
    Regroupe les swaps par token et calcule le SOL dépensé (buy) vs reçu (sell)
    à partir des transactions parsées Helius.
    """
    flows = defaultdict(lambda: {"sol_in": 0.0, "sol_out": 0.0, "last_ts": 0})

    for tx in txs:
        if tx.get("type") != "SWAP":
            continue
        ts = tx.get("timestamp", 0)
        native_transfers = tx.get("nativeTransfers", [])
        token_transfers = tx.get("tokenTransfers", [])

        sol_delta = 0.0
        for nt in native_transfers:
            if nt.get("fromUserAccount") == wallet_address:
                sol_delta -= nt.get("amount", 0) / LAMPORTS_PER_SOL
            if nt.get("toUserAccount") == wallet_address:
                sol_delta += nt.get("amount", 0) / LAMPORTS_PER_SOL

        for tt in token_transfers:
            mint = tt.get("mint")
            if not mint:
                continue
            if tt.get("toUserAccount") == wallet_address and sol_delta < 0:
                flows[mint]["sol_in"] += abs(sol_delta)
                flows[mint]["last_ts"] = max(flows[mint]["last_ts"], ts)
            elif tt.get("fromUserAccount") == wallet_address and sol_delta > 0:
                flows[mint]["sol_out"] += sol_delta
                flows[mint]["last_ts"] = max(flows[mint]["last_ts"], ts)

    return flows


def _compute_stats_from_flows(flows: dict) -> dict:
    closed_positions = [
        f for f in flows.values() if f["sol_in"] > 0 and f["sol_out"] > 0
    ]
    if not closed_positions:
        return None

    wins = [p for p in closed_positions if p["sol_out"] > p["sol_in"]]
    losses = [p for p in closed_positions if p["sol_out"] <= p["sol_in"]]

    pnls = [p["sol_out"] - p["sol_in"] for p in closed_positions]
    total_pnl = sum(pnls)
    top_trade_pnl = max(pnls) if pnls else 0
    roi_multiples = [p["sol_out"] / p["sol_in"] for p in closed_positions if p["sol_in"] > 0]

    return {
        "total_trades": len(closed_positions),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(closed_positions),
        "avg_roi_multiple": sum(roi_multiples) / len(roi_multiples) if roi_multiples else 0,
        "total_realized_pnl_sol": total_pnl,
        "top_trade_pnl_sol": top_trade_pnl,
        "last_active_ts": max(f["last_ts"] for f in flows.values()) if flows else 0,
    }


def _passes_hard_filters(wallet: Wallet, stats: dict, wallet_age_days: float) -> bool:
    if stats["total_trades"] < config.MIN_TOTAL_TRADES:
        return False
    if wallet_age_days < config.MIN_WALLET_AGE_DAYS:
        return False
    if stats["win_rate"] < config.MIN_WIN_RATE:
        return False
    if stats["total_realized_pnl_sol"] <= 0:
        return False
    if stats["top_trade_pnl_sol"] > 0 and stats["total_realized_pnl_sol"] > 0:
        dominance = stats["top_trade_pnl_sol"] / stats["total_realized_pnl_sol"]
        if dominance > config.MAX_SINGLE_TRADE_PROFIT_DOMINANCE:
            return False
    last_active = datetime.fromtimestamp(stats["last_active_ts"], tz=timezone.utc) if stats["last_active_ts"] else None
    if last_active:
        inactive_days = (datetime.now(timezone.utc) - last_active).days
        if inactive_days > config.MAX_INACTIVE_DAYS:
            return False
    return True


def _compute_score(stats: dict, avg_entry_percentile: float) -> float:
    win_rate_score = min(stats["win_rate"] / 0.8, 1.0) * 100
    roi_score = min(stats["avg_roi_multiple"] / 5.0, 1.0) * 100
    consistency_score = min(stats["total_trades"] / 30.0, 1.0) * 100
    rug_avoidance_score = 100  # placeholder: affiné en Phase 2 avec détection de rugs
    timing_score = max(0.0, (1 - avg_entry_percentile)) * 100

    score = (
        win_rate_score * config.WEIGHT_WIN_RATE
        + roi_score * config.WEIGHT_AVG_ROI
        + consistency_score * config.WEIGHT_CONSISTENCY
        + rug_avoidance_score * config.WEIGHT_RUG_AVOIDANCE
        + timing_score * config.WEIGHT_TIMING
    )
    return round(score, 2)


async def score_wallet(wallet_address: str, db: Session) -> dict | None:
    wallet = db.query(Wallet).filter(Wallet.address == wallet_address).first()
    if not wallet:
        return None

    txs = await helius_client.get_wallet_transactions(wallet_address, limit=100)
    if not txs:
        return None

    first_activity = await helius_client.get_wallet_first_activity(wallet_address)
    wallet_age_days = (
        (datetime.now(timezone.utc) - first_activity).days if first_activity else 0
    )

    flows = _extract_sol_flows_by_token(txs, wallet_address)
    stats = _compute_stats_from_flows(flows)
    if not stats:
        wallet.passed_hard_filters = False
        wallet.score = 0
        db.commit()
        return {"address": wallet_address, "passed": False, "reason": "no_closed_positions"}

    avg_entry_percentile = 0.5
    entries = [t.entry_percentile for t in wallet.transactions if t.entry_percentile is not None]
    if entries:
        avg_entry_percentile = sum(entries) / len(entries)

    passed = _passes_hard_filters(wallet, stats, wallet_age_days)

    wallet.total_trades = stats["total_trades"]
    wallet.win_count = stats["win_count"]
    wallet.loss_count = stats["loss_count"]
    wallet.win_rate = stats["win_rate"]
    wallet.avg_roi_multiple = stats["avg_roi_multiple"]
    wallet.total_realized_pnl_sol = stats["total_realized_pnl_sol"]
    wallet.top_trade_pnl_sol = stats["top_trade_pnl_sol"]
    wallet.avg_entry_percentile = avg_entry_percentile
    wallet.passed_hard_filters = passed
    wallet.last_active_at = (
        datetime.fromtimestamp(stats["last_active_ts"], tz=timezone.utc)
        if stats["last_active_ts"] else None
    )

    if passed:
        wallet.score = _compute_score(stats, avg_entry_percentile)
        wallet.is_watchlisted = wallet.score >= config.SCORE_THRESHOLD_WATCHLIST
    else:
        wallet.score = 0
        wallet.is_watchlisted = False

    db.commit()
    return {"address": wallet_address, "passed": passed, "score": wallet.score}


async def run_scoring_cycle():
    db: Session = SessionLocal()
    try:
        wallets = db.query(Wallet).all()
        results = []
        for w in wallets:
            result = await score_wallet(w.address, db)
            if result:
                results.append(result)
        return results
    finally:
        db.close()
