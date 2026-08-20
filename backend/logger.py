import sqlite3
import time
import os

class RequestLogger:
    def __init__(self, db_path: str = "requests.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    user_id TEXT,
                    tier TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    max_tokens INTEGER,
                    actual_cost REAL,
                    risk_score INTEGER,
                    policy_action TEXT,
                    status TEXT
                )
            """)
            conn.commit()

    def log_request(self, user_id: str, tier: str, input_tokens: int, output_tokens: int, 
                    max_tokens: int, actual_cost: float, risk_score: int, 
                    policy_action: str, status: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO request_log (
                    timestamp, user_id, tier, input_tokens, output_tokens, 
                    max_tokens, actual_cost, risk_score, policy_action, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(), user_id, tier, input_tokens, output_tokens,
                max_tokens, actual_cost, risk_score, policy_action, status
            ))
            conn.commit()
