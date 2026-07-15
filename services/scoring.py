"""
Calcule les stats de performance d'un wallet à partir de son historique on-chain,
applique des filtres durs, puis calcule un score 0-100.
"""
from datetime import datetime, timezone
import asyncio
from collections import defaultdict
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Wallet
from services import data_provider
from services.rug_detector import compute_rug_stats
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


def _passes_hard_filters(wallet: Wallet, stats: dict, wallet_age_days: float) -> tuple[bool, list[str]]:
    reasons = []

    if stats["total_trades"] < config.MIN_TOTAL_TRADES:
        reasons.append(
            f"trades_insuffisants ({stats['total_trades']}<{config.MIN_TOTAL_TRADES})"
        )
    if wallet_age_days < config.MIN_WALLET_AGE_DAYS:
        reasons.append(
            f"wallet_trop_jeune ({wallet_age_days:.0f}j<{config.MIN_WALLET_AGE_DAYS}j)"
        )
    if stats["win_rate"] < config.MIN_WIN_RATE:
        reasons.append(
            f"win_rate_bas ({stats['win_rate']:.0%}<{config.MIN_WIN_RATE:.0%})"
        )
    if stats["total_realized_pnl_sol"] <= 0:
        reasons.append(f"pnl_negatif ({stats['total_realized_pnl_sol']:.2f} SOL)")
    if stats["top_trade_pnl_sol"] > 0 and stats["total_realized_pnl_sol"] > 0:
        dominance = stats["top_trade_pnl_sol"] / stats["total_realized_pnl_sol"]
        if dominance > config.MAX_SINGLE_TRADE_PROFIT_DOMINANCE:
            reasons.append(f"trade_dominant ({dominance:.0%} du profit total)")
    last_active = datetime.fromtimestamp(stats["last_active_ts"], tz=timezone.utc) if stats["last_active_ts"] else None
    if last_active:
        inactive_days = (datetime.now(timezone.utc) - last_active).days
        if inactive_days > config.MAX_INACTIVE_DAYS:
            reasons.append(f"inactif ({inactive_days}j sans trade)")

    return (len(reasons) == 0, reasons)


def _compute_score(stats: dict, avg_entry_percentile: float, rug_ratio: float | None) -> float:
    win_rate_score = min(stats["win_rate"] / 0.8, 1.0) * 100
    roi_score = min(stats["avg_roi_multiple"] / 5.0, 1.0) * 100
    consistency_score = min(stats["total_trades"] / 30.0, 1.0) * 100
    # Si on n'a pas pu vérifier (pas de données DexScreener), on ne pénalise pas.
    rug_avoidance_score = 100 if rug_ratio is None else max(0.0, 100 - rug_ratio * 100)
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

    txs = await data_provider.get_wallet_transaction_history(
        wallet_address, max_pages=config.MAX_HISTORY_PAGES_FOR_SCORING
    )
    if not txs:
        return None

    timestamps = [t.get("timestamp") for t in txs if t.get("timestamp") is not None]
    first_activity = (
        datetime.fromtimestamp(min(timestamps), tz=timezone.utc) if timestamps else None
    )
    wallet_age_days = (
        (datetime.now(timezone.utc) - first_activity).total_seconds() / 86400
        if first_activity else 0
    )

    flows = _extract_sol_flows_by_token(txs, wallet_address)
    stats = _compute_stats_from_flows(flows)
    if not stats:
        wallet.passed_hard_filters = False
        wallet.rejection_reason = "aucune_position_cloturee (pas encore de vente détectée)"
        wallet.score = 0
        db.commit()
        return {"address": wallet_address, "passed": False, "reasons": ["aucune_position_cloturee"]}

    avg_entry_percentile = 0.5
    entries = [t.entry_percentile for t in wallet.transactions if t.entry_percentile is not None]
    if entries:
        avg_entry_percentile = sum(entries) / len(entries)

    passed, reasons = _passes_hard_filters(wallet, stats, wallet_age_days)

    wallet.total_trades = stats["total_trades"]
    wallet.win_count = stats["win_count"]
    wallet.loss_count = stats["loss_count"]
    wallet.win_rate = stats["win_rate"]
    wallet.avg_roi_multiple = stats["avg_roi_multiple"]
    wallet.total_realized_pnl_sol = stats["total_realized_pnl_sol"]
    wallet.top_trade_pnl_sol = stats["top_trade_pnl_sol"]
    wallet.avg_entry_percentile = avg_entry_percentile
    wallet.passed_hard_filters = passed
    wallet.rejection_reason = "; ".join(reasons) if reasons else None
    wallet.last_active_at = (
        datetime.fromtimestamp(stats["last_active_ts"], tz=timezone.utc)
        if stats["last_active_ts"] else None
    )

    if passed:
        unique_tokens = list(flows.keys())
        rug_stats = await compute_rug_stats(unique_tokens)
        rug_ratio = (
            rug_stats["rug_count"] / rug_stats["checked_count"]
            if rug_stats["checked_count"] > 0 else None
        )
        wallet.rug_hits = rug_stats["rug_count"]
        wallet.score = _compute_score(stats, avg_entry_percentile, rug_ratio)
        wallet.is_watchlisted = wallet.score >= config.SCORE_THRESHOLD_WATCHLIST
    else:
        wallet.score = 0
        wallet.is_watchlisted = False

    db.commit()
    return {"address": wallet_address, "passed": passed, "score": wallet.score, "reasons": reasons}


async def run_scoring_cycle():
    db: Session = SessionLocal()
    try:
        wallets = db.query(Wallet).all()
        results = []
        for w in wallets:
            result = await score_wallet(w.address, db)
            if result:
                results.append(result)
            await asyncio.sleep(0.3)  # ménage le rate limit Helius entre chaque wallet
        return results
    finally:
        db.close()
