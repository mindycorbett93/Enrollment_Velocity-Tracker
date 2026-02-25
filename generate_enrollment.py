"""
Generator for Provider Enrollment test data files.

Produces 12 pipe-delimited .dat files per practice type (180 total)
across 15 specialties, each containing 3-5 provider enrollment records.

File tiers control enrollment quality:
  Files  1-4  : Mostly ACTIVE enrollments, all credentials current
  Files  5-7  : Mix of ACTIVE, PENDING, REVALIDATION_NEEDED
  Files  8-9  : Some EXPIRED or TERMINATED, credential issues
  Files 10-12 : Problem cases -- DENIED, expired licenses, CAQH gaps
"""

import os
import random
import string
from datetime import datetime, timedelta

from generators.test_data_commons import *


# ============================================================
# ENROLLMENT-SPECIFIC CONSTANTS
# ============================================================

ENROLLMENT_STATUSES = [
    "ACTIVE", "PENDING", "DENIED",
    "TERMINATED", "EXPIRED", "REVALIDATION_NEEDED",
]

CAQH_STATUSES = ["ACTIVE", "EXPIRED", "PENDING"]

CREDENTIALING_STATUSES = ["APPROVED", "IN_PROCESS", "EXPIRED", "DENIED"]

CREDENTIALS = ["MD", "DO", "MD", "MD", "DO", "MD FACS", "MD PhD", "DO FACP", "NP", "PA"]

MALPRACTICE_CARRIERS = [
    "The Doctors Company",
    "Medical Protective",
    "ProAssurance",
    "Coverys",
    "NORCAL Group",
    "MagMutual",
    "CMIC Group",
    "Physicians Reciprocal Insurers",
    "ISMIE Mutual",
    "MLMIC Insurance Company",
]

HEADER_ROW = (
    "ENROLLMENT_ID|PROVIDER_NPI|PROVIDER_LAST_NAME|PROVIDER_FIRST_NAME|"
    "CREDENTIAL|TAXONOMY_CODE|SPECIALTY|FACILITY_NAME|FACILITY_NPI|"
    "FACILITY_ADDRESS|CITY|STATE|ZIP|PAYER_NAME|PAYER_ID|"
    "ENROLLMENT_STATUS|ENROLLMENT_DATE|EFFECTIVE_DATE|TERMINATION_DATE|"
    "REVALIDATION_DUE_DATE|CAQH_ID|CAQH_STATUS|MEDICAID_ID|MEDICARE_PTAN|"
    "DEA_NUMBER|DEA_EXPIRATION|STATE_LICENSE|LICENSE_STATE|LICENSE_EXPIRATION|"
    "BOARD_CERTIFIED|BOARD_CERTIFICATION_DATE|MALPRACTICE_CARRIER|"
    "MALPRACTICE_EXPIRATION|CREDENTIALING_STATUS|LAST_UPDATED"
)

# Map each state to a license prefix (used to build realistic license numbers)
STATE_LICENSE_PREFIXES = {
    "NY": "NY-MD-", "CA": "CA-MD-", "IL": "IL-MD-", "TX": "TX-MD-",
    "AZ": "AZ-MD-", "PA": "PA-MD-", "FL": "FL-MD-", "OH": "OH-MD-",
    "NC": "NC-MD-", "IN": "IN-MD-", "WA": "WA-MD-", "CO": "CO-MD-",
    "TN": "TN-MD-", "OR": "OR-MD-",
}


# ============================================================
# HELPER UTILITIES
# ============================================================

def _generate_enrollment_id(practice_idx, file_idx, record_idx):
    """Produce a unique enrollment identifier."""
    return f"ENR{practice_idx:02d}{file_idx:03d}{record_idx:03d}"


def _generate_caqh_id(seed):
    """Produce a CAQH ProView ID (8-digit numeric)."""
    random.seed(seed)
    return f"{random.randint(10000000, 99999999)}"


def _generate_dea_number(last_name, seed):
    """
    Produce a DEA registration number.
    Format: Two-letter prefix + first letter of last name + 6 digits + check digit.
    """
    random.seed(seed)
    prefix = random.choice(["A", "B", "F", "M"])
    mid_digits = f"{random.randint(100000, 999999)}"
    # Simplified check-digit (just use a random digit here for test data)
    check = random.randint(0, 9)
    return f"{prefix}{last_name[0].upper()}{mid_digits}{check}"


def _generate_medicaid_id(state, seed):
    """State Medicaid provider ID or empty."""
    random.seed(seed)
    if random.random() < 0.55:
        return f"{state}-MCD-{random.randint(100000, 999999)}"
    return ""


def _generate_medicare_ptan(seed):
    """Medicare Provider Transaction Access Number or empty."""
    random.seed(seed)
    if random.random() < 0.50:
        chars = "".join(random.choices(string.ascii_uppercase, k=2))
        nums = f"{random.randint(1000, 9999)}"
        return f"{chars}{nums}"
    return ""


def _generate_state_license(state, seed):
    """State medical license number."""
    random.seed(seed)
    prefix = STATE_LICENSE_PREFIXES.get(state, f"{state}-MD-")
    return f"{prefix}{random.randint(100000, 999999)}"


def _pick_facility(practice_type, seed):
    """
    Return a (facility_name, facility_npi, address, city, state, zip) tuple
    using commons data.
    """
    random.seed(seed)
    system = random.choice(list(FACILITY_NAMES.keys()))
    name = random.choice(FACILITY_NAMES[system])
    base_npi = FACILITY_NPI_BASE[system]
    facility_npi = f"{base_npi + random.randint(1, 999)}"
    street_num = random.randint(100, 9999)
    street = random.choice(STREETS)
    city, state, zipcode = random.choice(CITIES_STATES_ZIPS)
    return {
        "name": f"{name} - {PRACTICE_DISPLAY_NAMES[practice_type]}",
        "npi": facility_npi,
        "address": f"{street_num} {street}",
        "city": city,
        "state": state,
        "zip": zipcode,
    }


def _date_str(dt):
    """Format a datetime as YYYY-MM-DD, or empty string for None."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d")


def _datetime_str(dt):
    """Format a datetime as YYYY-MM-DD HH:MM:SS."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# RECORD BUILDER
# ============================================================

def _build_record(
    practice_type,
    practice_idx,
    file_idx,
    record_idx,
    file_tier,
):
    """
    Build a single provider enrollment record dict.

    file_tier determines the "quality" profile:
        1 -> clean / active
        2 -> mixed / some issues
        3 -> some expired / terminated
        4 -> problem cases
    """
    base_seed = practice_idx * 100000 + file_idx * 1000 + record_idx * 10

    # Provider basics --------------------------------------------------------
    provider = get_random_provider(practice_type, base_seed)
    payer = get_random_payer(base_seed + 1)
    facility = _pick_facility(practice_type, base_seed + 2)

    random.seed(base_seed + 3)
    credential = random.choice(CREDENTIALS)

    enrollment_id = _generate_enrollment_id(practice_idx, file_idx, record_idx)

    # Dates ------------------------------------------------------------------
    random.seed(base_seed + 4)
    now = datetime.now()

    enrollment_date = now - timedelta(days=random.randint(180, 1800))
    effective_date = enrollment_date + timedelta(days=random.randint(0, 30))

    # Tier-driven status selection -------------------------------------------
    if file_tier == 1:
        enrollment_status = random.choices(
            ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "PENDING"],
            weights=[40, 30, 20, 5, 5],
        )[0]
    elif file_tier == 2:
        enrollment_status = random.choices(
            ["ACTIVE", "PENDING", "REVALIDATION_NEEDED", "ACTIVE"],
            weights=[35, 30, 25, 10],
        )[0]
    elif file_tier == 3:
        enrollment_status = random.choices(
            ["EXPIRED", "TERMINATED", "ACTIVE", "REVALIDATION_NEEDED"],
            weights=[30, 30, 20, 20],
        )[0]
    else:  # tier 4 -- problem cases
        enrollment_status = random.choices(
            ["DENIED", "EXPIRED", "TERMINATED", "REVALIDATION_NEEDED", "PENDING"],
            weights=[30, 25, 20, 15, 10],
        )[0]

    # Termination date -------------------------------------------------------
    termination_date = None
    if enrollment_status in ("TERMINATED", "EXPIRED"):
        termination_date = now - timedelta(days=random.randint(1, 180))

    # Revalidation due date --------------------------------------------------
    if enrollment_status == "REVALIDATION_NEEDED":
        revalidation_due = now - timedelta(days=random.randint(1, 90))
    elif enrollment_status == "ACTIVE":
        revalidation_due = now + timedelta(days=random.randint(90, 730))
    else:
        revalidation_due = now + timedelta(days=random.randint(-60, 365))

    # CAQH -------------------------------------------------------------------
    caqh_id = _generate_caqh_id(base_seed + 5)
    if file_tier <= 2:
        caqh_status = "ACTIVE"
    elif file_tier == 3:
        caqh_status = random.choice(["ACTIVE", "EXPIRED", "PENDING"])
    else:
        caqh_status = random.choices(
            ["EXPIRED", "PENDING", "ACTIVE"],
            weights=[50, 30, 20],
        )[0]

    # Medicaid / Medicare IDs -----------------------------------------------
    medicaid_id = _generate_medicaid_id(facility["state"], base_seed + 6)
    medicare_ptan = _generate_medicare_ptan(base_seed + 7)

    # DEA --------------------------------------------------------------------
    dea_number = _generate_dea_number(provider["last"], base_seed + 8)
    random.seed(base_seed + 9)
    if file_tier <= 2:
        dea_expiration = now + timedelta(days=random.randint(180, 1095))
    elif file_tier == 3:
        dea_expiration = now + timedelta(days=random.randint(-90, 365))
    else:
        # Problem tier: many have expired DEA
        dea_expiration = now + timedelta(days=random.randint(-365, 90))

    # State License ----------------------------------------------------------
    random.seed(base_seed + 10)
    license_state = facility["state"]
    state_license = _generate_state_license(license_state, base_seed + 11)
    if file_tier <= 2:
        license_expiration = now + timedelta(days=random.randint(180, 1095))
    elif file_tier == 3:
        license_expiration = now + timedelta(days=random.randint(-60, 365))
    else:
        license_expiration = now + timedelta(days=random.randint(-365, 60))

    # Board Certification ----------------------------------------------------
    random.seed(base_seed + 12)
    if file_tier <= 2:
        board_certified = "YES"
        board_cert_date = now - timedelta(days=random.randint(365, 3650))
    elif file_tier == 3:
        board_certified = random.choice(["YES", "YES", "NO"])
        board_cert_date = now - timedelta(days=random.randint(365, 3650)) if board_certified == "YES" else None
    else:
        board_certified = random.choice(["YES", "NO", "NO"])
        board_cert_date = now - timedelta(days=random.randint(365, 3650)) if board_certified == "YES" else None

    # Malpractice Insurance --------------------------------------------------
    random.seed(base_seed + 13)
    malpractice_carrier = random.choice(MALPRACTICE_CARRIERS)
    if file_tier <= 2:
        malpractice_expiration = now + timedelta(days=random.randint(90, 730))
    elif file_tier == 3:
        malpractice_expiration = now + timedelta(days=random.randint(-30, 365))
    else:
        malpractice_expiration = now + timedelta(days=random.randint(-180, 60))

    # Credentialing ----------------------------------------------------------
    random.seed(base_seed + 14)
    if file_tier == 1:
        credentialing_status = "APPROVED"
    elif file_tier == 2:
        credentialing_status = random.choice(["APPROVED", "APPROVED", "IN_PROCESS"])
    elif file_tier == 3:
        credentialing_status = random.choice(["APPROVED", "IN_PROCESS", "EXPIRED"])
    else:
        credentialing_status = random.choices(
            ["DENIED", "EXPIRED", "IN_PROCESS", "APPROVED"],
            weights=[35, 30, 25, 10],
        )[0]

    # Last updated -----------------------------------------------------------
    last_updated = now - timedelta(
        days=random.randint(0, 30),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )

    # Assemble row values in header order -----------------------------------
    values = [
        enrollment_id,
        provider["npi"],
        provider["last"],
        provider["first"],
        credential,
        provider["taxonomy"],
        PRACTICE_DISPLAY_NAMES[practice_type],
        facility["name"],
        facility["npi"],
        facility["address"],
        facility["city"],
        facility["state"],
        facility["zip"],
        payer["name"],
        payer["payer_id"],
        enrollment_status,
        _date_str(enrollment_date),
        _date_str(effective_date),
        _date_str(termination_date),
        _date_str(revalidation_due),
        caqh_id,
        caqh_status,
        medicaid_id,
        medicare_ptan,
        dea_number,
        _date_str(dea_expiration),
        state_license,
        license_state,
        _date_str(license_expiration),
        board_certified,
        _date_str(board_cert_date),
        malpractice_carrier,
        _date_str(malpractice_expiration),
        credentialing_status,
        _datetime_str(last_updated),
    ]

    return "|".join(str(v) for v in values)


# ============================================================
# FILE GENERATOR
# ============================================================

def _file_tier(file_idx):
    """
    Determine quality tier from 1-based file index.
        1-4  -> tier 1  (clean)
        5-7  -> tier 2  (mixed)
        8-9  -> tier 3  (some expired / terminated)
        10-12 -> tier 4  (problem cases)
    """
    if file_idx <= 4:
        return 1
    elif file_idx <= 7:
        return 2
    elif file_idx <= 9:
        return 3
    else:
        return 4


def generate_enrollment_file(practice_type, practice_idx, file_idx):
    """
    Generate a single enrollment .dat file and return the output path.
    """
    tier = _file_tier(file_idx)

    # Decide record count (3-5 providers per file)
    random.seed(practice_idx * 10000 + file_idx * 100)
    num_records = random.randint(3, 5)

    lines = [HEADER_ROW]
    for record_idx in range(num_records):
        row = _build_record(practice_type, practice_idx, file_idx, record_idx, tier)
        lines.append(row)

    # Write file
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "test_data", "enrollment", practice_type,
    )
    os.makedirs(out_dir, exist_ok=True)

    filename = f"enrollment_{practice_type}_{file_idx:03d}.dat"
    filepath = os.path.join(out_dir, filename)
    with open(filepath, "w", newline="") as fh:
        fh.write("\n".join(lines) + "\n")

    return filepath


# ============================================================
# MAIN
# ============================================================

def main():
    """Generate all 180 provider enrollment test data files."""
    total_files = 0
    total_records = 0

    print("=" * 70)
    print("Provider Enrollment Test Data Generator")
    print("=" * 70)

    for practice_idx, practice_type in enumerate(PRACTICE_TYPES):
        practice_files = 0
        practice_records = 0

        for file_idx in range(1, 13):  # 1..12 inclusive
            filepath = generate_enrollment_file(practice_type, practice_idx, file_idx)

            # Count records (lines minus header)
            with open(filepath, "r") as fh:
                record_count = sum(1 for _ in fh) - 1

            practice_files += 1
            practice_records += record_count
            total_files += 1
            total_records += record_count

        tier_desc = "files 1-4 clean | 5-7 mixed | 8-9 expired | 10-12 problem"
        print(
            f"  [{practice_idx + 1:>2}/15] {PRACTICE_DISPLAY_NAMES[practice_type]:<42s} "
            f"-> {practice_files:>3} files, {practice_records:>4} records"
        )

    print("-" * 70)
    print(f"  Total: {total_files} files, {total_records} provider enrollment records")
    print(f"  Output: test_data/enrollment/{{practice_type}}/enrollment_{{practice}}_XXX.dat")
    print("=" * 70)


if __name__ == "__main__":
    main()
