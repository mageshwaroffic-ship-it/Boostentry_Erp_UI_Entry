from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from config import MENU_URL
from driver_utils import ss, click_js
import time

def open_operations(driver):
    wait = WebDriverWait(driver, 5)  # give more time for menu to load fully
    try:
        print("⏳ Waiting for Operations tile...")
        op_img = wait.until(EC.element_to_be_clickable((By.XPATH, "//img[@alt='Operations']")))
        click_js(driver, op_img)
        print("✅ Clicked Operations tile")
        ss(driver, "04_operations_tile_clicked.png")
    except Exception as e:
        print(f"⚠️ Operations tile not found in time ({e}), navigating directly to MENU_URL")
        driver.get(MENU_URL)

    # Ensure the menu page is fully loaded
    for attempt in range(3):
        try:
            WebDriverWait(driver, 10).until(EC.url_contains("/Settings/Menu"))
            ss(driver, f"04_on_menu_page_attempt_{attempt+1}.png")
            print("✅ On Menu page")
            break
        except:
            print("⚠️ Still not on menu page, retrying...")
            driver.get(MENU_URL)
            time.sleep(2)
