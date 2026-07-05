import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import init_db, get_db
from models import Wallet
from services.discovery import run_discovery_cycle
from services.scoring import run_scoring_cycle
from services.monitor import run_monitor_cycle
from services.paper_trading import process_pending_signals, manage_open_positions, get_portfolio_summary
from services.telegram_bot import poll_updates_once, notify_new_watchlist_wallet, send_message, set_bot_commands, main_keyboard, HELP_TEXT
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wallet-scorer")

_background_tasks: list[asyncio.Task] = []


async def discovery_loop():
    while True:
        try:
            result = await run_discovery_cycle()
            logger.info(f"Discovery cycle: {result}")
        except Exception as e:
            logger.exception(f"Erreur discovery: {e}")
        await asyncio.sleep(config.DISCOVERY_INTERVAL_MINUTES * 60)


async def scoring_loop():
    while True:
        try:
            await asyncio.sleep(60)  # laisse la discovery tourner un peu en premier
            from database import SessionLocal
            db = SessionLocal()
            already_watchlisted = {
                w.address for w in db.query(Wallet).filter(Wallet.is_watchlisted == True).all()  # noqa: E712
            }
            db.close()

            results = await run_scoring_cycle()
            logger.info(f"Scoring cycle: {len(results)} wallets traités")

            db = SessionLocal()
            newly_watchlisted = db.query(Wallet).filter(
                Wallet.is_watchlisted == True,  # noqa: E712
                ~Wallet.address.in_(already_watchlisted) if already_watchlisted else True,
            ).all()
            for w in newly_watchlisted:
                await notify_new_watchlist_wallet(w)
            db.close()
        except Exception as e:
            logger.exception(f"Erreur scoring: {e}")
        await asyncio.sleep(config.RESCORE_INTERVAL_MINUTES * 60)


async def telegram_polling_loop():
    offset = None
    while True:
        try:
            offset = await poll_updates_once(offset)
        except Exception as e:
            logger.exception(f"Erreur telegram polling: {e}")
        await asyncio.sleep(3)


async def monitor_loop():
    while True:
        try:
            result = await run_monitor_cycle()
            if result["alerts_sent"] > 0:
                logger.info(f"Monitor cycle: {result}")
        except Exception as e:
            logger.exception(f"Erreur monitor: {e}")
        await asyncio.sleep(config.MONITOR_INTERVAL_SECONDS)


async def paper_trading_loop():
    while True:
        try:
            await process_pending_signals()
            await manage_open_positions()
        except Exception as e:
            logger.exception(f"Erreur paper trading: {e}")
        await asyncio.sleep(config.MONITOR_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("DB initialisée")
    _background_tasks.append(asyncio.create_task(discovery_loop()))
    _background_tasks.append(asyncio.create_task(scoring_loop()))
    _background_tasks.append(asyncio.create_task(telegram_polling_loop()))
    _background_tasks.append(asyncio.create_task(monitor_loop()))
    _background_tasks.append(asyncio.create_task(paper_trading_loop()))
    await set_bot_commands()
    await send_message("🚀 Wallet Scorer démarré.\n\n" + HELP_TEXT, reply_markup=main_keyboard())
    yield
    for t in _background_tasks:
        t.cancel()


app = FastAPI(title="Solana Wallet Scorer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "running"}


@app.get("/wallets/top")
def top_wallets(limit: int = 20, db: Session = Depends(get_db)):
    wallets = (
        db.query(Wallet)
        .filter(Wallet.passed_hard_filters == True)  # noqa: E712
        .order_by(desc(Wallet.score))
        .limit(limit)
        .all()
    )
    return [
        {
            "address": w.address,
            "score": w.score,
            "win_rate": w.win_rate,
            "avg_roi_multiple": w.avg_roi_multiple,
            "total_trades": w.total_trades,
            "is_watchlisted": w.is_watchlisted,
        }
        for w in wallets
    ]


@app.get("/wallets/watchlist")
def watchlist(db: Session = Depends(get_db)):
    wallets = db.query(Wallet).filter(Wallet.is_watchlisted == True).all()  # noqa: E712
    return [{"address": w.address, "score": w.score} for w in wallets]


@app.post("/discovery/run")
async def trigger_discovery():
    result = await run_discovery_cycle()
    return result


@app.post("/scoring/run")
async def trigger_scoring():
    result = await run_scoring_cycle()
    return {"processed": len(result), "results": result}


@app.post("/monitor/run")
async def trigger_monitor():
    result = await run_monitor_cycle()
    return result


@app.get("/paper/portfolio")
async def paper_portfolio():
    return await get_portfolio_summary()
