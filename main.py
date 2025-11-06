#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — DB loop with Duplicate handling:
- If DUPLICATE detected right after Consignment No (Create button appears):
    -> erp_entry_status = 'Duplicate'
    -> overall_status   = 'Failed'
    -> write ValidationStatus with Duplicate (no FailedFields)
- Else same rules for Completed/Completed AHR/Failed/In Progress
"""
import os
import json
from time import sleep
from datetime import datetime, UTC
import traceback

import psycopg2
from psycopg2 import pool, extras

from driver_utils import build_driver
from login_page import login
from branch_page import select_branch
from operations_page import open_operations
from consignment_page import open_consignment_page
from consignment_form import fill_consignment_form

DB_CONFIG = {
    'dbname': 'mydb',
    'user': 'sql_developer',
    'password': 'Dev@123',
    'host': '103.14.123.44',
    'port': 5432,
}
TABLE_NAME = 'doc_processing_log'
JSON_COLUMN = 'extracted_json'

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

connection_pool = None
try:
    connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, **DB_CONFIG)
    print("✅ Connected to Remote PostgreSQL")
except Exception as e:
    print("❌ Database Connection Error:", e)
    raise

def get_conn():
    return connection_pool.getconn()

def release_conn(conn):
    return connection_pool.putconn(conn)

def get_table_columns(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s;
        """, (TABLE_NAME,))
        return {r[0] for r in cur.fetchall()}

def parse_final_data(value):
    if not value:
        return {}
    try:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode('utf-8', errors='ignore')
        if isinstance(value, str):
            txt = value.strip()
            if txt.startswith('"') and txt.endswith('"'):
                txt = txt[1:-1]
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

def update_json_column(conn, doc_id, new_json_obj):
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE_NAME} SET {JSON_COLUMN} = %s WHERE doc_id = %s;",
            (extras.Json(new_json_obj), doc_id),
        )
        conn.commit()

def claim_one_row(conn):
    with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
        sql = f"""
        WITH cte AS (
            SELECT doc_id, erp_entry_status AS prev_erp_entry_status
            FROM {TABLE_NAME}
            WHERE UPPER(data_extraction_status) = 'COMPLETED'
              AND UPPER(erp_entry_status) IN ('NOT STARTED','IN PROGRESS','FIXED')
            ORDER BY uploaded_on ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {TABLE_NAME}
        SET erp_entry_status = 'In Progress',
            updated_at = now()
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

def set_erp_status(conn, doc_id, status, note=None):
    cols = get_table_columns(conn)
    use_note = 'erp_note' in cols
    with conn.cursor() as cur:
        if use_note:
            note_text = f"\n[{datetime.now(UTC).isoformat()}] {note or status}"
            cur.execute(f"""
                UPDATE {TABLE_NAME}
                SET erp_entry_status = %s,
                    updated_at = now(),
                    erp_note = COALESCE(erp_note, '') || %s
                WHERE doc_id = %s;
            """, (status, note_text, doc_id))
        else:
            cur.execute(f"""
                UPDATE {TABLE_NAME}
                SET erp_entry_status = %s,
                    updated_at = now()
                WHERE doc_id = %s;
            """, (status, doc_id))
        conn.commit()

def update_overall_status(conn, doc_id, status_value="Completed"):
    try:
        cols = get_table_columns(conn)
        if 'overall_status' not in cols:
            print("ℹ️ 'overall_status' column not present — skipping overall_status.")
            return False
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE {TABLE_NAME}
                SET overall_status = %s,
                    updated_at = now()
                WHERE doc_id = %s;
            """, (status_value, doc_id))
            conn.commit()
        print(f"✅ overall_status set to '{status_value}' for doc_id={doc_id}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to update overall_status for doc_id={doc_id}: {e}")
        return False

def process_row_with_driver(driver, row, conn):
    try:
        raw_extracted = row.get('extracted_json')
        raw_corrected = row.get('corrected_json')
        prev_erp_status = (row.get('prev_erp_entry_status') or '').strip().upper()

        used_corrected = (prev_erp_status == 'FIXED' and raw_corrected and str(raw_corrected).strip() not in ('', '{}', 'null'))
        json_source = raw_corrected if used_corrected else raw_extracted
        print("📘 Using corrected_json (status FIXED)." if used_corrected else "📗 Using extracted_json (status NOT STARTED or other).")

        data = parse_final_data(json_source)

        branch = (data.get("Branch") or data.get("branch") or "").strip()
        if not branch:
            doc_id = row["doc_id"]
            parsed_json = parse_final_data(json_source) or {}
            if not isinstance(parsed_json, dict):
                parsed_json = {}
            parsed_json["ValidationStatus"] = {
                "FailedFields": [{"Field": "Branch", "Reason": "Missing value"}],
                "SubmitResult": {"Submitted": False, "ErrorText": "Missing Branch in data"}
            }
            update_json_column(conn, doc_id, parsed_json)
            set_erp_status(conn, doc_id, "Failed", note="Missing Branch in data")
            return False, "Missing Branch"

        select_branch(driver, branch)
        open_operations(driver)
        open_consignment_page(driver)

        fname = row.get('doc_file_name') or f"doc_{row.get('doc_id')}"
        prefix = os.path.splitext(os.path.basename(fname))[0]
        doc_id = row.get("doc_id")

        result = fill_consignment_form(driver, data=data, prefix=prefix)

        # ----- Duplicate: short-circuit -----
        if result.get("duplicate"):
            duplicate_info = result.get("duplicate_info") or {}
            validation_status_obj = {
                "FailedFields": [],
                "SubmitResult": {"Submitted": False, "ErrorText": "Duplicate detected"},
                "Duplicate": {"Detected": True, **duplicate_info}
            }
            try:
                parsed_json = parse_final_data(json_source)
                if not isinstance(parsed_json, dict):
                    parsed_json = {}
                parsed_json["ValidationStatus"] = validation_status_obj
                update_json_column(conn, doc_id, parsed_json)
                print(f"🗃️ Duplicate ValidationStatus saved for doc_id={doc_id}")
            except Exception as je:
                print(f"⚠️ Failed to update JSON for duplicate doc_id={doc_id}: {je}")

            # set erp_entry_status = Duplicate
            try:
                set_erp_status(conn, doc_id, "Duplicate",
                               note=f"Duplicate detected after Consignment No: {duplicate_info}")
                print(f"🟠 doc_id {doc_id} marked as Duplicate.")
            except Exception as e:
                print(f"⚠️ Failed to set ERP status 'Duplicate' for doc_id={doc_id}: {e}")

            # ALSO set overall_status = Failed (your new rule)
            try:
                update_overall_status(conn, doc_id, status_value="Failed")
            except Exception as e:
                print(f"⚠️ Failed to set overall_status='Failed' for doc_id={doc_id}: {e}")

            return True, "Duplicate"

        # ----- Normal path -----
        all_ok = bool(result.get("all_ok"))
        submit = result.get("submit") or {}
        submitted = bool(submit.get("submitted"))
        submit_err = submit.get("error")
        failed_fields = result.get("failed_fields") or []

        validation_status_obj = {
            "FailedFields": failed_fields,
            "SubmitResult": {
                "Submitted": submitted,
                "ErrorText": submit_err or None
            }
        }

        try:
            parsed_json = parse_final_data(json_source)
            if not isinstance(parsed_json, dict):
                parsed_json = {}
            parsed_json["ValidationStatus"] = validation_status_obj
            update_json_column(conn, doc_id, parsed_json)
            print(f"🗃️ ValidationStatus saved into {JSON_COLUMN} for doc_id={doc_id}")
        except Exception as je:
            print(f"⚠️ Failed to update JSON column for doc_id={doc_id}: {je}")

        if failed_fields:
            try:
                set_erp_status(conn, doc_id, "Failed",
                               note=f"{len(failed_fields)} field(s) failed validation")
                print(f"❌ doc_id {doc_id} marked Failed due to validation errors.")
            except Exception as e:
                print(f"⚠️ Failed to set ERP status Failed for doc_id={doc_id}: {e}")
            return True, "Validation failed -> Failed status"

        if all_ok and submitted:
            status_to_set = "Completed AHR" if used_corrected else "Completed"
            try:
                set_erp_status(conn, doc_id, status_to_set,
                               note="ERP entry submitted successfully")
                print(f"✅ doc_id {doc_id} processed & submitted successfully. Status = {status_to_set}")
            except Exception as e:
                print(f"⚠️ Failed to set ERP status {status_to_set} for doc_id={doc_id}: {e}")
            try:
                update_overall_status(conn, doc_id, status_value="Completed")
            except Exception as e:
                print(f"⚠️ update_overall_status error for doc_id={doc_id}: {e}")
            return True, status_to_set

        try:
            set_erp_status(conn, doc_id, "In Progress",
                           note=f"Submit failed: {submit_err or 'Unknown error'}")
            print(f"ℹ️ doc_id {doc_id} left as In Progress (submit failed).")
        except Exception as e:
            print(f"⚠️ Failed to set ERP status In Progress for doc_id={doc_id}: {e}")
        return True, "Submit failed"

    except Exception as e:
        tb = traceback.format_exc()
        print(f"❌ process_row_with_driver exception: {e}\n{tb}")
        return False, str(e)

def main_db_process(max_iterations=0):
    print("🚀 Starting DB driven processing loop...")
    conn = None
    try:
        conn = get_conn()
        iterations = 0
        while True:
            if max_iterations and iterations >= max_iterations:
                break

            row = claim_one_row(conn)
            if not row:
                print("⚠️ No pending rows found. Exiting loop.")
                break

            doc_id = row['doc_id']
            file_name = row.get('doc_file_name') or f"doc_{doc_id}"
            print(f"\n▶️ Claimed doc_id={doc_id}, file={file_name}")

            driver = build_driver()
            try:
                login(driver)
                success, msg = process_row_with_driver(driver, row, conn)
                if not success:
                    raise Exception(msg)
            except Exception as e:
                tb = traceback.format_exc()
                print(f"❌ doc_id {doc_id} failed during processing: {e}\n{tb}")
                try:
                    set_erp_status(conn, doc_id, "In Progress", note=f"Driver/login error: {e}")
                except Exception as se:
                    print(f"⚠️ Failed to set erp status after driver/login error for doc_id={doc_id}: {se}")
            finally:
                print("⏳ Waiting 5s before closing browser...")
                sleep(5)
                try:
                    driver.quit()
                except Exception:
                    pass

            iterations += 1

    except Exception as ex:
        print("DB loop error:", ex)
    finally:
        if conn:
            try: release_conn(conn)
            except Exception: pass
        print("🏁 Finished DB loop.")

if __name__ == "__main__":
    main_db_process(max_iterations=0)
