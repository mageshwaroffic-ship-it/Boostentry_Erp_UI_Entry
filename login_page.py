from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep
from config import BASE_URL, USERNAME, PASSWORD
from driver_utils import ss, click_js

def maybe_handle_already_logged_in_popup(driver):
    try:
        WebDriverWait(driver, 4).until(
            EC.any_of(
                EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='OK']")),
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".swal2-confirm"))
            )
        )
        try:
            driver.find_element(By.XPATH, "//button[normalize-space()='OK']").click()
        except:
            driver.find_element(By.CSS_SELECTOR, ".swal2-confirm").click()
        print("⚠️ Dismissed 'User Already Login' popup.")
        sleep(0.7)
        return True
    except:
        return False

def login(driver):
    wait = WebDriverWait(driver, 20)
    driver.get(BASE_URL)
    ss(driver, "00_login_page.png")

    wait.until(EC.presence_of_element_located((By.ID, "UserName"))).clear()
    driver.find_element(By.ID, "UserName").send_keys(USERNAME)
    driver.find_element(By.ID, "Password").clear()
    driver.find_element(By.ID, "Password").send_keys(PASSWORD)
    ss(driver, "01_credentials_typed.png")

    click_js(driver, wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit"))))
    print("✅ Clicked Sign in")

    if maybe_handle_already_logged_in_popup(driver):
        driver.find_element(By.ID, "UserName").clear()
        driver.find_element(By.ID, "UserName").send_keys(USERNAME)
        driver.find_element(By.ID, "Password").clear()
        driver.find_element(By.ID, "Password").send_keys(PASSWORD)
        click_js(driver, wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit"))))
        print("🔁 Retried Sign in after popup")
