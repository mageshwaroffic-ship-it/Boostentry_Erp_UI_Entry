from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoAlertPresentException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    WebDriverException,
)
from driver_utils import ss, click_js
import time

def handle_swal2_or_alert(driver, timeout=2, screenshot_name=None):
    """
    Try to detect and close a SweetAlert2 modal (OK button) or a JS alert.
    Returns True if a popup was handled, False otherwise.
    """
    wait = WebDriverWait(driver, timeout)
    try:
        ok_selector = (By.CSS_SELECTOR, "button.swal2-confirm.swal2-styled")
        ok_btn = wait.until(EC.element_to_be_clickable(ok_selector))
        try:
            click_js(driver, ok_btn)
        except (StaleElementReferenceException, ElementClickInterceptedException, WebDriverException):
            try:
                ok_btn = driver.find_element(*ok_selector)
                ok_btn.click()
            except Exception:
                pass
        try:
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.swal2-container, div.swal2-popup")))
        except TimeoutException:
            time.sleep(0.5)
        if screenshot_name:
            ss(driver, screenshot_name)
        return True
    except TimeoutException:
        pass
    except Exception:
        pass

    try:
        alert = driver.switch_to.alert
        alert.accept()
        if screenshot_name:
            ss(driver, screenshot_name)
        return True
    except NoAlertPresentException:
        return False
    except WebDriverException:
        return False

def click_submit_and_handle(driver, submit_locator, wait, popup_timeout=2, max_attempts=3):
    """
    Click the submit button (using click_js) and handle any popups that appear.
    """
    for attempt in range(1, max_attempts + 1):
        handle_swal2_or_alert(driver, timeout=popup_timeout, screenshot_name=f"popup_before_submit_attempt_{attempt}.png")

        try:
            submit_btn = wait.until(EC.element_to_be_clickable(submit_locator))
            click_js(driver, submit_btn)
        except (TimeoutException, ElementClickInterceptedException, StaleElementReferenceException):
            time.sleep(0.4)
            handle_swal2_or_alert(driver, timeout=popup_timeout, screenshot_name=f"popup_after_click_attempt_{attempt}.png")
            try:
                submit_btn = wait.until(EC.element_to_be_clickable(submit_locator))
                click_js(driver, submit_btn)
            except Exception:
                time.sleep(0.4)

        time.sleep(0.4)
        popup_handled = handle_swal2_or_alert(driver, timeout=popup_timeout, screenshot_name=f"popup_handled_after_submit_attempt_{attempt}.png")
        if popup_handled:
            time.sleep(0.5)
            continue

        return True
    return False

# NEW
def select_branch(driver, branch_name):
    wait = WebDriverWait(driver, 20)
    branch = wait.until(EC.presence_of_element_located((By.ID, "Branch")))
    Select(branch).select_by_visible_text(branch_name)
    print(f"✅ Branch selected: {branch_name}")

    ss(driver, "02_branch_selected.png")

    submit_locator = (By.XPATH, "//button[normalize-space()='Submit']")
    ok = click_submit_and_handle(driver, submit_locator, wait, popup_timeout=2, max_attempts=3)
    if not ok:
        raise RuntimeError("❌ Failed to click Submit for branch selection (popup kept interrupting or button not clickable).")

    print("✅ Branch Submit clicked (handled popups if any)")

    try:
        WebDriverWait(driver, 20).until(EC.url_contains("/Settings/LoadModule"))
        ss(driver, "03_after_branch_submit.png")
    except TimeoutException:
        ss(driver, "03_after_branch_submit_timeout.png")
        raise
