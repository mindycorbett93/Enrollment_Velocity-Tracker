import requests
import datetime
import sqlite3
import keyring
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from pydantic import BaseModel, Field

# --- AUTHENTICATION LAYER: Secure Credential Management ---
class PECOSCredentials:
    def __init__(self, service_id="CMS_IA_SYSTEM"):
        self.service_id = service_id

    def set_credentials(self, username, password):
        """Saves credentials to the system's secure vault (S_S14)."""
        keyring.set_password(self.service_id, username, password)
        keyring.set_password(self.service_id, f"{username}_account", username)

    def get_credentials(self, username):
        """Retrieves credentials securely at runtime (S_S15)."""
        pw = keyring.get_password(self.service_id, username)
        if not pw:
            raise ValueError(f"Credentials not found in vault for: {username}")
        return username, pw

# --- TRACKER ENGINE ---
class EnrollmentVelocityTracker:
    def __init__(self, db_path="enrollment_cache.db"):
        self.db_path = db_path
        self.benchmark_days = 90
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enrollment_audit (
                    npi TEXT PRIMARY KEY,
                    nppes_effective_date DATE,
                    pecos_status TEXT,
                    last_sync DATE,
                    days_in_pipeline INTEGER
                )
            """)

    def sync_nppes(self, npi: str):
        """Pulls Enumeration Date from NPPES API (S_R20)."""
        api_url = f"https://npiregistry.cms.hhs.gov/api/?version=2.1&number={npi}"
        resp = requests.get(api_url).json()
        if resp.get('results'):
            return resp['results']['basic']['enumeration_date']
        return None

    def sync_pecos_status(self, npi: str, username: str):
        """Automates PECOS portal navigation to extract enrollment status (S_R4, S_R5)."""
        user, password = PECOSCredentials().get_credentials(username)
        
        options = webdriver.ChromeOptions()
        options.add_argument('--headless') # Run in background
        driver = webdriver.Chrome(options=options)
        
        try:
            driver.get("https://pecos.cms.hhs.gov/pecos/login.do")
            
            # 1. Authenticate via I&A System (S_S18)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "userId"))).send_keys(user)
            driver.find_element(By.ID, "password").send_keys(password)
            driver.find_element(By.ID, "loginBtn").click()
            
            # 2. Navigate to 'My Enrollments' (S_R4)
            WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'My Enrollments')]"))).click()
            
            # 3. Locate provider and capture status (S_R5)
            # This logic searches the 'Existing Associates' table for the specific NPI
            status_element = driver.find_element(By.XPATH, f"//tr[contains(., '{npi}')]//td[@class='status-col']")
            return status_element.text # e.g., 'Approved', 'Pending', 'Voluntary Withdraw'
            
        except Exception as e:
            return f"Portal Sync Error: {str(e)}"
        finally:
            driver.quit()

    def update_velocity_score(self, npi: str, hire_date_str: str, pecos_user: str):
        """Calculates hire-to-billable delta and flags 90-day risks [Image 6]."""
        hire_date = datetime.datetime.strptime(hire_date_str, "%Y-%m-%d").date()
        nppes_date = self.sync_nppes(npi)
        pecos_status = self.sync_pecos_status(npi, pecos_user)
        
        today = datetime.date.today()
        days_in_pipe = (today - hire_date).days
        
        # Benchmark Logic: Critical flag if pending status exceeds 90 days [Image 4]
        is_critical = days_in_pipe >= self.benchmark_days and pecos_status!= "Approved"
        
        result = {
            "NPI": npi,
            "PECOS_Status": "CRITICAL DELAY" if is_critical else pecos_status,
            "Pipeline_Days": days_in_pipe,
            "Benchmark_Met": "No" if is_critical else "Yes"
        }
        
        self._cache_result(result, nppes_date)
        return result

    def _cache_result(self, data, nppes_date):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO enrollment_audit 
                (npi, nppes_effective_date, pecos_status, last_sync, days_in_pipeline)
                VALUES (?,?,?,?,?)
            """, (data['NPI'], nppes_date, data, datetime.date.today(), data))
