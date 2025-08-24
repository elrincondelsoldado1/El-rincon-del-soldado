# db.py
import os
import psycopg2
from psycopg2 import extras


def get_db_connection():
    """
    - En Render usará DATABASE_URL (con SSL).
    - En tu Windows usará las credenciales locales de Postgres.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(
            url,
            cursor_factory=extras.RealDictCursor,
            sslmode="require"
        )

    # Fallback local (lo que ya usabas)
    return psycopg2.connect(
        host="localhost",
        database="cafeteria_db",
        user="postgres",
        password="musa",
        cursor_factory=extras.RealDictCursor
    )

# compatibilidad con imports existentes
get_connection = get_db_connection
