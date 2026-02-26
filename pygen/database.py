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
            end_at TIMESTAMP,
            owner TEXT DEFAULT 'admin',
            record_count INTEGER,
            config TEXT,     -- JSON string of request/config
            result TEXT,     -- JSON string of results
            logs TEXT        -- JSON string of logs (optional, maybe too large?)
        )
    ''')
    conn.commit()
    conn.close()
    migrate_db()

def migrate_db():
    """检查并添加缺少的字段（针对旧数据库）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA table_info(history)")
        columns = [row[1] for row in c.fetchall()]
        
        if "end_at" not in columns:
            print("正在迁移数据库: 添加 end_at 字段...")
            c.execute("ALTER TABLE history ADD COLUMN end_at TIMESTAMP")
            
        if "owner" not in columns:
            print("正在迁移数据库: 添加 owner 字段...")
            c.execute("ALTER TABLE history ADD COLUMN owner TEXT DEFAULT 'admin'")
            
        if "record_count" not in columns:
            print("正在迁移数据库: 添加 record_count 字段...")
            c.execute("ALTER TABLE history ADD COLUMN record_count INTEGER")
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"数据库迁移失败: {e}")

def add_history(task_id, task_type, config_data, owner="admin"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO history (id, task_type, status, created_at, owner, config, result, logs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_id, 
        task_type, 
        'pending', 
        datetime.now(), 
        owner,
        json.dumps(config_data, ensure_ascii=False), 
        '{}', 
        '[]'
    ))
    conn.commit()
    conn.close()

def update_history_status(task_id, status, result=None, logs=None, end_at=None, record_count=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        updates = []
        params = []
        
        updates.append("status = ?")
        params.append(status)
        
        if result is not None:
            updates.append("result = ?")
            try:
                params.append(json.dumps(result, ensure_ascii=False))
            except TypeError as e:
                print(f"[DB ERROR] Result serialization failed for task {task_id}: {e}")
                # Fallback: save basic info
                params.append(json.dumps({"error": f"Serialization failed: {str(e)}"}, ensure_ascii=False))
            except Exception as e:
                print(f"[DB ERROR] Result processing failed: {e}")
                params.append("{}")
            
        if logs is not None:
            updates.append("logs = ?")
            try:
                params.append(json.dumps(logs, ensure_ascii=False))
            except Exception as e:
                print(f"[DB ERROR] Logs serialization failed: {e}")
                params.append("[]")
        
        if end_at is not None:
            updates.append("end_at = ?")
            params.append(end_at)
            
        if record_count is not None:
            updates.append("record_count = ?")
            params.append(record_count)
            
        params.append(task_id)
        
        sql = f"UPDATE history SET {', '.join(updates)} WHERE id = ?"
        c.execute(sql, params)
        conn.commit()
        conn.close()
    except Exception as e:
        import traceback
        print(f"[DB CRITICAL] Failed to update history for task {task_id}: {e}")
        traceback.print_exc()

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
            "endAt": row["end_at"] if "end_at" in row.keys() else None,
            "owner": row["owner"] if "owner" in row.keys() else "admin",
            "recordCount": row["record_count"] if "record_count" in row.keys() else None,
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
            "endAt": row["end_at"] if "end_at" in row.keys() else None,
            "owner": row["owner"] if "owner" in row.keys() else "admin",
            "recordCount": row["record_count"] if "record_count" in row.keys() else None,
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

def reset_running_tasks():
    """重启时将所有 'running' 状态的任务重置为 'failed'"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 查找所有 running 状态的任务
        c.execute("SELECT id, result FROM history WHERE status = 'running'")
        rows = c.fetchall()
        
        if not rows:
            conn.close()
            return

        print(f"发现 {len(rows)} 个异常中断的任务，正在重置状态...")
        
        for row in rows:
            task_id = row['id']
            result_json = row['result']
            try:
                result = json.loads(result_json) if result_json else {}
            except:
                result = {}
            
            result['error'] = "服务异常重启，任务被中断"
            
            c.execute(
                "UPDATE history SET status = 'failed', end_at = ?, result = ? WHERE id = ?", 
                (datetime.now(), json.dumps(result, ensure_ascii=False), task_id)
            )
            
        conn.commit()
        conn.close()
        print(f"已重置 {len(rows)} 个任务为 failed")
            
    except Exception as e:
        print(f"重置任务状态失败: {e}")

