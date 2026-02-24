import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path("history.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id TEXT PRIMARY KEY,
            task_type TEXT,  -- 'single' or 'batch'
            status TEXT,     -- 'pending', 'running', 'completed', 'failed'
            created_at TIMESTAMP,
            config TEXT,     -- JSON string of request/config
            result TEXT,     -- JSON string of results
            logs TEXT        -- JSON string of logs (optional, maybe too large?)
        )
    ''')
    conn.commit()
    conn.close()

def add_history(task_id, task_type, config_data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO history (id, task_type, status, created_at, config, result, logs)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_id, 
        task_type, 
        'pending', 
        datetime.now(), 
        json.dumps(config_data, ensure_ascii=False), 
        '{}', 
        '[]'
    ))
    conn.commit()
    conn.close()

def update_history_status(task_id, status, result=None, logs=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updates = []
    params = []
    
    updates.append("status = ?")
    params.append(status)
    
    if result is not None:
        updates.append("result = ?")
        params.append(json.dumps(result, ensure_ascii=False))
        
    if logs is not None:
        updates.append("logs = ?")
        params.append(json.dumps(logs, ensure_ascii=False))
        
    params.append(task_id)
    
    sql = f"UPDATE history SET {', '.join(updates)} WHERE id = ?"
    c.execute(sql, params)
    conn.commit()
    conn.close()

def get_all_history():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM history ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    
    history = []
    for row in rows:
        history.append({
            "id": row["id"],
            "taskType": row["task_type"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "config": json.loads(row["config"]),
            "result": json.loads(row["result"]) if row["result"] else {},
            # logs usually not needed for list view
        })
    return history

def get_history_detail(task_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM history WHERE id = ?', (task_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            "id": row["id"],
            "taskType": row["task_type"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "config": json.loads(row["config"]),
            "result": json.loads(row["result"]) if row["result"] else {},
            "logs": json.loads(row["logs"]) if row["logs"] else []
        }
    return None

def delete_history(task_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM history WHERE id = ?', (task_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
