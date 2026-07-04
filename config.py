import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Database ---
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wallet_scorer.db")

    # --- Helius ---
    HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
    HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    HELIUS_TX_URL = "https://api.helius.xyz/v0/addresses/{address}/transactions"

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- DexScreener (pas de clé requise) ---
    DEXSCREENER_TOKEN_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
    DEXSCREENER_TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
    DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
    DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"

    # --- Discovery ---
    MIN_PUMP_MULTIPLE = float(os.getenv("MIN_PUMP_MULTIPLE", "3.0"))  # token doit avoir x3+ depuis le bas
    EARLY_BUYER_WINDOW_MINUTES = int(os.getenv("EARLY_BUYER_WINDOW_MINUTES", "15"))
    MAX_EARLY_BUYERS_PER_TOKEN = int(os.getenv("MAX_EARLY_BUYERS_PER_TOKEN", "30"))
    # Au-delà de cet âge, remonter jusqu'aux tout premiers acheteurs via pagination
    # arrière devient irréaliste (trop de transactions à parcourir). On skip.
    MAX_TOKEN_AGE_HOURS_FOR_DISCOVERY = float(os.getenv("MAX_TOKEN_AGE_HOURS_FOR_DISCOVERY", "6"))

    # --- Filtres durs (avant scoring) ---
    MIN_TOTAL_TRADES = int(os.getenv("MIN_TOTAL_TRADES", "8"))
    MIN_WALLET_AGE_DAYS = int(os.getenv("MIN_WALLET_AGE_DAYS", "14"))
    MIN_WIN_RATE = float(os.getenv("MIN_WIN_RATE", "0.35"))
    MAX_SINGLE_TRADE_PROFIT_DOMINANCE = float(os.getenv("MAX_SINGLE_TRADE_PROFIT_DOMINANCE", "0.6"))
    MAX_INACTIVE_DAYS = int(os.getenv("MAX_INACTIVE_DAYS", "10"))
    # Nombre de pages Helius (x100 tx) à parcourir en arrière pour évaluer l'historique
    # complet d'un wallet lors du scoring (âge réel + PnL). Plus haut = plus précis
    # mais plus d'appels API par wallet.
    MAX_HISTORY_PAGES_FOR_SCORING = int(os.getenv("MAX_HISTORY_PAGES_FOR_SCORING", "5"))

    # --- Scoring: poids (doivent sommer à 1.0) ---
    WEIGHT_WIN_RATE = 0.30
    WEIGHT_AVG_ROI = 0.25
    WEIGHT_CONSISTENCY = 0.15
    WEIGHT_RUG_AVOIDANCE = 0.15
    WEIGHT_TIMING = 0.15

    # --- Détection de rugs (Phase 2) ---
    RUG_LIQUIDITY_THRESHOLD_USD = float(os.getenv("RUG_LIQUIDITY_THRESHOLD_USD", "500"))
    MAX_TOKENS_TO_CHECK_FOR_RUGS = int(os.getenv("MAX_TOKENS_TO_CHECK_FOR_RUGS", "15"))

    # --- Watchlist ---
    SCORE_THRESHOLD_WATCHLIST = float(os.getenv("SCORE_THRESHOLD_WATCHLIST", "70"))

    # --- Scheduler ---
    DISCOVERY_INTERVAL_MINUTES = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "30"))
    RESCORE_INTERVAL_MINUTES = int(os.getenv("RESCORE_INTERVAL_MINUTES", "15"))
    MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "20"))


config = Config()
