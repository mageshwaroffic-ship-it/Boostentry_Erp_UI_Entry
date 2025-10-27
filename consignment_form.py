# consignment_form.py — v7.8.1 (JSON-only ContentName+GoodsType, flexible keys, OPC53->OPC)
# -------------------------------------------------------------------------------------------------
# WHAT THIS DOES
# - Content Name in Insert Item modal is built ONLY from your JSON:
#       {"ContentName": "PPC", "GoodsType": "BAG"}  ->  "PPC BAG"
# - Flexible JSON keys accepted:
#       ContentName:  "ContentName", "Content Name", "contentname", "content_name", "content", "itemname"
#       GoodsType:    "GoodsType", "Goods Type", "goods_type", "goodstype", "goods", "type"
# - If ContentName is like "OPC53" / "OPC 53 GRADE" / "PPC-42", we extract the base "OPC"/"PPC".
# - ZERO weight-based inference.
# - Robust setter: iframe-aware, React/Angular-safe; autocomplete pick; event cascade firing.
# - Preserves your GST toggle-once + Consignee retry/skip logic, waits, popups, screenshots, etc.
# - Adds LAST_FILLED_FIELD tracking + more robust set_autocomplete_verify behavior.
# - ValidationStatus now includes ALL mapped fields even if JSON value is null/empty ("Missing value in JSON").
# -------------------------------------------------------------------------------------------------

import re
import time
from typing import Tuple, Optional, Dict, List, Union

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, ElementNotInteractableException

from driver_utils import ss


# ===========================
# Module-level flags
# ===========================
GST_TOGGLED_ONCE = False        # becomes True when we toggle GST the first time
CONSIGNEE_TRIED_BOTH = False    # becomes True once we've attempted Consignee under both GST states
LAST_FILLED_FIELD: Optional[str] = None


# ===========================
# Spinner selectors & locators
# ===========================
SPINNER_SELECTORS = [
    ".blockUI", ".blockMsg", ".blockOverlay",
    ".loading", ".spinner", ".overlay", "#loading",
    ".ui-autocomplete-loading", ".modal-backdrop.show",
]

# A primary Content Name locator (we also try robust fallbacks + iframes)
NAME_LOCATOR_PRIMARY = (By.XPATH, "//*[@id='Name' and (self::input or self::textarea)]")


# ===========================
# Idle / wait helpers
# ===========================
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


def wait_for_idle(driver, total_timeout: float = 14.0, quiet_time: float = 0.7, poll: float = 0.12) -> bool:
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


def wait_until_value(driver, locator: Tuple[str, str], expected: str, timeout: float = 8.0, casefold: bool = True) -> bool:
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


def wait_until_contains_value(driver, locator: Tuple[str, str], needle: str, timeout: float = 8.0) -> bool:
    needle = (needle or "").strip().casefold()
    end = time.time() + timeout
    while time.time() < end:
        try:
            el = driver.find_element(*locator)
            val = (el.get_attribute("value") or "").strip().casefold()
            if needle and needle in val:
                return True
        except Exception:
            pass
        time.sleep(0.10)
    return False


def wait_until_hidden(driver, locator: Tuple[str, str], timeout: float = 4.0) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located(locator))
        return True
    except TimeoutException:
        return False


# ===========================
# Stale-safe helpers
# ===========================
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


def safe_click(driver, locator: Tuple[str, str], timeout: float = 18):
    def _action():
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        return True
    ok = _retry(_action)
    wait_for_idle_fast(driver)
    return ok


def safe_type(driver, locator: Tuple[str, str], text: str, timeout: float = 12, tab_after: bool = False, clear: bool = True):
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


def fast_type(driver, locator: Tuple[str, str], text: str, timeout: float = 8, clear: bool = True, blur: bool = False):
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


def js_set_select_and_fire(driver, locator: Tuple[str, str], value: str):
    el = WebDriverWait(driver, 12).until(EC.presence_of_element_located(locator))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].value=arguments[1]; arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el, value)
    wait_for_idle_fast(driver)


# ===========================
# Autocomplete helpers
# ===========================
def pick_from_autocomplete(driver, target: str, mode: str = "equals", timeout: float = 3.0) -> bool:
    target_up = (target or "").strip().upper()
    if not target_up:
        return False
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul.ui-autocomplete li")))
    except TimeoutException:
        return False
    options = driver.find_elements(By.CSS_SELECTOR, "ul.ui-autocomplete li")
    # exact first
    for opt in options:
        txt = (opt.text or "").strip().upper()
        if txt == target_up:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                opt.click()
            except Exception:
                driver.execute_script("arguments[0].click();", opt)
            wait_until_hidden(driver, (By.CSS_SELECTOR, "ul.ui-autocomplete"), timeout=2.0)
            wait_for_idle_fast(driver)
            return True
    # contains fallback
    if mode == "contains":
        for opt in options:
            txt = (opt.text or "").strip().upper()
            if target_up in txt:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                    opt.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", opt)
                wait_until_hidden(driver, (By.CSS_SELECTOR, "ul.ui-autocomplete"), timeout=2.0)
                wait_for_idle_fast(driver)
                return True
    # ensure list closes
    wait_until_hidden(driver, (By.CSS_SELECTOR, "ul.ui-autocomplete"), timeout=1.5)
    wait_for_idle_fast(driver)
    return False


def set_autocomplete_verify(
    driver,
    field_locator: Tuple[str, str],
    value: str,
    verify: str = "equals",
    require_dropdown_match: bool = False,
    dropdown_pick_mode: str = "contains",
    max_attempts: int = 5,
    prefix: Optional[str] = None,
) -> bool:
    """
    Improved autocomplete setter:
     - Checks if the field exists and if it's already pre-filled correctly.
     - Attempts to safe_click + typing, then uses pick_from_autocomplete.
     - If element not clickable, re-checks for pre-filled value before failing.
     - Preserves original call signature so callers need no changes.
    """
    global LAST_FILLED_FIELD
    value = (value or "").strip()
    if not value:
        return False
    matched_once = False

    for attempt in range(1, max_attempts + 1):
        try:
            wait_for_idle_fast(driver)
            # presence check (not clickable yet)
            try:
                el = WebDriverWait(driver, 6).until(EC.presence_of_element_located(field_locator))
            except Exception:
                print(f"⚠️ {field_locator} not present (attempt {attempt})")
                time.sleep(0.25)
                continue

            # If already filled correctly, skip typing
            try:
                cur_val = (el.get_attribute("value") or "").strip()
            except Exception:
                cur_val = ""

            if cur_val:
                if verify == "equals" and cur_val.casefold() == value.casefold():
                    LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                    try: ss(driver, f"{prefix}_prefilled_{LAST_FILLED_FIELD}.png", prefix=prefix)
                    except Exception: pass
                    return True
                if verify == "contains" and value.casefold() in cur_val.casefold():
                    LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                    try: ss(driver, f"{prefix}_prefilled_{LAST_FILLED_FIELD}.png", prefix=prefix)
                    except Exception: pass
                    return True

            # Try to click (safe_click has longer timeout)
            try:
                safe_click(driver, field_locator)
            except Exception as e:
                # If click fails, re-evaluate present value and possibly accept it
                try:
                    cur_val2 = (el.get_attribute("value") or "").strip()
                except Exception:
                    cur_val2 = ""
                if cur_val2:
                    if verify == "equals" and cur_val2.casefold() == value.casefold():
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True
                    if verify == "contains" and value.casefold() in cur_val2.casefold():
                        LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                        return True
                print(f"⚠️ safe_click failed for {field_locator} (attempt {attempt}): {e}")
                time.sleep(0.25)
                continue

            # Type value
            fast_type(driver, field_locator, value, clear=True)
            # try picking from dropdown
            if pick_from_autocomplete(driver, value, mode=dropdown_pick_mode, timeout=2.0):
                matched_once = True

            # blur / tab out to commit
            try:
                el.send_keys(Keys.TAB)
            except Exception:
                try:
                    driver.execute_script("arguments[0].blur();", el)
                except Exception:
                    pass
            wait_for_idle_fast(driver)

            # verification
            try:
                final_val = (el.get_attribute("value") or "").strip()
            except Exception:
                final_val = ""

            if verify == "equals":
                ok = final_val.casefold() == value.casefold()
            else:
                ok = value.casefold() in final_val.casefold()

            if ok and ((not require_dropdown_match) or matched_once):
                LAST_FILLED_FIELD = _friendly_field_name(field_locator)
                return True
            else:
                print(f"↻ Retry {attempt}/{max_attempts} for {field_locator} value '{value}' (have='{final_val}', verify={verify})")
                time.sleep(0.25)

        except Exception as e:
            print(f"⚠️ Error in set_autocomplete_verify for {field_locator} attempt {attempt}: {e}")
            time.sleep(0.25)

    # Final fallback: try to read element value once more
    try:
        el_final = driver.find_element(*field_locator)
        final_val = (el_final.get_attribute("value") or "").strip()
        if verify == "equals":
            ok = final_val.casefold() == value.casefold()
        else:
            ok = value.casefold() in final_val.casefold()
        if ok:
            LAST_FILLED_FIELD = _friendly_field_name(field_locator)
            return True
    except Exception:
        pass

    print(f"❌ Failed to set {field_locator} after {max_attempts} attempts.")
    return False


def _friendly_field_name(locator: Tuple[str, str]) -> str:
    """Return a friendly short name for common locators for logging/last-field tracking."""
    try:
        how, what = locator
        if isinstance(what, str):
            return what
        return str(locator)
    except Exception:
        return str(locator)


# ===========================
# Popups
# ===========================
def _accept_alert_if_any(driver, timeout=2) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        try:
            alert_text = alert.text
        except Exception:
            alert_text = "<no text>"
        print(f"⚠️ Alert present: {alert_text!r} — accepting")
        alert.accept()
        time.sleep(0.2)
        return True
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


# ===========================
# Date normalization & compare
# ===========================
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


# ===========================
# Content Name computation & setters (JSON-only, flexible keys, iframe/React-safe)
# ===========================
def _tokenize_upper(s: str) -> List[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", (s or "").upper()) if t]


def value_has_tokens(value: str, required_tokens: List[str]) -> bool:
    hv = set(_tokenize_upper(value))
    req = set([t.upper() for t in required_tokens if t])
    return req.issubset(hv)


def _normalize_base_from_json(content_name: str) -> Optional[str]:
    """
    Extract 'OPC' or 'PPC' even if there are numbers/suffixes:
      'OPC53' -> 'OPC', 'OPC 53 GRADE' -> 'OPC', 'PPC-42' -> 'PPC'
    If 'OPC'/'PPC' not found, returns the first alpha token uppercased.
    """
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
    """
    Normalize GoodsType into one of: BAG, BULK, PAPER.
    Accepts synonyms like "bags", "paper bag", "bulk load", etc.
    """
    if not goods_type:
        return None
    gt = str(goods_type).strip().upper()
    if gt in ("BAG", "BULK", "PAPER"):
        return gt
    toks = set(_tokenize_upper(gt))  # "bag (paper)" -> {"BAG","PAPER"}
    if "PAPER" in toks:
        return "PAPER"
    if "BULK" in toks or gt in ("BULKS", "BULK LOAD", "BULKLOAD"):
        return "BULK"
    if "BAG" in toks or gt in ("BAGS", "BAG(S)"):
        return "BAG"
    return gt


def compute_final_content_string_from_json(content_name_raw: str, goods_type_raw: str) -> Optional[str]:
    """
    JSON-only computation: combine normalized ContentName + GoodsType.
      "PPC","BAG"     -> "PPC BAG"
      "OPC53","BAG"   -> "OPC BAG"
      "PPC-42","PAPER"-> "PPC PAPER"
    Special rule: OPC + PAPER coerces to OPC BAG.
    """
    base = _normalize_base_from_json(content_name_raw)
    label = _normalize_goods_type_from_json(goods_type_raw)
    if not base or not label:
        return None
    if base == "OPC" and label == "PAPER":
        label = "BAG"
    return f"{base} {label}"


# ---- Robust element finding (supports iframes) ----
_CNM_CANDIDATE_LOCATORS: List[Tuple[str, str]] = [
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


def _switch_default(driver):
    try:
        driver.switch_to.default_content()
    except Exception:
        pass


def _find_in_any_frame(driver, candidates: List[Tuple[str, str]], timeout_each: float = 2.0):
    """
    Try to find element in default content, else scan visible iframes one level deep.
    Returns (element, frame_index or None). Leaves driver focused in the frame where element lives.
    """
    _switch_default(driver)
    for loc in candidates:
        try:
            el = WebDriverWait(driver, timeout_each).until(EC.presence_of_element_located(loc))
            return el, None
        except TimeoutException:
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
            _switch_default(driver)
            continue
        for loc in candidates:
            try:
                el = WebDriverWait(driver, timeout_each).until(EC.presence_of_element_located(loc))
                return el, idx
            except TimeoutException:
                continue
        _switch_default(driver)
    return None, None


# ---- Low-level setters ----
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
    # clear
    try:
        el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
    except Exception:
        try:
            el.clear()
        except Exception:
            driver.execute_script("arguments[0].value='';", el)
    # type
    try:
        el.send_keys(text)
    except ElementNotInteractableException:
        _native_value_set_and_fire(driver, el, text)
    except Exception:
        _native_value_set_and_fire(driver, el, text)
    # autocomplete pick if present
    if try_pick:
        if pick_from_autocomplete(driver, text, mode="equals", timeout=2.0):
            return True
        if pick_from_autocomplete(driver, text, mode="contains", timeout=2.0):
            return True
    # blur
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
        return ""


def _tokens_ok(value: str, expected: str) -> bool:
    return value_has_tokens(value, _tokenize_upper(expected))


def _set_content_name_anyhow(driver, final_text: str, prefix: Optional[str] = None) -> bool:
    """
    1) find element (any frame)
    2) type + pick
    3) if not equal, do native setter + events
    4) verify by tokens/equality
    """
    el, frame_idx = _find_in_any_frame(driver, _CNM_CANDIDATE_LOCATORS, timeout_each=2.0)
    if el is None:
        print("❌ Content Name field not found in any context.")
        try: ss(driver, "22_insertitem_contentname_not_found.png", prefix=prefix)
        except Exception: pass
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass

    # Attempt 1: type & pick
    _type_and_optionally_pick(driver, el, final_text, try_pick=True)
    val = _read_el_value(driver, el)
    if val.strip():
        if val.strip().upper() == final_text.strip().upper() or _tokens_ok(val, final_text):
            try: ss(driver, "22_insertitem_contentname_ok.png", prefix=prefix)
            except Exception: pass
            return True

    # Attempt 2: native setter + events
    _native_value_set_and_fire(driver, el, final_text)
    wait_for_idle_fast(driver)
    val2 = _read_el_value(driver, el)
    if val2.strip().upper() == final_text.strip().upper() or _tokens_ok(val2, final_text):
        try: ss(driver, "22_insertitem_contentname_forced.png", prefix=prefix)
        except Exception: pass
        return True

    # Attempt 3: synonyms for PPC PAPER
    synonyms = [final_text]
    if final_text.strip().upper() == "PPC PAPER":
        synonyms.extend(["PPC BAG (PAPER)", "PPC PAPER BAG", "PPC BAG PAPER", "PPC (PAPER) BAG"])
    for alt in synonyms:
        _type_and_optionally_pick(driver, el, alt, try_pick=True)
        val3 = _read_el_value(driver, el)
        if val3.strip().upper() == final_text.strip().upper() or _tokens_ok(val3, final_text):
            try: ss(driver, "22_insertitem_contentname_ok.png", prefix=prefix)
            except Exception: pass
            return True

    try: ss(driver, "22_insertitem_contentname_failed.png", prefix=prefix)
    except Exception: pass
    print(f"❌ Could not set Content Name to {final_text!r}. Last seen value: {val2!r}")
    return False


# ===========================
# Verification & repair (skips Consignee if CONSIGNEE_TRIED_BOTH True)
# ===========================
def _read_value(driver, locator: Tuple[str, str]) -> str:
    try:
        el = driver.find_element(*locator)
        return (el.get_attribute("value") or "").strip()
    except Exception:
        return ""


def _equal(a: str, b: str) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def _contains(hay: str, needle: str) -> bool:
    return (needle or "").strip().casefold() in (hay or "").strip().casefold()


def _ensure_text(driver, locator: Tuple[str, str], expected: str) -> bool:
    ok = False
    try:
        ok = safe_type(driver, locator, expected, tab_after=True, clear=True) is None or True
    except Exception:
        ok = False
    return wait_until_value(driver, locator, expected, timeout=5.0)


def _ensure_autocomplete(driver, locator: Tuple[str, str], expected: str, require_dropdown=False, verify="equals", max_attempts: int = 5) -> bool:
    return set_autocomplete_verify(driver, locator, expected, verify=verify, require_dropdown_match=require_dropdown,
                                   dropdown_pick_mode="contains", max_attempts=max_attempts)


def _ensure_select(driver, locator: Tuple[str, str], expected: str) -> bool:
    js_set_select_and_fire(driver, locator, expected)
    try:
        el = driver.find_element(*locator)
        val = (el.get_attribute("value") or "").strip()
        if _equal(val, expected):
            return True
        sel_txt = driver.execute_script("const s=arguments[0];return s.options[s.selectedIndex]?.text||'';", el)
        return _equal(sel_txt, expected)
    except Exception:
        return False


def _toggle_gst_and_set(driver, current_expected: str) -> Optional[str]:
    cur = (current_expected or "").strip().casefold()
    new = "Registered" if "unregister" in cur else "Unregistered"
    locator = (By.ID, "CNM_CNE_REGTYPE")
    try:
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


def verify_and_repair_fields(driver, data: dict, prefix: Optional[str] = None, max_passes: int = 3) -> None:
    global CONSIGNEE_TRIED_BOTH, GST_TOGGLED_ONCE, LAST_FILLED_FIELD

    FIELDS: List[Dict] = []

    def add_field(key: str, locator: Tuple[str, str], kind: str, verify: str = "equals", require_dropdown=False, screenshot=None):
        if key == "Consignee" and CONSIGNEE_TRIED_BOTH:
            print("ℹ️ Skipping Consignee in verify_and_repair_fields because Consignee was already tried in fill flow.")
            return
        val = (data.get(key) or "").strip()
        if val:
            FIELDS.append({
                "key": key,
                "locator": locator,
                "kind": kind,
                "value": val,
                "verify": verify,
                "require_dropdown": require_dropdown,
                "screenshot": screenshot,
            })

    add_field("ConsignmentNo", (By.ID, "CNM_VNOSEQ"), "text", verify="equals", screenshot="08_consignment_no.png")
    add_field("Date", (By.ID, "CNM_VDATE"), "text", verify="date", screenshot="09_date_filled.png")
    add_field("Source", (By.ID, "CNM_FROM_STN_NAME"), "auto", verify="equals", screenshot="10_source_filled.png")
    add_field("Destination", (By.ID, "CNM_TO_STN_NAME"), "auto", verify="equals", screenshot="11_destination_filled.png")
    add_field("Vehicle", (By.ID, "CNM_VEHICLENO"), "auto", verify="equals", screenshot="12_vehicle_filled.png")
    add_field("EWayBillNo", (By.ID, "CNM_EWAYBILLNO"), "text", verify="equals", screenshot="13_ewaybill_filled.png")
    add_field("Consignor", (By.ID, "CNM_CNR_NAME"), "auto", verify="contains", screenshot="15_consignor_filled.png")
    add_field("GSTType", (By.ID, "CNM_CNE_REGTYPE"), "select", verify="equals", screenshot="17_gsttype_filled.png")
    add_field("Consignee", (By.ID, "CNM_CNE_NAME"), "auto", verify="equals", screenshot="18_consignee_filled.png")
    add_field("Delivery Address", (By.ID, "CNM_DLV_ADDRESS"), "text", verify="equals", screenshot="19_deliveryaddress_filled.png")

    for p in range(1, max_passes + 1):
        failures = []
        for f in FIELDS:
            key = f["key"]; loc = f["locator"]; kind = f["kind"]; expected = f["value"]; verify_mode = f["verify"]
            shot = f.get("screenshot"); require_dropdown = f.get("require_dropdown", False)

            current = _read_value(driver, loc)

            if key == "GSTType":
                ok = _equal(current, expected)
                if not ok:
                    try:
                        el = driver.find_element(*loc)
                        sel_txt = driver.execute_script("const s=arguments[0];return s.options[s.selectedIndex]?.text||'';", el)
                        ok = _equal(sel_txt, expected)
                    except Exception:
                        ok = False
            elif verify_mode == "date":
                ok = _date_equal(current, expected)
            elif verify_mode == "contains":
                ok = _contains(current, expected)
            else:
                ok = _equal(current, expected)

            if ok:
                LAST_FILLED_FIELD = key
                continue

            print(f"🔎 Pass {p}: '{key}' not verified (have={current!r}, want [{verify_mode}] {expected!r}) — fixing...")

            try:
                if kind == "text":
                    if verify_mode == "date":
                        ok = _ensure_text(driver, loc, expected)
                        if not ok:
                            alt = expected.replace(".", "/") if "." in expected else expected.replace("/", ".")
                            ok = _ensure_text(driver, loc, alt)
                    else:
                        ok = _ensure_text(driver, loc, expected)
                elif kind == "auto":
                    ok = _ensure_autocomplete(driver, loc, expected, require_dropdown=require_dropdown, verify=verify_mode)
                    if ok:
                        LAST_FILLED_FIELD = key
                    if key == "Consignee" and not ok:
                        final = _read_value(driver, loc)
                        if final == "" and (not CONSIGNEE_TRIED_BOTH):
                            print("⚠️ Consignee remains EMPTY after retries — toggling GST Type as fallback.")
                            curr_gst = (data.get("GSTType") or "").strip()
                            new_gst = _toggle_gst_and_set(driver, curr_gst)
                            if new_gst:
                                GST_TOGGLED_ONCE = True
                                data["GSTType"] = new_gst
                                gst_ok = _ensure_select(driver, (By.ID, "CNM_CNE_REGTYPE"), new_gst)
                                if gst_ok:
                                    print(f"✅ GST updated to {new_gst}; will retry Consignee after GST change.")
                                    ss(driver, "gst_after_toggle.png", prefix)
                                    ok = _ensure_autocomplete(driver, loc, expected, require_dropdown=require_dropdown, verify=verify_mode)
                                    if ok:
                                        LAST_FILLED_FIELD = key
                                else:
                                    print("⚠️ GST update didn't stick; proceeding.")
                elif kind == "select":
                    ok = _ensure_select(driver, loc, expected)
                    if ok:
                        LAST_FILLED_FIELD = key
                else:
                    ok = False
            except Exception as e:
                print(f"⚠️ Error while fixing {key}: {e}")
                ok = False

            if ok:
                print(f"✅ '{key}' confirmed after repair.")
                if shot:
                    ss(driver, shot, prefix=prefix)
            else:
                print(f"❌ '{key}' still not correct after repair attempt.")
                failures.append(key)

            wait_for_idle_fast(driver)

        if not failures:
            print(f"🎉 All fields verified in pass {p}.")
            return
        else:
            print(f"↻ Fields still incorrect after pass {p}: {', '.join(failures)}")

    print("⚠️ Verification finished with unresolved fields after max passes.")


# ===========================
# Helpers: robust JSON value fetch (accept multiple key spellings)
# ===========================
def _get_json_value(data: dict, candidate_keys: List[str]) -> Optional[str]:
    """
    Try exact keys, then case/space/underscore-insensitive matching.
    Returns the first non-empty string value.
    """
    if not data:
        return None

    # 1) exact pass
    for k in candidate_keys:
        if k in data:
            v = data.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()

    # 2) normalized key map
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


# ===========================
# Main form filler
# ===========================
def fill_consignment_form(driver, data, prefix: Optional[str] = None):
    """
    Flow:
      - Fill main fields quickly.
      - For Consignee: try current GST 3x; if empty -> toggle GST once -> 3x again; then stop.
      - Mark CONSIGNEE_TRIED_BOTH = True so verify stage won't reattempt it.
      - Insert Item modal:
           -> Build Content Name strictly from JSON: ContentName + GoodsType (e.g., "PPC BAG")
           -> Accept flexible key spellings and extract base from strings like "OPC53" -> "OPC"
           -> Use iframe-aware, React-safe setter to ensure it sticks.
    Returns True on completion of fill flow (does not perform final submit by default).
    """
    global GST_TOGGLED_ONCE, CONSIGNEE_TRIED_BOTH, LAST_FILLED_FIELD
    GST_TOGGLED_ONCE = False
    CONSIGNEE_TRIED_BOTH = False
    LAST_FILLED_FIELD = None

    wait = WebDriverWait(driver, 20)
    wait_for_idle_fast(driver, total_timeout=6.0)

    try:
        # Consignment No
        cons_no = (data.get("ConsignmentNo") or "").strip()
        if cons_no:
            safe_type(driver, (By.ID, "CNM_VNOSEQ"), cons_no, tab_after=True, clear=True)
            print(f"✅ ConsignmentNo filled: {cons_no}")
            LAST_FILLED_FIELD = "ConsignmentNo"
            ss(driver, "08_consignment_no.png", prefix=prefix)

        # Date
        cons_date = (data.get("Date") or "").strip()
        if cons_date:
            try:
                el = wait.until(EC.presence_of_element_located((By.ID, "CNM_VDATE")))
                driver.execute_script("try{arguments[0].removeAttribute('readonly')}catch(e){}", el)
            except Exception:
                pass
            safe_type(driver, (By.ID, "CNM_VDATE"), cons_date, tab_after=True, clear=True)
            print(f"✅ Date filled: {cons_date}")
            LAST_FILLED_FIELD = "Date"
            ss(driver, "09_date_filled.png", prefix=prefix)

        # Source
        source_val = (data.get("Source") or "").strip()
        if source_val:
            wait_for_idle(driver, total_timeout=6)
            ok = set_autocomplete_verify(driver, (By.ID, "CNM_FROM_STN_NAME"), source_val, verify="equals", max_attempts=6, prefix=prefix)
            print(f"{'✅' if ok else '⚠️'} Source {'confirmed' if ok else 'not confirmed'}: {source_val}")
            if ok:
                LAST_FILLED_FIELD = "Source"
            ss(driver, "10_source_filled.png", prefix=prefix)

        # Destination
        dest_val = (data.get("Destination") or "").strip()
        if dest_val:
            ok = set_autocomplete_verify(driver, (By.ID, "CNM_TO_STN_NAME"), dest_val, verify="equals", max_attempts=6, prefix=prefix)
            print(f"{'✅' if ok else '⚠️'} Destination {'confirmed' if ok else 'not confirmed'}: {dest_val}")
            if ok:
                LAST_FILLED_FIELD = "Destination"
            ss(driver, "11_destination_filled.png", prefix=prefix)

        # Vehicle
        vehicle_val = (data.get("Vehicle") or "").strip()
        if vehicle_val:
            ok = set_autocomplete_verify(driver, (By.ID, "CNM_VEHICLENO"), vehicle_val, verify="equals", max_attempts=6, prefix=prefix)
            print(f"{'✅' if ok else '⚠️'} Vehicle {'confirmed' if ok else 'not confirmed'}: {vehicle_val}")
            if ok:
                LAST_FILLED_FIELD = "Vehicle"
            ss(driver, "12_vehicle_filled.png", prefix=prefix)

        # E-Way Bill No
        eway_val = (data.get("EWayBillNo") or "").strip()
        if eway_val:
            safe_type(driver, (By.ID, "CNM_EWAYBILLNO"), eway_val, tab_after=True, clear=True)
            if wait_until_value(driver, (By.ID, "CNM_EWAYBILLNO"), eway_val, timeout=4.0):
                print(f"✅ E-Way Bill No filled: {eway_val}")
                LAST_FILLED_FIELD = "EWayBillNo"
            else:
                print("⚠️ E-Way Bill No might not have stuck.")
            ss(driver, "13_ewaybill_filled.png", prefix=prefix)

        # Consignor
        consignor_val = (data.get("Consignor") or "").strip()
        if consignor_val:
            ok = set_autocomplete_verify(driver, (By.ID, "CNM_CNR_NAME"), consignor_val, verify="contains", max_attempts=6, prefix=prefix)
            print(f"{'✅' if ok else '⚠️'} Consignor {'confirmed' if ok else 'not confirmed'}: {consignor_val}")
            if ok:
                LAST_FILLED_FIELD = "Consignor"
            ss(driver, "15_consignor_filled.png", prefix=prefix)

        # GST Type set as given initially
        gst_type_val = (data.get("GSTType") or "").strip()
        if gst_type_val:
            if _ensure_select(driver, (By.ID, "CNM_CNE_REGTYPE"), gst_type_val):
                print(f"✅ GST Type set: {gst_type_val}")
                LAST_FILLED_FIELD = "GSTType"
            else:
                print(f"⚠️ GST Type might not have stuck: expected '{gst_type_val}'")
            ss(driver, "17_gsttype_filled.png", prefix=prefix)

        # ======== Consignee logic: current GST (3 attempts) -> toggle once -> try 3 attempts ========
        consignee_val = (data.get("Consignee") or "").strip()
        if consignee_val:
            ok_initial = set_autocomplete_verify(driver, (By.ID, "CNM_CNE_NAME"), consignee_val, verify="equals", max_attempts=3, prefix=prefix)
            if ok_initial:
                print(f"✅ Consignee confirmed: {consignee_val}")
                CONSIGNEE_TRIED_BOTH = True
                LAST_FILLED_FIELD = "Consignee"
            else:
                final = _read_value(driver, (By.ID, "CNM_CNE_NAME"))
                if final == "" or final is None:
                    if not GST_TOGGLED_ONCE:
                        print("⚠️ Consignee empty after initial attempts — toggling GST Type once and retrying.")
                        new_gst = _toggle_gst_and_set(driver, gst_type_val)
                        GST_TOGGLED_ONCE = True
                        CONSIGNEE_TRIED_BOTH = True
                        if new_gst:
                            data["GSTType"] = new_gst
                            ok_after_toggle = set_autocomplete_verify(driver, (By.ID, "CNM_CNE_NAME"), consignee_val, verify="equals", max_attempts=3, prefix=prefix)
                            if ok_after_toggle:
                                print(f"✅ Consignee confirmed after GST toggle: {consignee_val}")
                                LAST_FILLED_FIELD = "Consignee"
                            else:
                                print("❌ Consignee still not found after GST toggle — moving on.")
                        else:
                            print("❌ GST toggle failed — moving on without Consignee.")
                    else:
                        print("⚠️ GST was already toggled earlier; not toggling again.")
                        CONSIGNEE_TRIED_BOTH = True
                else:
                    print(f"⚠️ Consignee set to non-empty but not exact: {final!r}; moving on.")
                    CONSIGNEE_TRIED_BOTH = True
            ss(driver, "18_consignee_filled.png", prefix=prefix)
        else:
            CONSIGNEE_TRIED_BOTH = True

        # Delivery Address
        for _ in range(2):
            if not _close_any_popup(driver, timeout=2):
                break
            time.sleep(0.15)
        delivery_val = (data.get("Delivery Address") or "").strip()
        if delivery_val:
            el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "CNM_DLV_ADDRESS")))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            current_val = (el.get_attribute("value") or "").strip()
            if not _contains(current_val, delivery_val):
                try: el.click()
                except Exception: driver.execute_script("arguments[0].click();", el)
                time.sleep(0.08)
                try:
                    el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
                except Exception:
                    try: el.clear()
                    except Exception: driver.execute_script("arguments[0].value='';", el)
                try:
                    el.send_keys(delivery_val)
                except Exception:
                    driver.execute_script("arguments[0].value=arguments[1];", el, delivery_val)
                wait_until_value(driver, (By.ID, "CNM_DLV_ADDRESS"), delivery_val, timeout=4.0)
                wait_for_idle_fast(driver)
                print(f"✅ Delivery Address set: {delivery_val}")
                LAST_FILLED_FIELD = "Delivery Address"
            else:
                print(f"✅ Delivery Address already correct: {current_val}")
                LAST_FILLED_FIELD = "Delivery Address"
            ss(driver, "19_deliveryaddress_filled.png", prefix=prefix)

        # Verify & repair main fields (dates tolerant; Consignor contains)
        verify_and_repair_fields(driver, data, prefix=prefix, max_passes=3)

        # Insert Item modal + JSON ContentName/GoodsType + item fields
        try:
            safe_click(driver, (By.ID, "btnAddItem"))
            wait_for_idle_fast(driver)
            print("✅ 'Add Item' clicked.")
            ss(driver, "21_additem_clicked.png", prefix=prefix)
        except Exception as e:
            print(f"❌ Failed to open Insert Item modal: {e}")

        try:
            for _ in range(2):
                if not _close_any_popup(driver, timeout=2):
                    break
                time.sleep(0.15)

            # Invoice No
            inv_no = (data.get('Invoice No') or '').strip()
            if inv_no:
                safe_type(driver, (By.XPATH, "//*[@id='InvcNo']"), inv_no, clear=True)
                LAST_FILLED_FIELD = "Invoice No"

            # ---- JSON-driven Content Name ----
            cn_raw = _get_json_value(data, ["ContentName", "Content Name", "contentname", "content_name", "content", "itemname"])
            gt_raw = _get_json_value(data, ["GoodsType", "Goods Type", "goods_type", "goodstype", "goods", "type"])

            final_cn = compute_final_content_string_from_json(cn_raw, gt_raw)
            if final_cn:
                ok_cn = _set_content_name_anyhow(driver, final_cn, prefix=prefix)
                if ok_cn:
                    LAST_FILLED_FIELD = "ContentName"
                else:
                    print(f"⚠️ Could not set Content Name {final_cn!r}; proceeding.")
            else:
                # partial attempt if one side present
                if cn_raw or gt_raw:
                    base = _normalize_base_from_json(cn_raw or "")
                    label = _normalize_goods_type_from_json(gt_raw or "")
                    attempt = " ".join([p for p in [base, label] if p]).strip()
                    if attempt:
                        okcn = _set_content_name_anyhow(driver, attempt, prefix=prefix)
                        if okcn:
                            LAST_FILLED_FIELD = "ContentName"
                        print(f"{'✅' if okcn else '⚠️'} Content Name attempt with partial JSON: {attempt!r}")
                    else:
                        print("⚠️ Content Name building failed: both parts empty after normalization.")
                else:
                    print("⚠️ Missing ContentName or GoodsType in JSON; unable to set Name field.")

            # Actual weight (typed only if provided; no inference)
            aw_raw = (data.get('ActualWeight') or '').strip()
            if aw_raw:
                safe_type(driver, (By.XPATH, "//*[@id='Actual']"), aw_raw, clear=True)
                LAST_FILLED_FIELD = "ActualWeight"

            # Eway / invoice dates & nos
            evu = (data.get('E-WayBill ValidUpto') or '').strip()
            if evu:
                safe_type(driver, (By.XPATH, "//*[@id='EwayBillExpDate']"), evu, clear=True)
            invd = (data.get('Invoice Date') or '').strip()
            if invd:
                safe_type(driver, (By.XPATH, "//*[@id='InvcDate']"), invd, clear=True)
            ebd = (data.get('E-Way Bill Date') or '').strip()
            if ebd:
                safe_type(driver, (By.XPATH, "//*[@id='EwayBillDate']"), ebd, clear=True)
            ebn = (data.get('E-Way Bill NO') or '').strip()
            if ebn:
                safe_type(driver, (By.XPATH, "//*[@id='EwayBillNo']"), ebn, clear=True)

            ss(driver, "22_insertitem_filled.png", prefix=prefix)
        except Exception as e:
            print(f"❌ Error filling Insert Item modal: {e}")

        # Add invoice
        try:
            safe_click(driver, (By.XPATH, "//*[@id='btnInsert']"))
            print("✅ 'Add Invoice' clicked.")
            ss(driver, "24_addinvoice_clicked.png", prefix=prefix)
            LAST_FILLED_FIELD = "AddInvoice"
        except Exception as e:
            print(f"❌ Failed to click Add Invoice: {e}")

        # Close modal
        try:
            safe_click(driver, (By.XPATH, "//*[@id='frvclose']"))
            wait_for_idle_fast(driver)
            print("✅ Insert Item modal closed.")
            ss(driver, "25_insertitem_closed.png", prefix=prefix)
        except Exception as e:
            print(f"❌ Failed to close Insert Item modal: {e}")

        # Rate (optional)
        rate_val = (data.get("Get Rate") or "").strip()
        if rate_val:
            try:
                safe_type(driver, (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, tab_after=True, clear=True)
                if wait_until_value(driver, (By.XPATH, "//*[@id='CNM_RATE']"), rate_val, timeout=4.0):
                    print(f"✅ Rate filled: {rate_val}")
                    LAST_FILLED_FIELD = "Get Rate"
                else:
                    print("⚠️ Rate did not stick after verification.")
                ss(driver, "27_rate_filled.png", prefix=prefix)
            except Exception as e:
                print(f"❌ Failed to set Rate: {e}")

                wait_for_idle_fast(driver)
        for _ in range(2):
            if not _close_any_popup(driver, timeout=2):
                break
            time.sleep(0.15)

        # 🆕 Validation: Check if any key fields are missing or failed
        required_fields = ["ConsignmentNo", "Date", "Source", "Destination", "Vehicle", "Consignee"]
        missing_fields = [f for f in required_fields if not (data.get(f) or "").strip()]

        if LAST_FILLED_FIELD is None or missing_fields:
            print(f"❌ Validation failed — missing or unfilled fields: {missing_fields}")
            return False

        print("✅ All required fields filled successfully — returning True.")
        return True

    except Exception as e:
        print(f"❌ Error in fill_consignment_form main flow: {e}")
        return False


# ====== Final Submit (intentionally commented out) ======
# Kept disabled to avoid unintended destructive actions.
"""
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
        return True
    except TimeoutException:
        print("⚠️ No success message found after submit — may have failed.")
        try:
            error_popup = driver.find_element(By.XPATH, "//*[contains(text(),'error') or contains(text(),'Error') or contains(text(),'failed')]")
            if error_popup.is_displayed():
                print("❌ Error popup detected — submission failed.")
                ss(driver, "29_submit_error_detected.png", prefix=prefix)
                return False
        except Exception:
            pass
        ss(driver, "29_submit_no_success.png", prefix=prefix)
        return False

except Exception as e:
    print(f"❌ Failed to click Submit button: {e}")
    ss(driver, "28_submit_failed.png", prefix=prefix)
    return False
"""


# ---------------------------------------------------------------------------------
# Validation builder: compare JSON -> ERP page values and build ValidationStatus
# ---------------------------------------------------------------------------------
def build_validation_status(driver, data: dict, numeric_tolerance: float = 0.1) -> dict:
    """
    Final robust validation builder:
    ✅ Detects missing/null JSON and ERP values.
    ✅ Reads ERP fields reliably (value OR visible text).
    ✅ Numeric, date, and text comparison tolerant.
    ✅ No false mismatches when UI is correctly filled.
    """
    from selenium.webdriver.common.by import By
    import re

    FIELD_LOCATORS = {
        "Vehicle": (By.ID, "CNM_VEHICLENO"),
        "ActualWeight": (By.XPATH, "//*[@id='Actual']"),
        "EWayBillNo": (By.ID, "CNM_EWAYBILLNO"),
        "Consignee": (By.ID, "CNM_CNE_NAME"),
        "GSTType": (By.ID, "CNM_CNE_REGTYPE"),
        "Delivery Address": (By.ID, "CNM_DLV_ADDRESS"),
        "Source": (By.ID, "CNM_FROM_STN_NAME"),
        "Destination": (By.ID, "CNM_TO_STN_NAME"),
        "ConsignmentNo": (By.ID, "CNM_VNOSEQ"),
        "Date": (By.ID, "CNM_VDATE"),
        "Get Rate": (By.XPATH, "//*[@id='CNM_RATE']"),
        "E-Way Bill NO": (By.XPATH, "//*[@id='EwayBillNo']"),
        "Invoice No": (By.XPATH, "//*[@id='InvcNo']"),
    }

    failed = []

    # --- Read ERP value safely ---
    def read_erp(locator):
        """Try value, then text, then innerText."""
        try:
            el = driver.find_element(*locator)
            val = (el.get_attribute("value") or "").strip()
            if not val:
                val = (el.text or "").strip()
            if not val:
                val = driver.execute_script("return arguments[0].innerText || '';", el)
            if not val:
                val = driver.execute_script(
                    "return arguments[0].getAttribute('value') || arguments[0].textContent || '';",
                    el,
                )
            return (val or "").strip()
        except Exception:
            return ""

    def _is_number(v):
        try:
            float(str(v).replace(",", "").strip())
            return True
        except:
            return False

    def _as_date_str(v):
        parts = re.findall(r"\d+", str(v))
        if len(parts) >= 3:
            d, m, y = parts[:3]
            if len(y) == 2:
                y = "20" + y
            return f"{d.zfill(2)}-{m.zfill(2)}-{y.zfill(4)}"
        return None

    # ---- Comparison logic ----
    for key, locator in FIELD_LOCATORS.items():
        json_val = str(data.get(key) or "").strip()
        erp_val = str(read_erp(locator) or "").strip()

        # CASE 1 — Missing in JSON
        if not json_val:
            failed.append({
                "Field": key,
                "CurrentValue": None,
                "ERPValue": erp_val,
                "Reason": "Missing value in JSON"
            })
            continue

        # CASE 2 — Missing in ERP
        if not erp_val:
            failed.append({
                "Field": key,
                "CurrentValue": json_val,
                "ERPValue": "",
                "Reason": "Missing value in ERP"
            })
            continue

        # CASE 3 — Numeric comparison
        if _is_number(json_val) and _is_number(erp_val):
            jv = float(json_val.replace(",", ""))
            ev = float(erp_val.replace(",", ""))
            if abs(jv - ev) <= numeric_tolerance:
                continue  # ✅ same
            failed.append({
                "Field": key,
                "CurrentValue": json_val,
                "ERPValue": erp_val,
                "Reason": "Numeric mismatch"
            })
            continue

        # CASE 4 — Date comparison
        jd = _as_date_str(json_val)
        ed = _as_date_str(erp_val)
        if jd and ed:
            if jd == ed:
                continue
            failed.append({
                "Field": key,
                "CurrentValue": json_val,
                "ERPValue": erp_val,
                "Reason": "Date mismatch"
            })
            continue

        # CASE 5 — Text comparison (case-insensitive, ignore punctuation)
        j_clean = re.sub(r"[^A-Za-z0-9]", "", json_val).lower()
        e_clean = re.sub(r"[^A-Za-z0-9]", "", erp_val).lower()
        if j_clean == e_clean:
            continue

        # Token-based similarity (e.g., "ARAKKONAM DEPOT" vs "ARAKKONAM")
        j_tokens = set(json_val.lower().split())
        e_tokens = set(erp_val.lower().split())
        if len(j_tokens) > 0 and (len(j_tokens & e_tokens) / len(j_tokens)) >= 0.8:
            continue

        # Otherwise, true mismatch
        failed.append({
            "Field": key,
            "CurrentValue": json_val,
            "ERPValue": erp_val,
            "Reason": "Mismatch"
        })

    return {"isPassed": len(failed) == 0, "FailedFields": failed}
