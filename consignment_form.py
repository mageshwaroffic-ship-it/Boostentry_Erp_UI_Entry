# consignment_form.py — v9.0.1
# - Fix: use _ensure_select (not ensure_select) for GST select + after GST toggle
# - Fills everything, audits, builds FailedFields, and only clicks Submit when all_ok
# - Returns dict: {"all_ok": bool, "failed_fields": [...], "submit": {"submitted": bool, "error": str|None}}

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

GST_TOGGLED_ONCE = False
CONSIGNEE_TRIED_BOTH = False
LAST_FILLED_FIELD: Optional[str] = None
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

def wait_until_value(driver, locator: Tuple[str,str], expected: str, timeout: float = 8.0, casefold: bool = True) -> bool:
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

# ---------- keyboard ----------
def _dispatch_enter_to_active(driver):
    try:
        driver.execute_script("""
            var ae = document.activeElement;
            if (ae) {
                var e = new KeyboardEvent('keydown', {key:'Enter',keyCode:13,which:13,bubbles:true});
                ae.dispatchEvent(e);
                e = new KeyboardEvent('keyup', {key:'Enter',keyCode:13,which:13,bubbles:true});
                ae.dispatchEvent(e);
            }
        """)
        time.sleep(0.08)
        return True
    except Exception:
        return False

def _dispatch_tab_to_active(driver):
    try:
        driver.execute_script("""
            var ae = document.activeElement;
            if (ae) {
                var e = new KeyboardEvent('keydown', {key:'Tab',keyCode:9,which:9,bubbles:true});
                ae.dispatchEvent(e);
                e = new KeyboardEvent('keyup', {key:'Tab',keyCode:9,which:9,bubbles:true});
                ae.dispatchEvent(e);
            }
        """)
        time.sleep(0.08)
        return True
    except Exception:
        return False

# ---------- element helpers ----------
def _remove_readonly_and_enable(driver, el):
    try:
        driver.execute_script("""
            const el = arguments[0];
            try { el.removeAttribute('readonly'); } catch(e){}
            try { el.readOnly = false; } catch(e){}
            try { el.disabled = false; } catch(e){}
            try { el.removeAttribute('disabled'); } catch(e){}
        """, el)
    except Exception:
        pass

def _native_value_set_and_fire(driver, el, value: str):
    return driver.execute_script("""
        const el = arguments[0], v = arguments[1];
        try {
            const proto = (el instanceof HTMLTextAreaElement)
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) { desc.set.call(el, v); } else { el.value = v; el.setAttribute('value', v); }
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur',   { bubbles: true }));
            try { if (window.jQuery) window.jQuery(el).val(v).trigger('input').trigger('change').trigger('blur'); } catch(e){}
            try {
                if (window.angular && window.angular.element) {
                    const ael = window.angular.element(el);
                    try { ael.triggerHandler('input'); } catch(e){}
                    try { ael.triggerHandler('change'); } catch(e){}
                    try { ael.triggerHandler('blur'); } catch(e){}
                }
            } catch(e){}
            return el.value || '';
        } catch(e) { return '<ERR>'; }
    """, el, value)

def _type_and_optionally_pick(driver, el, text: str, try_pick: bool = True):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    _remove_readonly_and_enable(driver, el)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    time.sleep(0.05)
    try:
        el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
    except Exception:
        try:
            el.clear()
        except Exception:
            driver.execute_script("arguments[0].value='';", el)
    try:
        el.send_keys(text)
    except ElementNotInteractableException:
        _native_value_set_and_fire(driver, el, text)
    except Exception:
        _native_value_set_and_fire(driver, el, text)
    if try_pick:
        if pick_from_autocomplete(driver, text, mode="equals", timeout=2.0):
            return True
        if pick_from_autocomplete(driver, text, mode="contains", timeout=2.0):
            return True
    try:
        el.send_keys(Keys.TAB)
    except Exception:
        pass
    wait_for_idle_fast(driver)
    return True

def _read_el_value(driver, el) -> str:
    try:
        return (el.get_attribute("value") or "").strip()
    except Exception:
        try:
            return (el.text or "").strip()
        except Exception:
            try:
                return driver.execute_script("return arguments[0].textContent||'';", el).strip()
            except Exception:
                return ""

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
def _push_audit(key: str, expected: str, ui_val: str, ok: bool, score: float, mode: str, note: str = ""):
    FIELD_AUDIT.append({
        "Field": key,
        "Expected": str(expected or ""),
        "UI": str(ui_val or ""),
        "OK": bool(ok),
        "Score": round(score if score is not None else 0.0, 3),
        "Mode": mode,
        "Note": note or "",
    })

def _print_audit_summary(prefix: Optional[str] = None):
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
    try:
        if prefix:
            ss(None, f"{prefix}_audit_summary.txt")
    except Exception:
        pass

# ---------- checks ----------
def immediate_field_check(driver, key: str, locator: Tuple[str,str], expected: str, verify_mode: str = "equals") -> bool:
    if expected is None:
        expected = ""
    if expected == "":
        ui_val = read_ui_value(driver, locator)
        _push_audit(key, expected, ui_val, False, 0.0, verify_mode, note="Missing value")
        print(f"❌ Immediate check for {key}: expected empty (Missing value)")
        return False

    wait_for_idle_fast(driver, total_timeout=2.0)
    ui_val = read_ui_value(driver, locator)
    print(f"⏱ Immediate check for {key}: expected={expected!r}, ui_val={ui_val!r}")

    if not ui_val:
        _push_audit(key, expected, ui_val, False, 0.0, verify_mode, note="UI empty")
        print(f"❌ Immediate check failed for {key}: UI empty")
        return False

    if numeric_equal(expected, ui_val, abs_tol=0.01, rel_tol=0.001):
        _push_audit(key, expected, ui_val, True, 1.0, verify_mode, note="numeric~= OK")
        print(f"✅ Immediate numeric match for {key}: {expected!r} ~= {ui_val!r}")
        return True

    if verify_mode == "date":
        if _date_equal(expected, ui_val):
            _push_audit(key, expected, ui_val, True, 1.0, "date", note="date normalized OK")
            return True
        score = similarity_ratio(expected, ui_val)
        _push_audit(key, expected, ui_val, False, score, "date", note="date mismatch")
        print(f"❌ Immediate date check failed for {key}")
        return False

    if verify_mode == "contains":
        if expected.casefold() in ui_val.casefold():
            _push_audit(key, expected, ui_val, True, 1.0, "contains", note="substring OK")
            return True
        score = similarity_ratio(expected, ui_val)
        ok = score >= IMMEDIATE_CHECK_THRESHOLD
        _push_audit(key, expected, ui_val, ok, score, "contains", note=("fuzzy OK" if ok else "fuzzy<0.70"))
        if ok:
            return True
        print(f"❌ Immediate contains/fuzzy check failed for {key} ({score:.2f})")
        return False

    score = similarity_ratio(expected, ui_val)
    ok = score >= IMMEDIATE_CHECK_THRESHOLD or expected.strip().casefold() == ui_val.strip().casefold()
    _push_audit(key, expected, ui_val, ok, score if score is not None else 0.0, "equals", note=("fuzzy OK" if ok else "fuzzy<0.70"))
    if ok:
        return True
    print(f"❌ Immediate fuzzy check failed for {key} ({score:.2f})")
    return False

def try_set_with_retry(setter: Callable[[], None], driver, key: str, locator: Tuple[str,str], expected: str, verify_mode: str = "equals", prefix: Optional[str] = None) -> bool:
    try:
        setter()
    except Exception as e:
        print(f"⚠️ Setter for {key} raised: {e}")

    ok = immediate_field_check(driver, key, locator, expected, verify_mode=verify_mode)
    if ok:
        return True

    print(f"↻ {key}: first immediate check failed — retrying once.")
    try:
        setter()
    except Exception as e:
        print(f"⚠️ Retry setter for {key} raised: {e}")

    wait_for_idle_fast(driver, total_timeout=1.5)
    ok2 = immediate_field_check(driver, key, locator, expected, verify_mode=verify_mode)
    if ok2:
        print(f"✅ {key} passed on retry.")
        return True

    print(f"❌ {key} failed after retry.")
    try:
        if prefix:
            ss(driver, f"{prefix}_{key}_failed_after_retry.png", prefix=prefix)
    except Exception:
        pass
    return False

# ---------- autocomplete ----------
def pick_from_autocomplete(driver, target: str, mode: str = "equals", timeout: float = 3.0) -> bool:
    global LAST_ALERT_ACCEPTED
    LAST_ALERT_ACCEPTED = False
    target_up = (target or "").strip().upper()
    if not target_up:
        return False
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul.ui-autocomplete li")))
    except TimeoutException:
        return False
    options = driver.find_elements(By.CSS_SELECTOR, "ul.ui-autocomplete li")
    for opt in options:
        txt = (opt.text or "").strip().upper()
        if txt == target_up:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                opt.click()
            except Exception:
                driver.execute_script("arguments[0].click();", opt)
            _dispatch_enter_to_active(driver)
            _accept_alert_if_any(driver, timeout=1)
            _dispatch_enter_to_active(driver)
            _dispatch_tab_to_active(driver)
            wait_for_idle_fast(driver)
            return True
    if mode == "contains":
        for opt in options:
            txt = (opt.text or "").strip().upper()
            if target_up in txt:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                    opt.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", opt)
                _dispatch_enter_to_active(driver)
                _accept_alert_if_any(driver, timeout=1)
                _dispatch_enter_to_active(driver)
                _dispatch_tab_to_active(driver)
                wait_for_idle_fast(driver)
                return True
    wait_for_idle_fast(driver)
    return False

def set_autocomplete_verify(
    driver,
    field_locator: Tuple[str,str],
    value: str,
    verify: str = "equals",
    require_dropdown_match: bool = False,
    dropdown_pick_mode: str = "contains",
    max_attempts: int = 5,
    prefix: Optional[str] = None,
) -> bool:
    global LAST_FILLED_FIELD, LAST_ALERT_ACCEPTED
    value = (value or "").strip()
    if not value:
        return False
    matched_once = False

    for attempt in range(1, max_attempts+1):
        try:
            LAST_ALERT_ACCEPTED = False
            wait_for_idle_fast(driver)
            try:
                el = WebDriverWait(driver, 6).until(EC.presence_of_element_located(field_locator))
            except Exception:
                time.sleep(0.25)
                continue
            try:
                cur_val = (el.get_attribute("value") or "").strip()
            except Exception:
                cur_val = ""
            if cur_val:
                if verify == "equals":
                    if fuzzy_ok(value, cur_val):
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True
                else:
                    if value.casefold() in cur_val.casefold() or similarity_ratio(value, cur_val) >= FUZZY_THRESHOLD:
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True

            try:
                safe_click(driver, field_locator)
            except Exception:
                cur_val2 = ""
                try:
                    cur_val2 = (el.get_attribute("value") or "").strip()
                except Exception:
                    pass
                if cur_val2:
                    if verify == "equals" and fuzzy_ok(value, cur_val2):
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True
                    if verify == "contains" and value.casefold() in cur_val2.casefold():
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True
                time.sleep(0.25)
                continue

            fast_type(driver, field_locator, value, clear=True)
            pick_ok = pick_from_autocomplete(driver, value, mode=dropdown_pick_mode, timeout=2.0)
            if pick_ok:
                matched_once = True
                try:
                    final_val = (el.get_attribute("value") or "").strip()
                except Exception:
                    final_val = ""
                if final_val:
                    if verify == "equals":
                        if fuzzy_ok(value, final_val):
                            LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                            return True
                    else:
                        if value.casefold() in final_val.casefold() or similarity_ratio(value, final_val) >= FUZZY_THRESHOLD:
                            LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                            return True
                if LAST_ALERT_ACCEPTED:
                    time.sleep(0.6)
                    try:
                        final_val2 = (el.get_attribute("value") or "").strip()
                    except Exception:
                        final_val2 = ""
                    if final_val2 and fuzzy_ok(value, final_val2):
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True
                    LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                    return True

            try:
                _dispatch_enter_to_active(driver)
                _dispatch_tab_to_active(driver)
            except Exception:
                pass
            wait_for_idle_fast(driver)

            try:
                final_val = (el.get_attribute("value") or "").strip()
            except Exception:
                final_val = ""
            if verify == "equals":
                if fuzzy_ok(value, final_val):
                    LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                    return True
            else:
                if value.casefold() in final_val.casefold() or similarity_ratio(value, final_val) >= FUZZY_THRESHOLD:
                    LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                    return True

            if matched_once and not final_val:
                LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                return True

            time.sleep(0.25)
        except Exception:
            time.sleep(0.25)

    if LAST_ALERT_ACCEPTED:
        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
        return True
    return False

def _friendly_field_name(locator: Tuple[str,str]) -> str:
    try:
        how, what = locator
        if isinstance(what, str):
            return what
        return str(locator)
    except Exception:
        return str(locator)

# ---------- JSON/content helpers ----------
def _get_json_value(data: dict, candidate_keys: List[str]) -> Optional[str]:
    if not data:
        return None
    for k in candidate_keys:
        if k in data:
            v = data.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    def norm_key(k: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", (k or "")).lower()
    normalized_map = {norm_key(k): k for k in data.keys()}
    for k in candidate_keys:
        nk = norm_key(k)
        if nk in normalized_map:
            real_k = normalized_map[nk]
            v = data.get(real_k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return None

def _tokenize_upper(s: str) -> List[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", (s or "").upper()) if t]

def value_has_tokens(value: str, required_tokens: List[str]) -> bool:
    hv = set(_tokenize_upper(value))
    req = set([t.upper() for t in required_tokens if t])
    return req.issubset(hv)

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
    toks = set(_tokenize_upper(gt))
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

_CNM_CANDIDATE_LOCATORS: List[Tuple[str,str]] = [
    (By.XPATH, "//*[@id='Name' and (self::input or self::textarea)]"),
    (By.ID, "Name"),
    (By.XPATH, "//input[@name='Name' and not(@type='hidden')]"),
    (By.XPATH, "//input[contains(@id,'Name') and not(@type='hidden')]"),
    (By.XPATH, "//input[contains(@name,'Name') and not(@type='hidden')]"),
    (By.XPATH, "//input[contains(translate(@placeholder,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'CONTENT') "
               "and contains(translate(@placeholder,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'NAME')]"),
    (By.XPATH, "//input[contains(translate(@aria-label,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'CONTENT') "
               "and contains(translate(@aria-label,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'NAME')]"),
]

def _find_in_any_frame(driver, candidates: List[Tuple[str,str]], timeout_each: float = 2.0):
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    for loc in candidates:
        try:
            el = WebDriverWait(driver, timeout_each).until(EC.presence_of_element_located(loc))
            return el, None
        except Exception:
            continue
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for idx, fr in enumerate(frames):
        try:
            if not fr.is_displayed():
                continue
        except Exception:
            continue
        try:
            driver.switch_to.frame(fr)
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            continue
        for loc in candidates:
            try:
                el = WebDriverWait(driver, timeout_each).until(EC.presence_of_element_located(loc))
                return el, idx
            except Exception:
                continue
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    return None, None

def _set_content_name_anyhow(driver, final_text: str, prefix: Optional[str] = None) -> bool:
    el, frame_idx = _find_in_any_frame(driver, _CNM_CANDIDATE_LOCATORS, timeout_each=2.0)
    if el is None:
        try: ss(driver, "22_insertitem_contentname_not_found.png", prefix=prefix)
        except Exception: pass
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass
    _type_and_optionally_pick(driver, el, final_text, try_pick=True)
    val = _read_el_value(driver, el)
    if val.strip():
        if val.strip().upper() == final_text.strip().upper() or value_has_tokens(val, [final_text]):
            try: ss(driver, "22_insertitem_contentname_ok.png", prefix=prefix)
            except Exception: pass
            return True
    _native_value_set_and_fire(driver, el, final_text)
    wait_for_idle_fast(driver)
    val2 = _read_el_value(driver, el)
    if val2.strip().upper() == final_text.strip().upper() or value_has_tokens(val2, [final_text]):
        try: ss(driver, "22_insertitem_contentname_forced.png", prefix=prefix)
        except Exception: pass
        return True
    synonyms = [final_text]
    if final_text.strip().upper() == "PPC PAPER":
        synonyms.extend(["PPC BAG (PAPER)", "PPC PAPER BAG", "PPC BAG PAPER", "PPC (PAPER) BAG"])
    for alt in synonyms:
        _type_and_optionally_pick(driver, el, alt, try_pick=True)
        val3 = _read_el_value(driver, el)
        if val3.strip().upper() == final_text.strip().upper() or value_has_tokens(val3, [final_text]):
            try: ss(driver, "22_insertitem_contentname_ok.png", prefix=prefix)
            except Exception: pass
            return True
    try: ss(driver, "22_insertitem_contentname_failed.png", prefix=prefix)
    except Exception: pass
    return False

# ---------- errors ----------
def detect_post_action_error(driver, timeout: float = 3.0) -> Optional[str]:
    try:
        wait_for_idle_fast(driver, total_timeout=1.0)
    except Exception:
        pass
    try:
        if _accept_alert_if_any(driver, timeout=0.5):
            return "JS Alert detected"
    except Exception:
        pass
    try:
        sw = driver.find_elements(By.CSS_SELECTOR, ".swal2-popup, .swal2-modal")
        for s in sw:
            try:
                if s.is_displayed():
                    txt = (s.text or "").strip()
                    if txt:
                        ltxt = txt.lower()
                        if any(k in ltxt for k in ("error", "failed", "invalid", "cannot", "please")):
                            return f"Swal2 popup: {txt[:120]}"
                        try:
                            btn = s.find_element(By.CSS_SELECTOR, ".swal2-confirm")
                            btn.click()
                        except Exception:
                            pass
            except Exception:
                continue
    except Exception:
        pass
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, ".toast-error, .toast.toast-error, .alert-danger, .alert.alert-danger")
        for el in candidates:
            try:
                if el.is_displayed():
                    m = (el.text or "").strip()
                    if m:
                        return f"UI error: {m[:120]}"
            except Exception:
                continue
    except Exception:
        pass
    try:
        nodes = driver.find_elements(By.XPATH, "//*[contains(translate(text(),'ERROR','error'),'error') or contains(translate(text(),'FAILED','failed'),'failed') or contains(translate(text(),'INVALID','invalid'),'invalid')]")
        for n in nodes:
            try:
                if n.is_displayed():
                    txt = (n.text or "").strip()
                    if txt and len(txt) > 3:
                        return f"UI message: {txt[:120]}"
            except Exception:
                continue
    except Exception:
        pass
    return None

# ---------- build_validation_status (secondary) ----------
def build_validation_status(driver, data: dict, numeric_tolerance: float = 0.1) -> dict:
    wait_for_idle_fast(driver, total_timeout=6.0)
    time.sleep(1.0)
    FIELD_LOCATORS = {
        "ConsignmentNo": (By.ID, "CNM_VNOSEQ"),
        "Date": (By.ID, "CNM_VDATE"),
        "Source": (By.ID, "CNM_FROM_STN_NAME"),
        "Destination": (By.ID, "CNM_TO_STN_NAME"),
        "Vehicle": (By.ID, "CNM_VEHICLENO"),
        "Consignor": (By.ID, "CNM_CNR_NAME"),
        "Consignee": (By.ID, "CNM_CNE_NAME"),
        "GSTType": (By.ID, "CNM_CNE_REGTYPE"),
        "Delivery Address": (By.ID, "CNM_DLV_ADDRESS"),
        "EWayBillNo": (By.ID, "CNM_EWAYBILLNO"),
    }
    AUTOCOMPLETE_FIELDS = {"Source", "Destination", "Consignor", "Consignee", "Vehicle"}
    failed = []

    def read_erp(locator):
        try:
            el = driver.find_element(*locator)
            tag = el.tag_name.lower() if hasattr(el, "tag_name") else ""
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
                pass
            return ""
        except Exception:
            return ""

    def _as_date_str(v):
        parts = re.findall(r"\d+", str(v))
        if len(parts) >= 3:
            d, m, y = parts[:3]
            if len(y) == 2:
                y = "20" + y
            return f"{d.zfill(2)}-{m.zfill(2)}-{y.zfill(4)}"
        return None

    for key, locator in FIELD_LOCATORS.items():
        json_val = str(data.get(key) or "").strip()
        erp_val = str(read_erp(locator) or "").strip()
        print(f"🔍 Checking {key}: JSON='{json_val}' | ERP='{erp_val}'")
        if not erp_val:
            if json_val:
                failed.append({"Field": key, "Reason": "UI field empty"})
            continue
        if not json_val:
            failed.append({"Field": key, "Reason": "Missing value"})
            continue
        if numeric_equal(json_val, erp_val, abs_tol=numeric_tolerance, rel_tol=0.001):
            continue
        if key in AUTOCOMPLETE_FIELDS and fuzzy_ok(json_val, erp_val):
            continue
        jd = _as_date_str(json_val)
        ed = _as_date_str(erp_val)
        if jd and ed and jd == ed:
            continue
        if json_val.lower() == erp_val.lower():
            continue
        if json_val.lower() in erp_val.lower():
            continue
        if similarity_ratio(json_val, erp_val) >= FUZZY_THRESHOLD:
            continue
        failed.append({"Field": key, "Reason": "Does not match invoice"})

    if failed:
        for f in failed:
            print(f"   → {f['Field']}: {f['Reason']}")
    else:
        print("✅ All ERP UI fields match extracted JSON (within fuzzy threshold).")
    return {"isPassed": len(failed) == 0, "FailedFields": failed}

# ---------- submit ----------
def _final_submit(driver, prefix: Optional[str] = None) -> Tuple[bool, Optional[str]]:
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

# ---------- UI label map ----------
_UI_LABELS = {
    "ConsignmentNo": "Consignment No",
    "Date": "Date",
    "Source": "Source",
    "Destination": "Destination",
    "Vehicle": "Vehicle",
    "EWayBillNo": "E-Way Bill No",
    "Consignor": "Consignor",
    "Consignee": "Consignee",
    "GSTType": "GST Type",
    "Delivery Address": "Delivery Address",
    "Invoice No": "Invoice No",
    "Invoice Date": "Invoice Date",
    "ContentName": "Content Name (Goods Name)",
    "ActualWeight": "Actual Weight",
    "Get Rate": "Rate",
    "E-WayBill ValidUpto": "E-WayBill ValidUpto",
    "E-Way Bill Date": "E-Way Bill Date",
    "E-Way Bill NO": "E-Way Bill No",
}

def _build_failed_fields_from_audit_and_missing(data: dict) -> List[Dict]:
    failed: List[Dict] = []

    for r in FIELD_AUDIT:
        if r.get("OK"):
            continue
        key = r.get("Field") or ""
        ui_label = _UI_LABELS.get(key, key)
        note = (r.get("Note") or "").lower()
        mode = (r.get("Mode") or "").lower()
        reason = "Does not match invoice"
        if "missing value" in note:
            reason = "Missing value"
        elif "ui empty" in note:
            reason = "UI field empty"
        elif "date" in mode and "mismatch" in note:
            reason = "Wrong date format / mismatch"
        elif "fuzzy" in note and "0.70" in note:
            reason = "Low similarity (<70%)"
        failed.append({"Field": ui_label, "Reason": reason})

    must_check_missing = [
        "ConsignmentNo","Date","Source","Destination","Vehicle",
        "EWayBillNo","Consignor","Consignee","GSTType","Delivery Address",
        "Invoice No","Invoice Date","ActualWeight","Get Rate",
        "E-WayBill ValidUpto","E-Way Bill Date","E-Way Bill NO"
    ]
    for k in must_check_missing:
        v = (data.get(k) if k in data else data.get(k.replace("_"," ")) )
        v = ("" if v is None else str(v).strip())
        if v == "":
            ui_label = _UI_LABELS.get(k, k)
            if not any(f["Field"] == ui_label for f in failed):
                failed.append({"Field": ui_label, "Reason": "Missing value"})

    cn_raw = _get_json_value(data, ["ContentName", "Content Name", "contentname", "content_name", "content", "itemname"])
    gt_raw = _get_json_value(data, ["GoodsType", "Goods Type", "goods_type", "goodstype", "goods", "type"])
    final_cn = compute_final_content_string_from_json(cn_raw, gt_raw)
    if not final_cn:
        ui_label = _UI_LABELS.get("ContentName", "Content Name (Goods Name)")
        if not any(f["Field"] == ui_label for f in failed):
            failed.append({"Field": ui_label, "Reason": "Missing value"})

    uniq, seen = [], set()
    for f in failed:
        t = (f["Field"], f["Reason"])
        if t in seen:
            continue
        uniq.append(f); seen.add(t)
    return uniq

# ---------- main filler ----------
def _ensure_select(driver, locator: Tuple[str,str], expected: str) -> bool:
    js_set_select_and_fire(driver, locator, expected)
    try:
        el = driver.find_element(*locator)
        val = (el.get_attribute("value") or "").strip()
        if (expected or "").strip().casefold() == val.strip().casefold():
            return True
        sel_txt = driver.execute_script("const s=arguments[0];return s.options[s.selectedIndex]?.text||'';", el)
        return (expected or "").strip().casefold() == (sel_txt or "").strip().casefold()
    except Exception:
        return False

def _toggle_gst_and_set(driver, current_expected: str) -> Optional[str]:
    try:
        cur = (current_expected or "").strip().casefold()
        new = "Registered" if "unregister" in cur else "Unregistered"
        locator = (By.ID, "CNM_CNE_REGTYPE")
        js_set_select_and_fire(driver, locator, new)
        el = driver.find_element(*locator)
        sel_txt = driver.execute_script("const s=arguments[0];return s.options[s.selectedIndex]?.text||'';", el)
        if (sel_txt or "").strip().casefold() == new.casefold() or (el.get_attribute("value") or "").strip().casefold() == new.casefold():
            print(f"🔁 GST toggled from '{current_expected}' to '{new}' and verified.")
            ss(driver, "gst_toggled.png")
            return new
        else:
            print(f"⚠️ GST toggle attempted to '{new}' but verification failed (selected={sel_txt}).")
            ss(driver, "gst_toggle_failed.png")
            return None
    except Exception as e:
        print(f"⚠️ GST toggle error: {e}")
        return None

def fill_consignment_form(driver, data, prefix: Optional[str] = None) -> Dict:
    global GST_TOGGLED_ONCE, CONSIGNEE_TRIED_BOTH, LAST_FILLED_FIELD, LAST_ALERT_ACCEPTED, FIELD_AUDIT
    GST_TOGGLED_ONCE = False
    CONSIGNEE_TRIED_BOTH = False
    LAST_FILLED_FIELD = None
    LAST_ALERT_ACCEPTED = False
    FIELD_AUDIT = []

    wait = WebDriverWait(driver, 20)
    wait_for_idle_fast(driver, total_timeout=6.0)

    try:
        # Consignment No
        cons_no = (data.get("ConsignmentNo") or "").strip()
        try_set_with_retry(lambda: safe_type(driver, (By.ID, "CNM_VNOSEQ"), cons_no, tab_after=True, clear=True),
                           driver, "ConsignmentNo", (By.ID, "CNM_VNOSEQ"), cons_no, verify_mode="equals", prefix=prefix)
        ss(driver, "08_consignment_no.png", prefix=prefix)

        # Date
        cons_date = (data.get("Date") or "").strip()
        try:
            el = wait.until(EC.presence_of_element_located((By.ID, "CNM_VDATE")))
            driver.execute_script("try{arguments[0].removeAttribute('readonly')}catch(e){}", el)
        except Exception:
            pass
        try_set_with_retry(lambda: safe_type(driver, (By.ID, "CNM_VDATE"), cons_date, tab_after=True, clear=True),
                           driver, "Date", (By.ID, "CNM_VDATE"), cons_date, verify_mode="date", prefix=prefix)
        ss(driver, "09_date_filled.png", prefix=prefix)

        # Source
        source_val = (data.get("Source") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_verify(driver, (By.ID, "CNM_FROM_STN_NAME"), source_val, verify="equals", max_attempts=6, prefix=prefix),
                           driver, "Source", (By.ID, "CNM_FROM_STN_NAME"), source_val, verify_mode="equals", prefix=prefix)
        ss(driver, "10_source_filled.png", prefix=prefix)

        # Destination
        dest_val = (data.get("Destination") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_verify(driver, (By.ID, "CNM_TO_STN_NAME"), dest_val, verify="equals", max_attempts=6, prefix=prefix),
                           driver, "Destination", (By.ID, "CNM_TO_STN_NAME"), dest_val, verify_mode="equals", prefix=prefix)
        ss(driver, "11_destination_filled.png", prefix=prefix)

        # Vehicle
        vehicle_val = (data.get("Vehicle") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_verify(driver, (By.ID, "CNM_VEHICLENO"), vehicle_val, verify="equals", max_attempts=6, prefix=prefix),
                           driver, "Vehicle", (By.ID, "CNM_VEHICLENO"), vehicle_val, verify_mode="equals", prefix=prefix)
        ss(driver, "12_vehicle_filled.png", prefix=prefix)

        # EWay Bill No
        eway_val = (data.get("EWayBillNo") or "").strip()
        try_set_with_retry(lambda: safe_type(driver, (By.ID, "CNM_EWAYBILLNO"), eway_val, tab_after=True, clear=True),
                           driver, "EWayBillNo", (By.ID, "CNM_EWAYBILLNO"), eway_val, verify_mode="contains", prefix=prefix)
        ss(driver, "13_ewaybill_filled.png", prefix=prefix)

        # Consignor
        consignor_val = (data.get("Consignor") or "").strip()
        try_set_with_retry(lambda: set_autocomplete_verify(driver, (By.ID, "CNM_CNR_NAME"), consignor_val, verify="contains", max_attempts=6, prefix=prefix),
                           driver, "Consignor", (By.ID, "CNM_CNR_NAME"), consignor_val, verify_mode="contains", prefix=prefix)
        ss(driver, "15_consignor_filled.png", prefix=prefix)

        # GST Type  (FIXED: _ensure_select)
        gst_type_val = (data.get("GSTType") or "").strip()
        try_set_with_retry(lambda: _ensure_select(driver, (By.ID, "CNM_CNE_REGTYPE"), gst_type_val),
                           driver, "GSTType", (By.ID, "CNM_CNE_REGTYPE"), gst_type_val, verify_mode="equals", prefix=prefix)
        ss(driver, "17_gsttype_filled.png", prefix=prefix)

        # Consignee (with one GST toggle fallback)
        consignee_val = (data.get("Consignee") or "").strip()
        def set_consignee():
            return set_autocomplete_verify(driver, (By.ID, "CNM_CNE_NAME"), consignee_val, verify="equals", max_attempts=3, prefix=prefix)
        ok_cnee = try_set_with_retry(set_consignee, driver, "Consignee", (By.ID, "CNM_CNE_NAME"), consignee_val, verify_mode="equals", prefix=prefix)
        ss(driver, "18_consignee_filled.png", prefix=prefix)
        if not ok_cnee:
            final_ui = read_ui_value(driver, (By.ID, "CNM_CNE_NAME"))
            if not (final_ui and similarity_ratio(consignee_val, final_ui) >= IMMEDIATE_CHECK_THRESHOLD):
                if not GST_TOGGLED_ONCE:
                    print("⚠️ Consignee low match — toggling GST and retrying once.")
                    new_gst = _toggle_gst_and_set(driver, gst_type_val)
                    GST_TOGGLED_ONCE = True
                    if new_gst:
                        data["GSTType"] = new_gst
                        _ensure_select(driver, (By.ID, "CNM_CNE_REGTYPE"), new_gst)  # FIXED: underscore
                        set_autocomplete_verify(driver, (By.ID, "CNM_CNE_NAME"), consignee_val, verify="equals", max_attempts=3, prefix=prefix)
                        immediate_field_check(driver, "Consignee", (By.ID, "CNM_CNE_NAME"), consignee_val, verify_mode="equals")

        CONSIGNEE_TRIED_BOTH = True
        try:
            el_c = driver.find_element(By.ID, "CNM_CNE_NAME")
            try:
                el_c.send_keys(Keys.ENTER); time.sleep(0.08); el_c.send_keys(Keys.TAB)
            except Exception:
                _dispatch_enter_to_active(driver); _dispatch_tab_to_active(driver)
            wait_for_idle_fast(driver, total_timeout=2.0)
            try:
                safe_click(driver, (By.ID, "CNM_DLV_ADDRESS"))
            except Exception:
                pass
            time.sleep(0.12)
        except Exception:
            pass

        # Delivery Address
        for _ in range(2):
            if not _close_any_popup(driver, timeout=2):
                break
            time.sleep(0.15)

        delivery_val = (data.get("Delivery Address") or "").strip()
        el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "CNM_DLV_ADDRESS")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        def set_delivery():
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
        try_set_with_retry(set_delivery, driver, "Delivery Address", (By.ID, "CNM_DLV_ADDRESS"), delivery_val, verify_mode="equals", prefix=prefix)
        ss(driver, "19_deliveryaddress_filled.png", prefix=prefix)

        # Insert Item
        try:
            safe_click(driver, (By.ID, "btnAddItem"))
            wait_for_idle_fast(driver)
            ss(driver, "21_additem_clicked.png", prefix=prefix)
            err = detect_post_action_error(driver, timeout=2.0)
            if err:
                _push_audit("InsertItem", "Open", f"Error: {err}", False, 0.0, "action", "Insert modal open failed")
        except Exception as e:
            _push_audit("InsertItem", "Open", f"Exception: {e}", False, 0.0, "action", "Insert modal click exception")

        for _ in range(2):
            if not _close_any_popup(driver, timeout=2):
                break
            time.sleep(0.15)

        # Invoice No (empty -> Missing value)
        inv_no = (data.get('Invoice No') or '').strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='InvcNo']"), inv_no, clear=True),
                           driver, "Invoice No", (By.XPATH, "//*[@id='InvcNo']"), inv_no, verify_mode="equals", prefix=prefix)

        # Content Name (derived)
        cn_raw = _get_json_value(data, ["ContentName", "Content Name", "contentname", "content_name", "content", "itemname"])
        gt_raw = _get_json_value(data, ["GoodsType", "Goods Type", "goods_type", "goodstype", "goods", "type"])
        final_cn = compute_final_content_string_from_json(cn_raw, gt_raw)
        if final_cn:
            def set_content():
                return _set_content_name_anyhow(driver, final_cn, prefix=prefix)
            set_content()
            found_loc = None
            for loc in _CNM_CANDIDATE_LOCATORS:
                try:
                    driver.find_element(*loc)
                    found_loc = loc
                    break
                except Exception:
                    continue
            if found_loc:
                ok = immediate_field_check(driver, "ContentName", found_loc, final_cn, verify_mode="equals")
                if not ok:
                    set_content()
                    wait_for_idle_fast(driver, total_timeout=1.0)
                    immediate_field_check(driver, "ContentName", found_loc, final_cn, verify_mode="equals")

        # Actual Weight
        aw_raw = (data.get('ActualWeight') or '').strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='Actual']"), aw_raw, clear=True),
                           driver, "ActualWeight", (By.XPATH, "//*[@id='Actual']"), aw_raw, verify_mode="equals", prefix=prefix)

        # E-WayBill ValidUpto
        evu = (data.get('E-WayBill ValidUpto') or '').strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='EwayBillExpDate']"), evu, clear=True),
                           driver, "E-WayBill ValidUpto", (By.XPATH, "//*[@id='EwayBillExpDate']"), evu, verify_mode="date", prefix=prefix)

        # Invoice Date
        invd = (data.get('Invoice Date') or '').strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='InvcDate']"), invd, clear=True),
                           driver, "Invoice Date", (By.XPATH, "//*[@id='InvcDate']"), invd, verify_mode="date", prefix=prefix)

        # E-Way Bill Date
        ebd = (data.get('E-Way Bill Date') or '').strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='EwayBillDate']"), ebd, clear=True),
                           driver, "E-Way Bill Date", (By.XPATH, "//*[@id='EwayBillDate']"), ebd, verify_mode="date", prefix=prefix)

        # E-Way Bill NO
        ebn = (data.get('E-Way Bill NO') or '').strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='EwayBillNo']"), ebn, clear=True),
                           driver, "E-Way Bill NO", (By.XPATH, "//*[@id='EwayBillNo']"), ebn, verify_mode="contains", prefix=prefix)

        ss(driver, "22_insertitem_filled.png", prefix=prefix)

        # Insert
        try:
            safe_click(driver, (By.XPATH, "//*[@id='btnInsert']"))
            ss(driver, "24_addinvoice_clicked.png", prefix=prefix)
            err = detect_post_action_error(driver, timeout=2.0)
            if err:
                _push_audit("InsertItem", "Insert", f"Error: {err}", False, 0.0, "action", "Insert error")
            else:
                _push_audit("InsertItem", "Insert", "OK", True, 1.0, "action", "Inserted")
        except Exception as e:
            _push_audit("InsertItem", "Insert", f"Exception: {e}", False, 0.0, "action", "Insert click exception")

        # Close modal
        try:
            safe_click(driver, (By.XPATH, "//*[@id='frvclose']"))
            wait_for_idle_fast(driver)
            ss(driver, "25_insertitem_closed.png", prefix=prefix)
        except Exception:
            pass

        # Rate
        rate_val = (data.get("Get Rate") or "").strip()
        try_set_with_retry(lambda: safe_type(driver, (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, tab_after=True, clear=True),
                           driver, "Get Rate", (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, verify_mode="equals", prefix=prefix)
        ss(driver, "27_rate_filled.png", prefix=prefix)

        for _ in range(2):
            if not _close_any_popup(driver, timeout=2):
                break
            time.sleep(0.15)

        _print_audit_summary(prefix=prefix)

        failed_fields = _build_failed_fields_from_audit_and_missing(data)
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
            "submit": submit_result
        }

    except Exception as e:
        print(f"❌ Error in fill_consignment_form: {e}")
        ss(driver, "fill_consignment_form_exception.png", prefix=prefix)
        _push_audit("Flow", "Complete", f"Exception: {e}", False, 0.0, "flow", "Main flow exception")
        _print_audit_summary(prefix=prefix)
        return {
            "all_ok": False,
            "failed_fields": [{"Field": "Flow", "Reason": f"Exception: {e}"}],
            "submit": {"submitted": False, "error": f"Exception: {e}"}
        }


__all__ = [
    "fill_consignment_form",
    "build_validation_status",
]
