import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_pool = None

def normalize_database_url(database_url: str) -> str:
    if not database_url:
        raise ValueError("DATABASE_URL is required")

    # Neon and Heroku-style URLs can use postgres://, while psycopg2 expects
    # postgresql:// for URL parsing.
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

    parsed = urlsplit(database_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("DATABASE_URL must be a full Postgres connection string")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    hostname = (parsed.hostname or '').lower()

    # Neon connection strings are SSL-only. The Neon dashboard normally includes
    # sslmode=require; add it if someone pasted a shortened URL.
    if hostname.endswith('.neon.tech') and 'sslmode' not in query and 'sslrootcert' not in query:
        query['sslmode'] = 'require'

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

def init_db(database_url: str):
    global _pool
    database_url = normalize_database_url(database_url)

    min_connections = 1
    max_connections = 5 if '-pooler.' in database_url else 10
    _pool = pool.ThreadedConnectionPool(min_connections, max_connections, database_url)
    
    # Initialize schema
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(80) UNIQUE NOT NULL,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS portfolios (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS holdings (
                    id SERIAL PRIMARY KEY,
                    portfolio_id INTEGER REFERENCES portfolios(id) ON DELETE CASCADE,
                    ticker VARCHAR(50) NOT NULL,
                    shares FLOAT NOT NULL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id SERIAL PRIMARY KEY,
                    portfolio_id INTEGER REFERENCES portfolios(id) ON DELETE CASCADE,
                    message TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS holding_snapshots (
                    id SERIAL PRIMARY KEY,
                    holding_id INTEGER REFERENCES holdings(id) ON DELETE CASCADE,
                    portfolio_id INTEGER REFERENCES portfolios(id) ON DELETE CASCADE,
                    ticker VARCHAR(50) NOT NULL,
                    event VARCHAR(20) NOT NULL,
                    shares_delta FLOAT NOT NULL,
                    shares_total FLOAT NOT NULL,
                    price_at_event FLOAT,
                    value_before FLOAT,
                    value_after FLOAT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Add user_id column to portfolios if it doesn't exist (migration)
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'portfolios' AND column_name = 'user_id'
                    ) THEN
                        ALTER TABLE portfolios ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
                    END IF;
                END $$;
            """)
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'holdings' AND column_name = 'last_price'
                    ) THEN
                        ALTER TABLE holdings
                            ADD COLUMN last_price FLOAT,
                            ADD COLUMN last_price_updated_at TIMESTAMP WITH TIME ZONE;
                    END IF;
                END $$;
            """)
        conn.commit()
    finally:
        put_db_connection(conn)

def get_db_connection():
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db first.")
    return _pool.getconn()

def put_db_connection(conn):
    if _pool is not None and conn is not None:
        _pool.putconn(conn)

def get_db():
    """FastAPI dependency yielding a pooled DB connection."""
    conn = get_db_connection()
    try:
        yield conn
    finally:
        put_db_connection(conn)
