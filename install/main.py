import os
import sys
import requests
import argparse
import time
import sqlite3
from modules.security import generate_installer_key, verify_master_key, get_core_token

CORE_API_URL = "http://127.0.0.1:8000/system/internal/core"
ENV_PATH = ".env"
DB_PATH = "storage/databases/system.db"

def header():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("="*65)
    print("      NEBULA SYSTEMS - INSTALLER & MANAGEMENT v2026")
    print("="*65)

def check_system():
    if not os.path.exists(ENV_PATH):
        return 2
    if not os.path.exists(DB_PATH):
        return 2
    return 0

def verify_admin_exists():
    if not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_staff = 1")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False

def ask_confirm(msg="Confirm action?"):
    ans = input(f"{msg} (YES/NO): ").strip().upper()
    return ans == "YES"

def first_run_setup():
    header()
    print("[!] SYSTEM INITIALIZATION")
    print("-" * 65)
    
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    ans = input("Generate Installer Master Key? (YES/NO): ").strip().upper()
    if ans == "YES":
        new_key = generate_installer_key()
        print(f"\n[!!!] YOUR MASTER KEY: {new_key}\n")
    
    print("--- Create Master System Operator ---")
    user = input("Username: ")
    pwd = input("Password: ")
    
    print("\n[!] Connecting to Nebula Core...")
    token = get_core_token()
    headers = {"X-Nebula-Token": token}
    
    try:
        r = requests.post(
            f"{CORE_API_URL}/init-admin", 
            json={"username": user, "password": pwd}, 
            headers=headers,
            timeout=5
        )
        if r.status_code in [200, 201]:
            print("[OK] Admin created via Core.")
        elif r.status_code == 409:
            print("[!] Core: Already initialized.")
        else:
            print(f"[!] Core Error: {r.text}")
    except:
        print("[ERROR] Core is offline. Check if Nebula Core is running.")
    
    input("\nPress Enter to return to menu...")

def run_interactive():
    if os.path.exists(ENV_PATH):
        try:
            import dotenv
            dotenv.load_dotenv()
            master = os.getenv("INSTALLER_MASTER_KEY")
            if master:
                attempt = input("Enter Installer Master Key: ")
                if not verify_master_key(attempt):
                    print("ACCESS DENIED.")
                    time.sleep(2)
                    return
        except:
            pass

    while True:
        header()
        db_status = "FOUND" if os.path.exists(DB_PATH) else "MISSING"
        admin_status = "YES" if verify_admin_exists() else "NO"
        print(f" DATABASE: {db_status} | ADMIN CONFIGURED: {admin_status}")
        print("-" * 65)
        print(" [1] Run First-Time Setup / Create Admin")
        print(" [2] View System Status")
        print(" [7] HARD RESET (Delete Database)")
        print(" [0] Exit")
        print("-" * 65)
        
        choice = input("SELECT >> ")
        token = get_core_token()
        headers = {"X-Nebula-Token": token}

        if choice == "1":
            first_run_setup()
        elif choice == "2":
            try:
                r = requests.get(f"{CORE_API_URL}/status", headers=headers)
                print(f"Core Response: {r.json()}")
            except:
                print("Core Offline.")
        elif choice == "7":
            if input("Type 'ERASE' to confirm: ") == "ERASE":
                if os.path.exists(DB_PATH):
                    os.remove(DB_PATH)
                    print("[OK] Deleted.")
                else:
                    print("[!] Not found.")
        elif choice == "0":
            break
        input("\nPress Enter...")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()

    if args.check:
        sys.exit(check_system())
    else:
        run_interactive()