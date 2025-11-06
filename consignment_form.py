# consignment_form.py — v9.7
# Change: After entering Consignment No and moving focus (TAB), check ONLY the "Create" button.
# If the button (given CSS selector) is present, return Duplicate immediately:
#   - Stop further processing
#   - Do NOT add any field failures
#   - No need to check Consignment No conversion/locking

import re
import time
from typing import Tuple, Optional, Dict, List, Callable

from difflib import SequenceMatcher
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementNotInteractableException,
    NoAlertPresentException,
)

from driver_utils import ss  # screenshot helper

IMMEDIATE_CHECK_THRESHOLD = 0.70
FUZZY_THRESHOLD = IMMEDIATE_CHECK_THRESHOLD

LAST_ALERT_ACCEPTED = False
FIELD_AUDIT: List[Dict] = []

SPINNER_SELECTORS = [
    ".blockUI", ".blockMsg", ".blockOverlay",
    ".loading", ".spinner", ".overlay", "#loading",
    ".ui-autocomplete-loading", ".modal-backdrop.show",
]

# ---------- similarity ----------
def similarity_ratio(a: str, b: str) -> float:
    try:
        return SequenceMatcher(None, (a or "").strip().lower(), (b or "").strip().lower()).ratio()
    except Exception:
        return 0.0

def fuzzy_ok(json_val: str, erp_val: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    if not json_val or not erp_val:
        return False
    if json_val.strip().casefold() == erp_val.strip().casefold():
        return True
    if json_val.strip().casefold() in erp_val.strip().casefold():
        return True
    return similarity_ratio(json_val, erp_val) >= threshold

# ---------- wait helpers ----------
def _jq_active(driver) -> int:
    try:
        return int(driver.execute_script("return (window.jQuery && jQuery.active) ? jQuery.active : 0;"))
    except Exception:
        return 0

def _spinners_present(driver) -> int:
    try:
        return int(driver.execute_script(
            "return Array.from(document.querySelectorAll(arguments[0])).filter(el=>el.offsetParent!==null).length;",
            ", ".join(SPINNER_SELECTORS)
        ))
    except Exception:
        try:
            return int(driver.execute_script(
                "return document.querySelectorAll('.loading,.spinner,.ui-autocomplete-loading,.modal-backdrop.show').length;"
            ))
        except Exception:
            return 0

def wait_for_idle_fast(driver, total_timeout: float = 4.0, quiet_time: float = 0.30, poll: float = 0.08) -> bool:
    end = time.time() + total_timeout
    stable_until = None
    while time.time() < end:
        try:
            ready = driver.execute_script("return document.readyState;") == "complete"
        except Exception:
            ready = True
        active = _jq_active(driver)
        spinners = _spinners_present(driver)
        if ready and active == 0 and spinners == 0:
            if stable_until is None:
                stable_until = time.time() + quiet_time
            if time.time() >= stable_until:
                return True
        else:
            stable_until = None
        time.sleep(poll)
    return False

def wait_until_value(driver, locator: Tuple[str,str], expected: str, timeout: float = 6.0, casefold: bool = True) -> bool:
    exp = (expected or "")
    if casefold:
        exp = exp.casefold()
    end = time.time() + timeout
    while time.time() < end:
        try:
            el = driver.find_element(*locator)
            val = (el.get_attribute("value") or "")
            if (val.casefold() if casefold else val) == exp:
                return True
        except Exception:
            pass
        time.sleep(0.10)
    return False

# ---------- safe click/type ----------
def _retry(f, tries=2, pause=0.3, exceptions=(StaleElementReferenceException,)):
    last = None
    for _ in range(tries):
        try:
            return f()
        except exceptions as e:
            last = e
            time.sleep(pause)
    if last:
        raise last

def safe_click(driver, locator: Tuple[str,str], timeout: float = 18):
    def _action():
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        return True
    _retry(_action)
    wait_for_idle_fast(driver)
    return True

def safe_type(driver, locator: Tuple[str,str], text: str, timeout: float = 12, tab_after: bool = False, clear: bool = True):
    def _action():
        el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        if clear:
            try:
                el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
            except Exception:
                try:
                    el.clear()
                except Exception:
                    driver.execute_script("arguments[0].value='';", el)
        try:
            el.send_keys(text)
        except Exception:
            driver.execute_script("arguments[0].value=arguments[1];", el, text)
            driver.execute_script("arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", el)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
        if tab_after:
            try:
                el.send_keys(Keys.TAB)
            except Exception:
                pass
        return True
    _retry(_action)
    wait_until_value(driver, locator, text, timeout=3.0)
    wait_for_idle_fast(driver)

def fast_type(driver, locator: Tuple[str,str], text: str, timeout: float = 8, clear: bool = True, blur: bool = False):
    el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    if clear:
        try:
            el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
        except Exception:
            try:
                el.clear()
            except Exception:
                driver.execute_script("arguments[0].value='';", el)
    try:
        el.send_keys(text)
    except Exception:
        driver.execute_script("arguments[0].value=arguments[1];", el, text)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", el)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
    if blur:
        try:
            el.send_keys(Keys.TAB)
        except Exception:
            pass
    time.sleep(0.05)

def js_set_select_and_fire(driver, locator: Tuple[str,str], value: str):
    el = WebDriverWait(driver, 12).until(EC.presence_of_element_located(locator))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].value=arguments[1]; arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el, value)
    wait_for_idle_fast(driver)

# ---------- popups ----------
def _accept_alert_if_any(driver, timeout=2) -> bool:
    global LAST_ALERT_ACCEPTED
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        try:
            alert_text = alert.text
        except Exception:
            alert_text = "<no text>"
        print(f"⚠️ Alert present: {alert_text!r} — accepting")
        try:
            alert.accept()
        except Exception:
            try:
                alert.dismiss()
            except Exception:
                pass
        time.sleep(0.18)
        wait_for_idle_fast(driver)
        LAST_ALERT_ACCEPTED = True
        return True
    except (TimeoutException, NoAlertPresentException):
        return False
    except Exception:
        return False

def _close_any_popup(driver, timeout=2) -> bool:
    if _accept_alert_if_any(driver, timeout=timeout):
        return True
    btn_selectors = [
        (By.ID, "btn-ok"),
        (By.XPATH, "//button[normalize-space()='OK']"),
        (By.XPATH, "//button[normalize-space()='Ok']"),
        (By.CSS_SELECTOR, ".swal2-confirm"),
        (By.XPATH, "//div[contains(@class,'modal')]//button[normalize-space()='OK']"),
    ]
    for how, what in btn_selectors:
        try:
            btn = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((how, what)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            try:
                btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            time.sleep(0.15)
            print(f"✅ Popup closed with selector: {how}={what}")
            wait_for_idle_fast(driver)
            return True
        except TimeoutException:
            continue
        except Exception as e:
            print(f"⚠️ Error closing popup: {e}")
            continue
    return False

def _popup_text(driver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".swal2-popup, .swal2-modal")
        if el.is_displayed():
            return (el.text or "").strip()
    except Exception:
        pass
    try:
        el = driver.find_element(By.XPATH, "//div[contains(@class,'modal') and contains(@class,'show')]")
        if el.is_displayed():
            return (el.text or "").strip()
    except Exception:
        pass
    return ""

def handle_known_alerts_after_rate(driver, prefix: Optional[str] = None) -> bool:
    wait_for_idle_fast(driver, total_timeout=0.6)
    txt = _popup_text(driver)
    if txt:
        print(f"🔎 Rate popup detected text: {txt!r}")
    if "no rate contract" in txt.lower():
        try: ss(driver, "rate_contract_alert.png", prefix=prefix)
        except Exception: pass
        closed = _close_any_popup(driver, timeout=4)
        if closed:
            print("✅ 'No Rate Contract defined' popup closed.")
        else:
            print("⚠️ Could not close 'No Rate Contract defined' popup via known selectors.")
        return True
    if _close_any_popup(driver, timeout=1):
        print("ℹ️ Post-rate generic popup closed.")
        return True
    return False

# ---------- read ----------
def read_ui_value(driver, locator: Tuple[str,str]) -> str:
    try:
        el = driver.find_element(*locator)
    except Exception:
        return ""
    try:
        tag = el.tag_name.lower()
    except Exception:
        tag = ""
    if tag == "select":
        try:
            sel_txt = driver.execute_script("const s=arguments[0];return s.options[s.selectedIndex]?.text||'';", el)
            if sel_txt and str(sel_txt).strip():
                return str(sel_txt).strip()
        except Exception:
            pass
    try:
        val = (el.get_attribute("value") or "").strip()
        if val:
            return val
    except Exception:
        pass
    try:
        txt = (el.text or "").strip()
        if txt:
            return txt
    except Exception:
        pass
    try:
        txt2 = driver.execute_script("return arguments[0].textContent||'';", el)
        return (txt2 or "").strip()
    except Exception:
        return ""

# ---------- numeric/date ----------
def _clean_number_text(s: str) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", s)
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = parts[0] + "." + "".join(parts[1:])
    if cleaned in ("", ".", "-", "-.", "-0"):
        return None
    return cleaned

def numeric_equal(a: str, b: str, abs_tol: float = 0.01, rel_tol: float = 0.001) -> bool:
    ca = _clean_number_text(a); cb = _clean_number_text(b)
    if not ca or not cb:
        return False
    try:
        fa = float(ca); fb = float(cb)
    except Exception:
        return False
    if abs(fa - fb) <= abs_tol:
        return True
    denom = max(abs(fa), abs(fb), 1.0)
    return abs(fa - fb) / denom <= rel_tol

def _parse_date_parts(s: str):
    if not s:
        return None
    parts = re.findall(r"\d+", s)
    if len(parts) >= 3:
        d, m, y = parts[0], parts[1], parts[2]
        if len(y) == 2:
            y = "20" + y
        d = d.zfill(2); m = m.zfill(2); y = y.zfill(4)
        return (d, m, y)
    return None

def _date_equal(a: str, b: str) -> bool:
    pa = _parse_date_parts(a or "")
    pb = _parse_date_parts(b or "")
    if pa and pb:
        return pa == pb
    na = re.sub(r"\D", "", (a or ""))
    nb = re.sub(r"\D", "", (b or ""))
    return na == nb

# ---------- audit ----------
def _push_audit(field_label: str, expected: str, ui_val: str, ok: bool, score: float, mode: str, note: str = ""):
    FIELD_AUDIT.append({
        "Field": field_label,
        "Expected": str(expected or ""),
        "UI": str(ui_val or ""),
        "OK": bool(ok),
        "Score": round(score if score is not None else 0.0, 3),
        "Mode": mode,
        "Note": note or "",
    })

def _print_audit_summary():
    print("\n================= FIELD ENTRY AUDIT (DB vs ERP UI) =================")
    if not FIELD_AUDIT:
        print("No audit entries.")
        return
    header = f"{'Field':<24} {'OK':<3} {'Score':<5}  {'Mode':<8}  Expected  |  UI"
    print(header)
    print("-" * len(header))
    for r in FIELD_AUDIT:
        print(f"{r['Field']:<24} {('✔' if r['OK'] else '✖'):<3} {str(r['Score']):<5}  {r['Mode']:<8}  {r['Expected']}  |  {r['UI']}")
        if r.get("Note"):
            print(f"{'':<24}     note: {r['Note']}")
    failed = [r for r in FIELD_AUDIT if not r["OK"]]
    print("-" * len(header))
    if failed:
        print(f"❌ Result: {len(failed)} field(s) failed 70% rule / empty mismatch.")
    else:
        print("✅ Result: all fields passed (>=70% / numeric/date OK).")
    print("====================================================================\n")

# ---------- checks ----------
def _immediate_check(driver, field_label: str, locator: Tuple[str,str], expected: str, verify_mode: str = "equals") -> bool:
    if expected is None:
        expected = ""
    wait_for_idle_fast(driver, total_timeout=2.0)
    ui_val = read_ui_value(driver, locator)
    print(f"⏱ Immediate check for {field_label}: expected={expected!r}, ui_val={ui_val!r}")

    if not expected.strip():
        _push_audit(field_label, expected, ui_val, False, 0.0, verify_mode, note="Missing value")
        return False

    if not ui_val:
        _push_audit(field_label, expected, ui_val, False, 0.0, verify_mode, note="UI empty")
        return False

    if verify_mode == "equals" and numeric_equal(expected, ui_val, abs_tol=0.01, rel_tol=0.001):
        _push_audit(field_label, expected, ui_val, True, 1.0, verify_mode, note="numeric~= OK")
        return True

    if verify_mode == "date":
        if _date_equal(expected, ui_val):
            _push_audit(field_label, expected, ui_val, True, 1.0, "date", note="date normalized OK")
            return True
        score = similarity_ratio(expected, ui_val)
        _push_audit(field_label, expected, ui_val, False, score, "date", note="date mismatch")
        return False

    if verify_mode == "contains":
        if expected.casefold() in ui_val.casefold():
            _push_audit(field_label, expected, ui_val, True, 1.0, "contains", note="substring OK")
            return True
        score = similarity_ratio(expected, ui_val)
        ok = score >= IMMEDIATE_CHECK_THRESHOLD
        _push_audit(field_label, expected, ui_val, ok, score, "contains", note=("fuzzy OK" if ok else "fuzzy<0.70"))
        return ok

    score = similarity_ratio(expected, ui_val)
    ok = score >= IMMEDIATE_CHECK_THRESHOLD or expected.strip().casefold() == ui_val.strip().casefold()
    _push_audit(field_label, expected, ui_val, ok, score, "equals", note=("fuzzy OK" if ok else "fuzzy<0.70"))
    return ok

def _persist_check(driver, field_label: str, locator: Tuple[str,str], expected: str, verify_mode: str = "equals") -> bool:
    wait_for_idle_fast(driver, total_timeout=1.0)
    time.sleep(0.15)
    ui_val = read_ui_value(driver, locator)
    if not ui_val:
        _push_audit(field_label, expected, ui_val, False, 0.0, verify_mode, note="not persisted (ERP doesn't have this value)")
        print(f"❌ Persist check failed for {field_label}: cleared after blur.")
        return False

    if verify_mode == "date" and _date_equal(expected, ui_val):
        return True
    if verify_mode == "contains" and expected.casefold() in ui_val.casefold():
        return True
    if numeric_equal(expected, ui_val, abs_tol=0.01, rel_tol=0.001):
        return True
    if expected.strip().casefold() == ui_val.strip().casefold():
        return True
    if similarity_ratio(expected, ui_val) >= IMMEDIATE_CHECK_THRESHOLD:
        return True

    _push_audit(field_label, expected, ui_val, False, similarity_ratio(expected, ui_val), verify_mode, note="not persisted (mismatch after blur)")
    print(f"❌ Persist mismatch for {field_label}: '{ui_val}'")
    return False

# ---------- robust autocomplete ----------
def _ensure_dropdown_and_pick(driver, field_label: str, locator: Tuple[str,str], value: str, verify_mode: str, max_attempts: int = 2) -> bool:
    value = (value or "").strip()
    if not value:
        return False

    for attempt in range(1, max_attempts + 1):
        try:
            el = WebDriverWait(driver, 8).until(EC.presence_of_element_located(locator))
        except Exception:
            time.sleep(0.2)
            continue

        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        try:
            el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
        except Exception:
            try:
                el.clear()
            except Exception:
                driver.execute_script("arguments[0].value='';", el)

        try:
            el.send_keys(value)
        except Exception:
            driver.execute_script("arguments[0].value=arguments[1];", el, value)
            driver.execute_script("arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", el)

        wait_for_idle_fast(driver, total_timeout=0.8)

        options = []
        try:
            WebDriverWait(driver, 2.0).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul.ui-autocomplete li")))
            options = driver.find_elements(By.CSS_SELECTOR, "ul.ui-autocomplete li")
        except TimeoutException:
            options = []

        picked = False
        if options:
            target_up = value.upper()
            def _txt(opt):
                try:
                    return (opt.text or "").strip()
                except Exception:
                    return ""
            for opt in options:
                if (_txt(opt).strip().upper() == target_up):
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                        opt.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", opt)
                    picked = True
                    break
            if not picked:
                for opt in options:
                    if target_up in _txt(opt).strip().upper():
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                            opt.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", opt)
                        picked = True
                        break
            if not picked:
                opt = options[0]
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                    opt.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", opt)
                picked = True
        else:
            try:
                el.send_keys(Keys.ARROW_DOWN); el.send_keys(Keys.ENTER)
                picked = True
            except Exception:
                pass

        try:
            el.send_keys(Keys.TAB)
        except Exception:
            pass
        wait_for_idle_fast(driver, total_timeout=0.9)

        ui_val = read_ui_value(driver, locator)
        ok = False
        if verify_mode == "date":
            ok = _date_equal(value, ui_val)
        elif verify_mode == "contains":
            ok = bool(value and ui_val and value.casefold() in ui_val.casefold())
        elif numeric_equal(value, ui_val, abs_tol=0.01, rel_tol=0.001):
            ok = True
        else:
            ok = bool(value and ui_val and (value.strip().casefold() == ui_val.strip().casefold()
                                            or similarity_ratio(value, ui_val) >= IMMEDIATE_CHECK_THRESHOLD))
        if ok:
            return True

        time.sleep(0.2)

    return False

def set_autocomplete_and_move(driver, field_label: str, locator: Tuple[str,str], value: str, verify_mode: str) -> bool:
    ok = _ensure_dropdown_and_pick(driver, field_label, locator, value, verify_mode, max_attempts=2)
    return ok

def try_set_with_retry(setter: Callable[[], bool], driver, field_label: str, locator: Tuple[str,str], expected: str, verify_mode: str = "equals", prefix: Optional[str] = None) -> bool:
    try:
        ok = setter()
    except Exception as e:
        print(f"⚠️ Setter for {field_label} raised: {e}")
        ok = False

    ok_now = _immediate_check(driver, field_label, locator, expected, verify_mode=verify_mode)
    if ok and ok_now:
        return True

    print(f"↻ {field_label}: first immediate check failed — retrying once.")
    try:
        ok = setter()
    except Exception as e:
        print(f"⚠️ Retry setter for {field_label} raised: {e}")

    wait_for_idle_fast(driver, total_timeout=1.5)
    ok2 = _immediate_check(driver, field_label, locator, expected, verify_mode=verify_mode)
    if ok2:
        print(f"✅ {field_label} passed on retry.")
        return True

    print(f"❌ {field_label} failed after retry.")
    try:
        if prefix:
            ss(driver, f"{prefix}_{field_label}_failed_after_retry.png", prefix=prefix)
    except Exception:
        pass
    return False

# ---------- flexible JSON lookup ----------
def _get_json_value(data: dict, candidate_keys: List[str]) -> Optional[str]:
    if not data:
        return None
    for k in candidate_keys:
        if k in data and str(data.get(k)).strip():
            return str(data.get(k)).strip()
    def _norm(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", (s or "")).lower()
    norm_map = {_norm(k): k for k in data.keys()}
    for k in candidate_keys:
        nk = _norm(k)
        if nk in norm_map:
            v = data.get(norm_map[nk])
            if v is not None and str(v).strip():
                return str(v).strip()
    return None

# ---------- helpers for Content Name text ----------
def _normalize_base_from_json(content_name: str) -> Optional[str]:
    if not content_name:
        return None
    s = str(content_name).strip().upper()
    if "OPC" in s:
        return "OPC"
    if "PPC" in s:
        return "PPC"
    m = re.search(r"[A-Z]+", s)
    if m:
        return m.group(0)
    return None

def _normalize_goods_type_from_json(goods_type: str) -> Optional[str]:
    if not goods_type:
        return None
    gt = str(goods_type).strip().upper()
    if gt in ("BAG", "BULK", "PAPER"):
        return gt
    toks = set(re.split(r"[^A-Z0-9]+", gt))
    if "PAPER" in toks:
        return "PAPER"
    if "BULK" in toks or gt in ("BULKS", "BULK LOAD", "BULKLOAD"):
        return "BULK"
    if "BAG" in toks or gt in ("BAGS", "BAG(S)"):
        return "BAG"
    return gt

def compute_final_content_string_from_json(content_name_raw: str, goods_type_raw: str) -> Optional[str]:
    base = _normalize_base_from_json(content_name_raw)
    label = _normalize_goods_type_from_json(goods_type_raw)
    if not base or not label:
        return None
    if base == "OPC" and label == "PAPER":
        label = "BAG"
    return f"{base} {label}"

# ---------- duplicate detection inputs ----------
_CREATE_BTN_CSS = (
    "body > div.wrapper > div.content-wrapper > section.content-header > div > "
    "div.col-12.col-sm-12.col-md-4.col-lg-4.col-xl-4.col4 > div > div:nth-child(2) > "
    "div:nth-child(1) > a"
)

def _element_present(driver, css: str, timeout: float = 0.8) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        )
        return True
    except Exception:
        return False

# ---------- submission ----------
def _final_submit(driver, prefix: Optional[str] = None):
    try:
        safe_click(driver, (By.XPATH, "//*[@id='btnSubmit']"))
        wait_for_idle_fast(driver)
        print("✅ Submit button clicked successfully.")
        ss(driver, "28_submit_clicked.png", prefix=prefix)

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Successfully') or contains(text(),'successfully') or contains(text(),'Saved')]"))
            )
            print("🎉 Submission successful — success message detected.")
            return True, None
        except TimeoutException:
            print("⚠️ No success message found after submit — may have failed.")
            try:
                error_popup = driver.find_element(By.XPATH, "//*[contains(text(),'error') or contains(text(),'Error') or contains(text(),'failed')]")
                if error_popup.is_displayed():
                    err_text = (error_popup.text or "").strip() or "Unknown error"
                    print(f"❌ Error popup detected — submission failed: {err_text}")
                    ss(driver, "29_submit_error_detected.png", prefix=prefix)
                    return False, err_text
            except Exception:
                pass
            ss(driver, "29_submit_no_success.png", prefix=prefix)
            return False, "No success message after Submit"
    except Exception as e:
        print(f"❌ Failed to click Submit button: {e}")
        ss(driver, "28_submit_failed.png", prefix=prefix)
        return False, f"Submit click error: {e}"

# ---------- main filler ----------
def fill_consignment_form(driver, data, prefix: Optional[str] = None) -> Dict:
    """
    Returns:
      {
        "all_ok": bool,
        "failed_fields": [...],
        "submit": {"submitted": bool, "error": str|None},
        "duplicate": bool,
        "duplicate_info": {"reason": str} | None
      }
    """
    global LAST_ALERT_ACCEPTED, FIELD_AUDIT
    LAST_ALERT_ACCEPTED = False
    FIELD_AUDIT = []

    wait = WebDriverWait(driver, 20)
    wait_for_idle_fast(driver, total_timeout=6.0)

    try:
        LOC = {
            "Consignment No": (By.ID, "CNM_VNOSEQ"),
            "Date": (By.ID, "CNM_VDATE"),
            "Source": (By.ID, "CNM_FROM_STN_NAME"),
            "Destination": (By.ID, "CNM_TO_STN_NAME"),
            "Vehicle": (By.ID, "CNM_VEHICLENO"),
            "E-Way Bill No": (By.ID, "CNM_EWAYBILLNO"),
            "Consignor": (By.ID, "CNM_CNR_NAME"),
            "GST Type": (By.ID, "CNM_CNE_REGTYPE"),
            "Consignee": (By.ID, "CNM_CNE_NAME"),
            "Delivery Address": (By.ID, "CNM_DLV_ADDRESS"),
            "Rate": (By.XPATH, "//*[@id='CNM_RATE']"),
        }

        # ---------- Consignment No: type + TAB ----------
        cons_no = (data.get("ConsignmentNo") or "").strip()
        safe_type(driver, LOC["Consignment No"], cons_no, tab_after=True, clear=True)
        try: ss(driver, "08_consignment_no_typed.png", prefix=prefix)
        except Exception: pass

        # >>> DUPLICATE CHECK (ONLY the Create button, right after moving to next field) <<<
        wait_for_idle_fast(driver, total_timeout=1.2)
        create_btn_present = _element_present(driver, _CREATE_BTN_CSS, timeout=0.8)
        if create_btn_present:
            try: ss(driver, "08b_duplicate_create_button_detected.png", prefix=prefix)
            except Exception: pass
            print("🟠 DUPLICATE detected: Create button present right after Consignment No TAB.")
            FIELD_AUDIT = []
            return {
                "all_ok": False,
                "failed_fields": [],
                "submit": {"submitted": False, "error": "Duplicate detected"},
                "duplicate": True,
                "duplicate_info": {"reason": "Create button present after Consignment No"}
            }

        # Not duplicate → proceed & audit CN normally
        try_set_with_retry(lambda: (safe_type(driver, LOC["Consignment No"], cons_no, tab_after=True, clear=True) or True),
                           driver, "Consignment No", LOC["Consignment No"], cons_no, verify_mode="equals", prefix=prefix)
        ss(driver, "08_consignment_no.png", prefix=prefix)
        _persist_check(driver, "Consignment No", LOC["Consignment No"], cons_no, "equals")

        # ---------- Date ----------
        cons_date = (data.get("Date") or "").strip()
        try:
            el = wait.until(EC.presence_of_element_located(LOC["Date"]))
            driver.execute_script("try{arguments[0].removeAttribute('readonly')}catch(e){}", el)
        except Exception:
            pass
        try_set_with_retry(lambda: (safe_type(driver, LOC["Date"], cons_date, tab_after=True, clear=True) or True),
                           driver, "Date", LOC["Date"], cons_date, verify_mode="date", prefix=prefix)
        ss(driver, "09_date_filled.png", prefix=prefix)
        _persist_check(driver, "Date", LOC["Date"], cons_date, "date")

        # ---------- Source (autocomplete) ----------
        source_val = (data.get("Source") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_and_move(driver, "Source", LOC["Source"], source_val, "equals"),
                           driver, "Source", LOC["Source"], source_val, verify_mode="equals", prefix=prefix)
        ss(driver, "10_source_filled.png", prefix=prefix)
        _persist_check(driver, "Source", LOC["Source"], source_val, "equals")

        # ---------- Destination (autocomplete) ----------
        dest_val = (data.get("Destination") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_and_move(driver, "Destination", LOC["Destination"], dest_val, "equals"),
                           driver, "Destination", LOC["Destination"], dest_val, "equals", prefix=prefix)
        ss(driver, "11_destination_filled.png", prefix=prefix)
        _persist_check(driver, "Destination", LOC["Destination"], dest_val, "equals")

        # ---------- Vehicle (autocomplete) ----------
        vehicle_val = (data.get("Vehicle") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_and_move(driver, "Vehicle", LOC["Vehicle"], vehicle_val, "equals"),
                           driver, "Vehicle", LOC["Vehicle"], vehicle_val, "equals", prefix=prefix)
        ss(driver, "12_vehicle_filled.png", prefix=prefix)
        _persist_check(driver, "Vehicle", LOC["Vehicle"], vehicle_val, "equals")

        # ---------- E-Way Bill No (header) ----------
        eway_val_header = _get_json_value(data, ["EWayBillNo","EwayBillNo","E-Way Bill No","E-Way Bill NO"]) or ""
        try_set_with_retry(lambda: (safe_type(driver, LOC["E-Way Bill No"], eway_val_header, tab_after=True, clear=True) or True),
                           driver, "E-Way Bill No", LOC["E-Way Bill No"], eway_val_header, verify_mode="contains", prefix=prefix)
        ss(driver, "13_ewaybill_filled.png", prefix=prefix)
        _persist_check(driver, "E-Way Bill No", LOC["E-Way Bill No"], eway_val_header, "contains")

        # ---------- Consignor ----------
        consignor_val = (data.get("Consignor") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_and_move(driver, "Consignor", LOC["Consignor"], consignor_val, "contains"),
                           driver, "Consignor", LOC["Consignor"], consignor_val, "contains", prefix=prefix)
        ss(driver, "15_consignor_filled.png", prefix=prefix)
        _persist_check(driver, "Consignor", LOC["Consignor"], consignor_val, "contains")

        # ---------- GST Type ----------
        gst_type_val = (data.get("GSTType") or "").strip()
        try_set_with_retry(lambda: (js_set_select_and_fire(driver, LOC["GST Type"], gst_type_val) or True),
                           driver, "GST Type", LOC["GST Type"], gst_type_val, verify_mode="equals", prefix=prefix)
        ss(driver, "17_gsttype_filled.png", prefix=prefix)
        _persist_check(driver, "GST Type", LOC["GST Type"], gst_type_val, "equals")

        # ---------- Consignee ----------
        consignee_val = (data.get("Consignee") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_and_move(driver, "Consignee", LOC["Consignee"], consignee_val, "equals"),
                           driver, "Consignee", LOC["Consignee"], consignee_val, "equals", prefix=prefix)
        ss(driver, "18_consignee_filled.png", prefix=prefix)
        _persist_check(driver, "Consignee", LOC["Consignee"], consignee_val, "equals")

        # move focus into Delivery Address
        try:
            safe_click(driver, LOC["Delivery Address"])
        except Exception:
            pass
        wait_for_idle_fast(driver)

        # ---------- Delivery Address ----------
        delivery_val = (data.get("Delivery Address") or "").strip()
        def set_delivery():
            el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable(LOC["Delivery Address"]))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            try:
                el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
            except Exception:
                try:
                    el.clear()
                except Exception:
                    driver.execute_script("arguments[0].value='';", el)
            try:
                el.send_keys(delivery_val)
            except Exception:
                driver.execute_script("arguments[0].value=arguments[1];", el, delivery_val)
        try_set_with_retry(set_delivery, driver, "Delivery Address", LOC["Delivery Address"], delivery_val, verify_mode="equals", prefix=prefix)
        ss(driver, "19_deliveryaddress_filled.png", prefix=prefix)
        _persist_check(driver, "Delivery Address", LOC["Delivery Address"], delivery_val, "equals")

        # --- Insert Item modal ---
        try:
            safe_click(driver, (By.ID, "btnAddItem"))
            wait_for_idle_fast(driver)
            ss(driver, "21_additem_clicked.png", prefix=prefix)
        except Exception:
            pass

        # Invoice No
        inv_no = (data.get('Invoice No') or '').strip()
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='InvcNo']"), inv_no, clear=True) or True),
                           driver, "Invoice No", (By.XPATH, "//*[@id='InvcNo']"), inv_no, verify_mode="equals", prefix=prefix)

        # Content Name robust
        cn_raw = (data.get("ContentName") or data.get("Content Name") or "").strip()
        gt_raw = (data.get("GoodsType") or data.get("Goods Type") or "").strip()
        final_cn = compute_final_content_string_from_json(cn_raw, gt_raw)
        if final_cn:
            CN_LOC = (By.XPATH, "//*[@id='Name' and (self::input or self::textarea) or @id='Name']")
            def set_cn():
                return _ensure_dropdown_and_pick(driver, "Content Name (Goods Name)", CN_LOC, final_cn, "equals", max_attempts=6)
            try_set_with_retry(set_cn, driver, "Content Name (Goods Name)", CN_LOC, final_cn, verify_mode="equals", prefix=prefix)
            _persist_check(driver, "Content Name (Goods Name)", CN_LOC, final_cn, "equals")
            try: ss(driver, "22_insertitem_contentname.png", prefix=prefix)
            except Exception: pass

        # Actual Weight
        aw_raw = (data.get('ActualWeight') or '').strip()
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='Actual']"), aw_raw, clear=True) or True),
                           driver, "Actual Weight", (By.XPATH, "//*[@id='Actual']"), aw_raw, verify_mode="equals", prefix=prefix)

        # E-WayBill ValidUpto
        evu = (data.get('E-WayBill ValidUpto') or '').strip()
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='EwayBillExpDate']"), evu, clear=True) or True),
                           driver, "E-WayBill ValidUpto", (By.XPATH, "//*[@id='EwayBillExpDate']"), evu, verify_mode="date", prefix=prefix)

        # Invoice Date
        invd = (data.get('Invoice Date') or '').strip()
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='InvcDate']"), invd, clear=True) or True),
                           driver, "Invoice Date", (By.XPATH, "//*[@id='InvcDate']"), invd, verify_mode="date", prefix=prefix)

        # E-Way Bill Date
        ebd = (data.get('E-Way Bill Date') or '').strip()
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='EwayBillDate']"), ebd, clear=True) or True),
                           driver, "E-Way Bill Date", (By.XPATH, "//*[@id='EwayBillDate']"), ebd, verify_mode="date", prefix=prefix)

        # E-Way Bill No (INSIDE modal)
        ebn = _get_json_value(data, ["E-Way Bill NO","E-Way Bill No","EwayBillNo","EWayBillNo"]) or ""
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='EwayBillNo']"), ebn, clear=True) or True),
                           driver, "E-Way Bill No", (By.XPATH, "//*[@id='EwayBillNo']"), ebn, verify_mode="contains", prefix=prefix)

        ss(driver, "22_insertitem_filled.png", prefix=prefix)

        # Insert + close item modal
        try:
            safe_click(driver, (By.XPATH, "//*[@id='btnInsert']"))
            ss(driver, "24_addinvoice_clicked.png", prefix=prefix)
        except Exception:
            pass
        try:
            safe_click(driver, (By.XPATH, "//*[@id='frvclose']"))
            wait_for_idle_fast(driver)
            ss(driver, "25_insertitem_closed.png", prefix=prefix)
        except Exception:
            pass

        # Rate (+persist)
        rate_val = (data.get("Get Rate") or "").strip()
        try_set_with_retry(lambda: (safe_type(driver, (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, tab_after=True, clear=True) or True),
                           driver, "Rate", (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, verify_mode="equals", prefix=prefix)
        ss(driver, "27_rate_filled.png", prefix=prefix)
        _persist_check(driver, "Rate", (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, "equals")

        try:
            handle_known_alerts_after_rate(driver, prefix=prefix)
        except Exception:
            pass

        _print_audit_summary()

        # Build failed list from audit
        failed: List[Dict] = []
        for r in FIELD_AUDIT:
            if r.get("OK"):
                continue
            reason = "Does not match invoice"
            note = (r.get("Note") or "").lower()
            mode = (r.get("Mode") or "").lower()
            if "missing value" in note:
                reason = "Missing value"
            elif "ui empty" in note:
                reason = "UI field empty"
            elif "not persisted" in note:
                reason = "ERP doesn't have this value"
            elif "date" in mode and "mismatch" in note:
                reason = "Wrong date format / mismatch"
            elif "fuzzy" in note and "0.70" in note:
                reason = "Low similarity (<70%)"
            failed.append({"Field": r["Field"], "Reason": reason})
        # dedupe
        uniq, seen = [], set()
        for f in failed:
            t = (f["Field"], f["Reason"])
            if t in seen: continue
            uniq.append(f); seen.add(t)
        failed_fields = uniq

        all_ok = len(failed_fields) == 0
        submit_result = {"submitted": False, "error": None}
        if all_ok:
            ok, err = _final_submit(driver, prefix=prefix)
            submit_result["submitted"] = ok
            submit_result["error"] = err
        else:
            submit_result["submitted"] = False
            submit_result["error"] = "One or more fields failed validation"

        return {
            "all_ok": all_ok,
            "failed_fields": failed_fields,
            "submit": submit_result,
            "duplicate": False,
            "duplicate_info": None
        }

    except Exception as e:
        print(f"❌ Error in fill_consignment_form: {e}")
        ss(driver, "fill_consignment_form_exception.png", prefix=prefix)
        return {
            "all_ok": False,
            "failed_fields": [{"Field": "Flow", "Reason": f"Exception: {e}"}],
            "submit": {"submitted": False, "error": f"Exception: {e}"},
            "duplicate": False,
            "duplicate_info": None
        }


__all__ = ["fill_consignment_form"]
