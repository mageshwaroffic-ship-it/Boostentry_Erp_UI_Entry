#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — DB-driven automation loop that claims rows, fills ERP using Selenium,
builds validation, and updates DB statuses. Includes a safe update_overall_status()
implementation to avoid 'name not defined' errors and to allow marking overall_status
when ERP entry is completed.

Drop-in replacement for your existing main.py — preserves your flow and comments.
"""

import os
import json
import shutil
from time import sleep
from datetime import datetime
import traceback

import psycopg2
from psycopg2 import pool, extras

# your existing automation modules (ensure these imports work in your project)
from driver_utils import build_driver
from login_page import login
from branch_page import select_branch
from operations_page import open_operations
from consignment_page import open_consignment_page
from consignment_form import fill_consignment_form, build_validation_status

# ----------------------------
# CONFIGURATION (adjust as needed)
# ----------------------------
DB_CONFIG = {
    'dbname': 'mydb',
    'user': 'sql_developer',
    'password': 'Dev@123',
    'host': '103.14.123.44',
    'port': 5432,
}
TABLE_NAME = 'doc_processing_log'
JSON_COLUMN = 'extracted_json'  # where ValidationStatus will be written

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ----------------------------
# Database Connection Pool
# ----------------------------
connection_pool = None
try:
    connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, **DB_CONFIG)
    print("✅ Connected to Remote PostgreSQL")
except Exception as e:
    print("❌ Database Connection Error:", e)
    raise

def get_conn():
    """
    Acquire a dedicated connection from the pool.
    Remember to call release_conn(conn) when finished.
    """
    return connection_pool.getconn()

def release_conn(conn):
    """
    Return a connection to the pool.
    """
    return connection_pool.putconn(conn)


# ----------------------------
# Utility Helpers
# ----------------------------
def get_table_columns(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s;
        """, (TABLE_NAME,))
        return {r[0] for r in cur.fetchall()}

def parse_final_data(value):
    """
    Parse JSON safely and extract 'final_data' if present.
    Works for both extracted_json and corrected_json formats.
    """
    if not value:
        return {}
    try:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode('utf-8', errors='ignore')
        if isinstance(value, str):
            txt = value.strip()
            # handle quoted JSON strings
            if txt.startswith('"') and txt.endswith('"'):
                txt = txt[1:-1]
            # attempt to clean escaped quotes
            txt = txt.replace('\\"', '"').replace("''", "'")
            data = json.loads(txt)
        elif isinstance(value, dict):
            data = value
        else:
            return {}
        if isinstance(data, dict):
            if "final_data" in data and isinstance(data["final_data"], dict):
                return data["final_data"]
            return data
    except Exception as e:
        print(f"⚠️ JSON parse error in parse_final_data(): {e}")
        return {}
    return {}

def move_safely(src_path: str, dst_dir: str) -> str:
    os.makedirs(dst_dir, exist_ok=True)
    base = os.path.basename(src_path)
    name, ext = os.path.splitext(base)
    candidate = os.path.join(dst_dir, base)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dst_dir, f"{name}({i}){ext}")
        i += 1
    shutil.move(src_path, candidate)
    return candidate


# ----------------------------
# Database Claim / Update
# ----------------------------
def claim_one_row(conn, json_col, use_erp_updated_at):
    """
    Claim one row for processing (FOR UPDATE SKIP LOCKED).
    Returns a dict with row fields or None if no row available.
    """
    with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
        set_time_col = "erp_updated_at" if use_erp_updated_at else "updated_at"
        sql = f"""
        WITH cte AS (
            SELECT doc_id, erp_entry_status AS prev_erp_entry_status
            FROM {TABLE_NAME}
            WHERE UPPER(data_extraction_status) = 'COMPLETED'
              AND UPPER(erp_entry_status) IN ('NOT STARTED', 'FIXED')
            ORDER BY uploaded_on ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {TABLE_NAME}
        SET erp_entry_status = 'In Progress',
            {set_time_col} = now()
        FROM cte
        WHERE {TABLE_NAME}.doc_id = cte.doc_id
        RETURNING
            {TABLE_NAME}.doc_id,
            {TABLE_NAME}.doc_file_name,
            cte.prev_erp_entry_status,
            {TABLE_NAME}.extracted_json,
            {TABLE_NAME}.corrected_json;
        """
        cur.execute(sql)
        row = cur.fetchone()
        conn.commit()
        return row


def set_erp_status(conn, doc_id, status, note=None,
                   use_erp_updated_at=False, use_erp_note=False):
    """
    Set erp_entry_status and optionally append a note to erp_note.
    """
    with conn.cursor() as cur:
        time_col = "erp_updated_at" if use_erp_updated_at else "updated_at"
        if use_erp_note:
            note_text = f"\n[{datetime.utcnow().isoformat()}] {note or status}"
            sql = f"""
                UPDATE {TABLE_NAME}
                SET erp_entry_status = %s,
                    {time_col} = now(),
                    erp_note = COALESCE(erp_note, '') || %s
                WHERE doc_id = %s;
            """
            cur.execute(sql, (status, note_text, doc_id))
        else:
            sql = f"""
                UPDATE {TABLE_NAME}
                SET erp_entry_status = %s,
                    {time_col} = now()
                WHERE doc_id = %s;
            """
            cur.execute(sql, (status, doc_id))
        conn.commit()


def update_json_column(conn, doc_id, new_json_obj):
    """Update extracted_json column safely with psycopg2 JSON adapter."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE_NAME} SET {JSON_COLUMN} = %s WHERE doc_id = %s;",
            (extras.Json(new_json_obj), doc_id),
        )
        conn.commit()


# ----------------------------
# Helper: overall status updater (prevents NameError)
# ----------------------------
def update_overall_status(conn, doc_id, status_value="Completed"):
    """
    Safe helper to mark overall_status for the given doc_id.
    This function is intentionally conservative:
      - Attempts a simple UPDATE to overall_status column if it exists.
      - Does not raise on failure; logs and returns False in that case.
    """
    try:
        cols = get_table_columns(conn)
        if 'overall_status' not in cols:
            print("ℹ️ 'overall_status' column not present — skipping update_overall_status.")
            return False
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE {table}
                SET overall_status = %s,
                    updated_at = now()
                WHERE doc_id = %s;
            """.format(table=TABLE_NAME), (status_value, doc_id))
            conn.commit()
        print(f"✅ overall_status set to '{status_value}' for doc_id={doc_id}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to update overall_status for doc_id={doc_id}: {e}")
        return False


# ----------------------------
# Selenium Automation Core
# ----------------------------
def process_row_with_driver(driver, row, conn, use_erp_updated_at, use_erp_note):
    """
    Process a single claimed row using the provided Selenium driver.
    Returns: (True/False, message)
    """
    try:
        raw = row.get('json_col')
        data = parse_final_data(raw)
        branch = (data.get("Branch") or data.get("branch") or "").strip()
        if not branch:
            return False, "No 'Branch' found in parsed data"

        # Navigate to correct branch/page
        select_branch(driver, branch)
        open_operations(driver)
        open_consignment_page(driver)

        fname = row.get('doc_file_name') or f"doc_{row.get('doc_id')}"
        prefix = os.path.splitext(os.path.basename(fname))[0]
        doc_id = row.get("doc_id")

        # 🧩 STEP 1 — Fill form (this returns True/False; Submit remains commented in consignment_form)
        submit_ok = fill_consignment_form(driver, data=data, prefix=prefix)

        # 🧩 STEP 2 — Build validation report (even if submit fails)
        validation_status = {}
        try:
            validation_status = build_validation_status(driver, data)
            print(f"🧾 Validation built: {len(validation_status.get('FailedFields', []))} field(s) failed.")
        except Exception as ve:
            print(f"⚠️ Validation generation error: {ve}")
            validation_status = {
                "isPassed": False,
                "FailedFields": [{
                    "Field": "Validation",
                    "CurrentValue": "",
                    "ERPValue": "",
                    "Reason": f"Exception: {ve}"
                }]
            }

        # Attach ValidationStatus to the same JSON structure and save back
        try:
            parsed_json = parse_final_data(raw)
            # ensure parsed_json is a dict
            if not isinstance(parsed_json, dict):
                parsed_json = {}
            parsed_json["ValidationStatus"] = validation_status
            update_json_column(conn, doc_id, parsed_json)
            print(f"🗃️ ValidationStatus saved into {JSON_COLUMN} for doc_id={doc_id}")
        except Exception as je:
            print(f"⚠️ Failed to update JSON column for doc_id={doc_id}: {je}")

        # 🧩 STEP 3 — Set ERP status and handle failures
        if submit_ok:
            # ✅ Successful ERP entry - mark Completed
            try:
                set_erp_status(conn, doc_id, "Completed", note="ERP entry completed by automation",
                               use_erp_updated_at=use_erp_updated_at, use_erp_note=use_erp_note)
                print(f"✅ doc_id {doc_id} processed successfully.")
            except Exception as e:
                print(f"⚠️ Failed to set erp status Completed for doc_id={doc_id}: {e}")

            # Update overall_status (best-effort)
            try:
                update_overall_status(conn, doc_id, status_value="Completed")
            except Exception as e:
                print(f"⚠️ update_overall_status error for doc_id={doc_id}: {e}")

        else:
            # ❌ Form fill failed or missing fields -> mark Failed and store a helpful note + screenshot
            safe_prefix = os.path.splitext(os.path.basename(fname))[0]
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            screenshot_name = f"{safe_prefix}_{doc_id}_{ts}.png"
            screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_name)
            try:
                driver.save_screenshot(screenshot_path)
                note = f"Form fill failed - missing or invalid field(s). Screenshot: {screenshot_path}"
            except Exception:
                note = "Form fill failed - missing or invalid field(s). (No screenshot available)"

            try:
                set_erp_status(conn, doc_id, "Failed", note=note,
                               use_erp_updated_at=use_erp_updated_at, use_erp_note=use_erp_note)
                print(f"❌ doc_id {doc_id} marked as Failed due to form fill issues.")
            except Exception as e:
                print(f"⚠️ Failed to set erp status Failed for doc_id={doc_id}: {e}")

        return True, "OK"


    except Exception as e:
        tb = traceback.format_exc()
        print(f"❌ process_row_with_driver exception: {e}\n{tb}")
        return False, str(e)


# ----------------------------
# Main Loop
# ----------------------------
def main_db_process(max_iterations=0):
    print("🚀 Starting DB driven processing loop...")
    conn = None
    claimed_conn = None
    try:
        conn = get_conn()
        # We use one DB connection to claim and update; functions that call get_conn() will obtain their own if needed.
        cols = get_table_columns(conn)
        use_erp_updated_at = 'erp_updated_at' in cols
        use_erp_note = 'erp_note' in cols

        print(f"Using JSON column: {JSON_COLUMN}")
        print(f"erp_updated_at: {use_erp_updated_at}, erp_note: {use_erp_note}")

        iterations = 0
        while True:
            if max_iterations and iterations >= max_iterations:
                break

            row = claim_one_row(conn, JSON_COLUMN, use_erp_updated_at)
            if not row:
                print("⚠️ No pending rows found. Exiting loop.")
                break

            prev_erp_status = (row.get('prev_erp_entry_status') or '').strip().upper()
            corrected_raw = row.get('corrected_json')
            extracted_raw = row.get('extracted_json')

            # Choose JSON source
            if prev_erp_status == 'FIXED' and corrected_raw and str(corrected_raw).strip() not in ('', '{}', 'null'):
                json_data = corrected_raw
                print("📘 Using corrected_json (status FIXED).")
            else:
                json_data = extracted_raw
                print("📗 Using extracted_json (status NOT STARTED or other).")

            row['json_col'] = json_data
            doc_id = row['doc_id']
            file_name = row.get('doc_file_name') or f"doc_{doc_id}"
            print(f"\n▶️ Claimed doc_id={doc_id}, file={file_name}")

            driver = build_driver()
            try:
                login(driver)
                success, msg = process_row_with_driver(driver, row, conn, use_erp_updated_at, use_erp_note)
                if not success:
                    raise Exception(msg)
            except Exception as e:
                tb = traceback.format_exc()
                try:
                    set_erp_status(conn, doc_id, "Failed", note=f"Driver/login error: {e}",
                                   use_erp_updated_at=use_erp_updated_at, use_erp_note=use_erp_note)
                except Exception as se:
                    print(f"⚠️ Failed to set erp status after driver/login error for doc_id={doc_id}: {se}")
                print(f"❌ doc_id {doc_id} failed during setup: {e}\n{tb}")
            finally:
                print("⏳ Waiting 30s before closing browser...")
                sleep(30)
                try:
                    driver.quit()
                except Exception:
                    pass

            iterations += 1

    except Exception as ex:
        print("DB loop error:", ex)
    finally:
        if conn:
            try:
                release_conn(conn)
            except Exception:
                pass
        print("🏁 Finished DB loop.")


# ----------------------------
# Entry Point
# ----------------------------
if __name__ == "__main__":
    # You can pass max_iterations as needed for testing
    main_db_process(max_iterations=0)
