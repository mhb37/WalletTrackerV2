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

    # --- Paper trading (Phase 3) ---
    PAPER_INITIAL_CAPITAL_USD = float(os.getenv("PAPER_INITIAL_CAPITAL_USD", "100"))
    PAPER_POSITION_SIZE_PCT = float(os.getenv("PAPER_POSITION_SIZE_PCT", "0.10"))

    # Entrée: on n'entre pas tout de suite au signal, on attend un retracement
    PAPER_ENTRY_WINDOW_MINUTES = int(os.getenv("PAPER_ENTRY_WINDOW_MINUTES", "10"))
    PAPER_RETRACEMENT_PCT = float(os.getenv("PAPER_RETRACEMENT_PCT", "0.08"))  # -8% depuis le pic local
    PAPER_MAX_DRAWDOWN_FROM_SIGNAL_PCT = float(os.getenv("PAPER_MAX_DRAWDOWN_FROM_SIGNAL_PCT", "0.30"))
    PAPER_MIN_LIQUIDITY_USD = float(os.getenv("PAPER_MIN_LIQUIDITY_USD", "3000"))

    # Sortie: paliers de take-profit (fraction de la position vendue à chaque palier)
    PAPER_TP1_PCT = float(os.getenv("PAPER_TP1_PCT", "0.30"))   # +30%
    PAPER_TP1_FRACTION = float(os.getenv("PAPER_TP1_FRACTION", "0.25"))
    PAPER_TP2_PCT = float(os.getenv("PAPER_TP2_PCT", "0.75"))   # +75%
    PAPER_TP2_FRACTION = float(os.getenv("PAPER_TP2_FRACTION", "0.25"))
    PAPER_TP3_PCT = float(os.getenv("PAPER_TP3_PCT", "1.50"))   # +150%
    PAPER_TP3_FRACTION = float(os.getenv("PAPER_TP3_FRACTION", "0.25"))

    # Stop-loss initial (avant d'être "armé") et trailing stop (une fois armé)
    PAPER_INITIAL_STOP_PCT = float(os.getenv("PAPER_INITIAL_STOP_PCT", "0.25"))     # -25% depuis l'entrée
    PAPER_TRAILING_ARM_PCT = float(os.getenv("PAPER_TRAILING_ARM_PCT", "0.20"))     # s'arme à +20%
    PAPER_TRAILING_STOP_PCT = float(os.getenv("PAPER_TRAILING_STOP_PCT", "0.25"))   # -25% depuis le pic

    # --- Watchlist ---
    SCORE_THRESHOLD_WATCHLIST = float(os.getenv("SCORE_THRESHOLD_WATCHLIST", "70"))

    # --- Scheduler ---
    DISCOVERY_INTERVAL_MINUTES = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "30"))
    RESCORE_INTERVAL_MINUTES = int(os.getenv("RESCORE_INTERVAL_MINUTES", "15"))
    MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "20"))


config = Config()
