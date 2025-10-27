# consignment_page.py
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from driver_utils import ss, click_js

def open_consignment_page(driver):
    wait = WebDriverWait(driver, 20)

    # ---------------- Booking Operation ----------------
    try:
        click_js(driver, wait.until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='side-menu']/li[2]/a"))
        ))
    except:
        click_js(driver, wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(),'Booking Operation')]/ancestor::a"))
        ))
    ss(driver, "05_booking_operation_expanded.png")
    print("✅ Booking Operation expanded")

    # ---------------- Consignment ----------------
    try:
        click_js(driver, wait.until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='side-menu']/li[2]/ul/li/a"))
        ))
    except:
        click_js(driver, wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Consignment']/ancestor::a"))
        ))
    ss(driver, "06_consignment_clicked.png")
    print("✅ Consignment clicked")

    # ---------------- API ----------------
    try:
        click_js(driver, wait.until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='side-menu']/li[2]/ul/li/ul/li/a"))
        ))
    except:
        click_js(driver, wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='API']/ancestor::a"))
        ))
    print("✅ API clicked; checking for iframe...")

    # ---------------- Switch into iframe ----------------
    WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located((By.TAG_NAME, "iframe"))
    )
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    print("🔍 Found iframes:", len(iframes))

    driver.switch_to.frame(iframes[0])
    print("🔄 Switched into iframe")

    # ---------------- Ensure form is ready ----------------
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "CNM_VNOSEQ"))
    )
    ss(driver, "07_consignment_form_ready.png")
    print("🎯 Consignment form is open and ready inside iframe")

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "CNM_AGAINSTDATE"))
    )
    ss(driver, "08_date_field_ready.png")
    print("📅 Date field is also ready")
