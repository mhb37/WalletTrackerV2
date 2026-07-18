"""
Phase 1 du pipeline: repère des tokens qui ont pump, identifie leurs early buyers,
et les enregistre comme wallets candidats à scorer.
"""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Wallet, Token, WalletTransaction
from services.dexscreener_client import dexscreener_client
from services import data_provider
from config import config

logger = logging.getLogger("wallet-scorer")

_discovery_in_progress = False


async def run_discovery_cycle():
    global _discovery_in_progress
    if _discovery_in_progress:
        # Un cycle tourne déjà (auto ou manuel) -> on ne lance pas de doublon,
        # sinon deux cycles peuvent tenter d'insérer les mêmes transactions en
        # même temps et se faire rejeter par la contrainte unique en base.
        return {"skipped": True, "reason": "deja_en_cours"}

    _discovery_in_progress = True
    db: Session = SessionLocal()
    try:
        pumped_tokens, source_counts = await dexscreener_client.find_pumped_solana_tokens(
            config.MIN_PUMP_MULTIPLE
        )
        new_wallets_found = 0
        diagnostics = []

        for pt in pumped_tokens:
            token_address = pt["token_address"]

            existing = db.query(Token).filter(Token.address == token_address).first()
            if existing and existing.used_for_discovery:
                diagnostics.append(f"{token_address[:8]}: déjà traité")
                continue  # déjà traité

            mint_time = pt.get("created_at")
            if mint_time is None:
                diagnostics.append(f"{token_address[:8]}: pas de created_at")
                if not existing:
                    token = Token(
                        address=token_address,
                        symbol=pt.get("symbol"),
                        current_price_usd=pt.get("current_price_usd"),
                        pump_multiple=pt.get("pump_multiple"),
                        used_for_discovery=True,
                    )
                    db.add(token)
                    db.commit()
                continue

            if not existing:
                token = Token(
                    address=token_address,
                    symbol=pt.get("symbol"),
                    created_at=mint_time,
                    current_price_usd=pt.get("current_price_usd"),
                    pump_multiple=pt.get("pump_multiple"),
                )
                db.add(token)
            else:
                token = existing
                token.pump_multiple = pt.get("pump_multiple")

            age_hours = (datetime.now(timezone.utc) - mint_time).total_seconds() / 3600
            if age_hours > config.MAX_TOKEN_AGE_HOURS_FOR_DISCOVERY:
                diagnostics.append(f"{token_address[:8]}: trop vieux ({age_hours:.1f}h)")
                token.used_for_discovery = True
                db.commit()
                continue

            early_buyers, stop_reason = await data_provider.get_token_early_buyers(
                token_address=token_address,
                mint_timestamp=mint_time,
                window_minutes=config.EARLY_BUYER_WINDOW_MINUTES,
                max_buyers=config.MAX_EARLY_BUYERS_PER_TOKEN,
            )
            diagnostics.append(
                f"{token_address[:8]}: age={age_hours:.1f}h, {len(early_buyers)} buyers, {stop_reason}"
            )

            total = len(early_buyers) or 1
            added_this_token = 0
            for idx, buyer in enumerate(early_buyers):
                wallet_address = buyer["wallet"]

                exists_tx = db.query(WalletTransaction).filter(
                    WalletTransaction.tx_signature == buyer["tx_signature"]
                ).first()
                if exists_tx:
                    continue

                wallet = db.query(Wallet).filter(Wallet.address == wallet_address).first()
                if not wallet:
                    wallet = Wallet(address=wallet_address, first_seen=datetime.now(timezone.utc))
                    db.add(wallet)
                    new_wallets_found += 1

                tx = WalletTransaction(
                    wallet_address=wallet_address,
                    token_address=token_address,
                    action="buy",
                    token_amount=buyer.get("token_amount", 0),
                    entry_percentile=idx / total,
                    tx_signature=buyer["tx_signature"],
                    timestamp=buyer["timestamp"],
                )
                db.add(tx)
                added_this_token += 1

            token.used_for_discovery = True

            try:
                db.commit()
            except IntegrityError as e:
                # Une transaction déjà présente en base (ex: cycle concurrent) ->
                # on annule juste ce lot et on continue, plutôt que de tout
                # faire planter. Le token sera reconsidéré au prochain cycle.
                db.rollback()
                logger.warning(f"[discovery] {token_address[:8]}: doublon détecté à l'insertion, lot ignoré ({e})")
                new_wallets_found -= added_this_token if added_this_token > 0 else 0

            await asyncio.sleep(0.5)  # ménage le rate limit Helius entre chaque token

        return {
            "tokens_scanned": len(pumped_tokens),
            "new_wallets_found": new_wallets_found,
            "diagnostics": diagnostics,
            "source_counts": source_counts,
        }

    finally:
        db.close()
        _discovery_in_progress = False
