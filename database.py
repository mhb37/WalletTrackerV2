from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import config

# Railway donne une DATABASE_URL qui commence par postgres:// -> SQLAlchemy veut postgresql://
db_url = config.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}

engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import models  # noqa: import déclenche l'enregistrement des tables
    Base.metadata.create_all(bind=engine)
    _run_light_migrations()


def _run_light_migrations():
    """
    create_all() ne modifie jamais les tables existantes. Pour ajouter une colonne
    à un modèle sans tout recréer, on la rajoute ici à la main si besoin.
    """
    from sqlalchemy import text
    statements = [
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS last_seen_signature VARCHAR",
    ]
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()
