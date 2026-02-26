import sqlite3
from pathlib import Path

DB_PATH = Path("history.db")

def fix_tasks(task_ids, status="completed"):
    if not DB_PATH.exists():
        print("Database not found")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    for task_id in task_ids:
        print(f"Updating task {task_id} to {status}...")
        c.execute("UPDATE history SET status = ? WHERE id = ?", (status, task_id))
        if c.rowcount > 0:
            print(f"Task {task_id} updated.")
        else:
            print(f"Task {task_id} not found.")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    fix_tasks(["f84db28a", "f4e53ff8"], "completed")
