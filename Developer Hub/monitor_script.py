import sqlite3
import time
import subprocess
import os

db_path = 'Backend/.data/agenthub.db'
session_id = '9b98e742-5d9f-4d2b-86a5-21b75d812dcd'
timeout = 12 * 60  # 12 minutes
start_time = time.time()

def get_session_data():
    if not os.path.exists(db_path):
        return None, []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT status, completed_at FROM sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()
        
        # Checking agents column in sessions if agents table doesn't exist or is different
        conn.close()
        return session, []
    except Exception as e:
        print(f"Error querying DB: {e}")
        return None, []

def check_playwright():
    try:
        output = subprocess.check_output(['pgrep', '-f', 'fabric-portal-architecture-mixed.spec.ts']).decode()
        return True, output.strip()
    except subprocess.CalledProcessError:
        return False, ""

print(f"Monitoring session {session_id}...")

while time.time() - start_time < timeout:
    session, _ = get_session_data()
    pw_running, pw_pids = check_playwright()
    
    status = session['status'] if session else "Unknown"
    completed_at = session['completed_at'] if session else "N/A"
    
    print(f"Time: {int(time.time() - start_time)}s | Status: {status} | Playwright: {'Running' if pw_running else 'Finished'}", end='\r')
    
    if status in ['completed', 'failed', 'cancelled'] and not pw_running:
        print("\nTarget state reached.")
        break
    
    time.sleep(5)

print("\nFinal Report:")
session, _ = get_session_data()
pw_running, _ = check_playwright()
if session:
    print(f"Session Status: {session['status']}")
    print(f"Completed At: {session['completed_at']}")
else:
    print("Session not found in DB.")
print(f"Playwright Process: {'Running' if pw_running else 'Finished'}")
