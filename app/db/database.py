import psycopg
from app.core.config import DATABASE_URL
from app.core.logging import get_logger

log = get_logger("database")

def get_connection():
    log.info("🔌 Opening database connection")
    return psycopg.connect(DATABASE_URL)
