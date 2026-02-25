#!/usr/bin/env python3
"""
Enrollment Velocity Analyzer
=============================

Processes provider enrollment test data files (.dat, pipe-delimited) and
extracts enrollment status, credential verification, and enrollment velocity
metrics across practice types.

Outputs per practice type:
  - {practice_type}_enrollment_status.csv
  - {practice_type}_credential_alerts.csv
  - {practice_type}_velocity_metrics.csv

Consolidated outputs:
  - enrollment_dashboard.json
  - enrollment_velocity_report.txt

Usage:
    python scripts/enrollment_velocity.py
    python scripts/enrollment_velocity.py --practice-type cardiology
    python scripts/enrollment_velocity.py --alert-days 60 --verbose
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta


# ============================================================
# CONSTANTS
# ============================================================

HEADER_FIELDS = [
    "ENROLLMENT_ID", "PROVIDER_NPI", "PROVIDER_LAST_NAME",
    "PROVIDER_FIRST_NAME", "CREDENTIAL", "TAXONOMY_CODE", "SPECIALTY",
    "FACILITY_NAME", "FACILITY_NPI", "FACILITY_ADDRESS", "CITY", "STATE",
    "ZIP", "PAYER_NAME", "PAYER_ID", "ENROLLMENT_STATUS", "ENROLLMENT_DATE",
    "EFFECTIVE_DATE", "TERMINATION_DATE", "REVALIDATION_DUE_DATE", "CAQH_ID",
    "CAQH_STATUS", "MEDICAID_ID", "MEDICARE_PTAN", "DEA_NUMBER",
    "DEA_EXPIRATION", "STATE_LICENSE", "LICENSE_STATE", "LICENSE_EXPIRATION",
    "BOARD_CERTIFIED", "BOARD_CERTIFICATION_DATE", "MALPRACTICE_CARRIER",
    "MALPRACTICE_EXPIRATION", "CREDENTIALING_STATUS", "LAST_UPDATED",
]

ENROLLMENT_STATUS_VALUES = {
    "ACTIVE", "PENDING", "DENIED", "TERMINATED", "EXPIRED",
    "REVALIDATION_NEEDED",
}

CREDENTIAL_CHECK_FIELDS = [
    ("CAQH_STATUS", None, "CAQH Profile"),
    ("LICENSE_EXPIRATION", "LICENSE_STATE", "State License"),
    ("DEA_EXPIRATION", "DEA_NUMBER", "DEA Registration"),
    ("MALPRACTICE_EXPIRATION", "MALPRACTICE_CARRIER", "Malpractice Insurance"),
]

ALERT_BUCKETS = [30, 60, 90]


# ============================================================
# PARSING
# ============================================================

def parse_date(value):
    """Parse a YYYY-MM-DD date string, returning None on empty/invalid."""
    if not value or not value.strip():
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def read_dat_file(filepath):
    """
    Read a pipe-delimited .dat file.  Returns a list of dicts, one per
    data row.  The header row is used to key each dict.
    """
    records = []
    with open(filepath, "r", newline="") as fh:
        lines = fh.read().splitlines()

    if not lines:
        return records

    header = [h.strip() for h in lines[0].split("|")]

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        values = line.split("|")
        # Pad or truncate to match header length
        while len(values) < len(header):
            values.append("")
        record = {header[i]: values[i].strip() for i in range(len(header))}
        records.append(record)

    return records


def discover_practice_types(input_dir):
    """
    Return sorted list of practice-type directory names that contain
    at least one .dat file.
    """
    practice_types = []
    if not os.path.isdir(input_dir):
        return practice_types
    for entry in sorted(os.listdir(input_dir)):
        subdir = os.path.join(input_dir, entry)
        if not os.path.isdir(subdir):
            continue
        if entry == "results":
            continue
        dat_files = [f for f in os.listdir(subdir) if f.endswith(".dat")]
        if dat_files:
            practice_types.append(entry)
    return practice_types


def load_practice_records(input_dir, practice_type):
    """Load all enrollment records for a single practice type."""
    subdir = os.path.join(input_dir, practice_type)
    all_records = []
    if not os.path.isdir(subdir):
        return all_records
    dat_files = sorted(f for f in os.listdir(subdir) if f.endswith(".dat"))
    for fname in dat_files:
        filepath = os.path.join(subdir, fname)
        records = read_dat_file(filepath)
        for rec in records:
            rec["_source_file"] = fname
            rec["_practice_type"] = practice_type
        all_records.extend(records)
    return all_records


# ============================================================
# ENROLLMENT STATUS ANALYSIS
# ============================================================

def compute_enrollment_status_rows(records, today):
    """
    Build rows for the enrollment_status CSV.

    Columns: provider_npi, provider_name, credential, specialty,
             payer_name, enrollment_status, effective_date, term_date,
             days_enrolled
    """
    rows = []
    for rec in records:
        npi = rec.get("PROVIDER_NPI", "")
        name = "{}, {}".format(
            rec.get("PROVIDER_LAST_NAME", ""),
            rec.get("PROVIDER_FIRST_NAME", ""),
        )
        credential = rec.get("CREDENTIAL", "")
        specialty = rec.get("SPECIALTY", "")
        payer = rec.get("PAYER_NAME", "")
        status = rec.get("ENROLLMENT_STATUS", "")
        eff_date = rec.get("EFFECTIVE_DATE", "")
        term_date = rec.get("TERMINATION_DATE", "")

        # Compute days_enrolled
        eff_dt = parse_date(eff_date)
        term_dt = parse_date(term_date)
        if eff_dt is not None:
            end = term_dt if term_dt is not None else today
            days_enrolled = max((end - eff_dt).days, 0)
        else:
            days_enrolled = ""

        rows.append({
            "provider_npi": npi,
            "provider_name": name,
            "credential": credential,
            "specialty": specialty,
            "payer_name": payer,
            "enrollment_status": status,
            "effective_date": eff_date,
            "term_date": term_date,
            "days_enrolled": days_enrolled,
        })
    return rows


# ============================================================
# CREDENTIAL VERIFICATION & ALERTS
# ============================================================

def compute_credential_alerts(records, today, alert_days):
    """
    Build rows for the credential_alerts CSV.

    Generates alerts for credentials expiring within *alert_days* or
    already expired, plus status-based alerts (CAQH not ACTIVE,
    board certification missing, credentialing not APPROVED).

    Columns: provider_npi, provider_name, alert_type, credential_type,
             expiration_date, days_until_expiry, action_required, priority
    """
    alerts = []

    for rec in records:
        npi = rec.get("PROVIDER_NPI", "")
        name = "{}, {}".format(
            rec.get("PROVIDER_LAST_NAME", ""),
            rec.get("PROVIDER_FIRST_NAME", ""),
        )

        # --- Date-based credential expiration alerts ---
        date_checks = [
            ("LICENSE_EXPIRATION", "State License"),
            ("DEA_EXPIRATION", "DEA Registration"),
            ("MALPRACTICE_EXPIRATION", "Malpractice Insurance"),
        ]
        for field, cred_type in date_checks:
            exp_date = parse_date(rec.get(field, ""))
            if exp_date is None:
                continue
            days_until = (exp_date - today).days
            if days_until <= alert_days:
                if days_until < 0:
                    alert_type = "EXPIRED"
                    action = "Immediate renewal required"
                    priority = "CRITICAL"
                elif days_until <= 30:
                    alert_type = "EXPIRING_30_DAYS"
                    action = "Urgent renewal needed"
                    priority = "HIGH"
                elif days_until <= 60:
                    alert_type = "EXPIRING_60_DAYS"
                    action = "Schedule renewal"
                    priority = "MEDIUM"
                else:
                    alert_type = "EXPIRING_90_DAYS"
                    action = "Plan renewal"
                    priority = "LOW"

                alerts.append({
                    "provider_npi": npi,
                    "provider_name": name,
                    "alert_type": alert_type,
                    "credential_type": cred_type,
                    "expiration_date": exp_date.strftime("%Y-%m-%d"),
                    "days_until_expiry": days_until,
                    "action_required": action,
                    "priority": priority,
                })

        # --- CAQH status alert ---
        caqh_status = rec.get("CAQH_STATUS", "")
        if caqh_status and caqh_status != "ACTIVE":
            priority = "HIGH" if caqh_status == "EXPIRED" else "MEDIUM"
            action = ("Renew CAQH profile immediately"
                      if caqh_status == "EXPIRED"
                      else "Complete CAQH attestation")
            alerts.append({
                "provider_npi": npi,
                "provider_name": name,
                "alert_type": "CAQH_NOT_ACTIVE",
                "credential_type": "CAQH Profile",
                "expiration_date": "",
                "days_until_expiry": "",
                "action_required": action,
                "priority": priority,
            })

        # --- Board certification alert ---
        board = rec.get("BOARD_CERTIFIED", "")
        if board == "NO":
            alerts.append({
                "provider_npi": npi,
                "provider_name": name,
                "alert_type": "NOT_BOARD_CERTIFIED",
                "credential_type": "Board Certification",
                "expiration_date": "",
                "days_until_expiry": "",
                "action_required": "Obtain board certification",
                "priority": "MEDIUM",
            })

        # --- Credentialing status alert ---
        cred_status = rec.get("CREDENTIALING_STATUS", "")
        if cred_status and cred_status not in ("APPROVED",):
            if cred_status == "DENIED":
                priority = "CRITICAL"
                action = "Appeal or re-apply for credentialing"
            elif cred_status == "EXPIRED":
                priority = "HIGH"
                action = "Re-initiate credentialing process"
            else:  # IN_PROCESS
                priority = "LOW"
                action = "Follow up on credentialing application"
            alerts.append({
                "provider_npi": npi,
                "provider_name": name,
                "alert_type": "CREDENTIALING_{}".format(cred_status),
                "credential_type": "Credentialing",
                "expiration_date": "",
                "days_until_expiry": "",
                "action_required": action,
                "priority": priority,
            })

        # --- Enrollment status alert for non-ACTIVE ---
        enroll_status = rec.get("ENROLLMENT_STATUS", "")
        if enroll_status in ("DENIED", "EXPIRED", "TERMINATED",
                             "REVALIDATION_NEEDED"):
            if enroll_status == "DENIED":
                priority = "CRITICAL"
                action = "Appeal denial or re-submit enrollment"
            elif enroll_status == "EXPIRED":
                priority = "HIGH"
                action = "Re-enroll with payer"
            elif enroll_status == "TERMINATED":
                priority = "HIGH"
                action = "Investigate termination and re-enroll"
            else:  # REVALIDATION_NEEDED
                priority = "MEDIUM"
                action = "Complete revalidation before due date"
            alerts.append({
                "provider_npi": npi,
                "provider_name": name,
                "alert_type": "ENROLLMENT_{}".format(enroll_status),
                "credential_type": "Payer Enrollment ({})".format(
                    rec.get("PAYER_NAME", "")),
                "expiration_date": rec.get("REVALIDATION_DUE_DATE", ""),
                "days_until_expiry": "",
                "action_required": action,
                "priority": priority,
            })

        # --- Revalidation due date alert ---
        reval_date = parse_date(rec.get("REVALIDATION_DUE_DATE", ""))
        if reval_date is not None and enroll_status == "ACTIVE":
            days_until = (reval_date - today).days
            if days_until <= alert_days:
                if days_until < 0:
                    alert_type = "REVALIDATION_OVERDUE"
                    action = "Immediate revalidation required"
                    priority = "CRITICAL"
                elif days_until <= 30:
                    alert_type = "REVALIDATION_DUE_30_DAYS"
                    action = "Submit revalidation now"
                    priority = "HIGH"
                elif days_until <= 60:
                    alert_type = "REVALIDATION_DUE_60_DAYS"
                    action = "Prepare revalidation documents"
                    priority = "MEDIUM"
                else:
                    alert_type = "REVALIDATION_DUE_90_DAYS"
                    action = "Plan for revalidation"
                    priority = "LOW"
                alerts.append({
                    "provider_npi": npi,
                    "provider_name": name,
                    "alert_type": alert_type,
                    "credential_type": "Revalidation ({})".format(
                        rec.get("PAYER_NAME", "")),
                    "expiration_date": reval_date.strftime("%Y-%m-%d"),
                    "days_until_expiry": days_until,
                    "action_required": action,
                    "priority": priority,
                })

    # Sort by priority then by days_until_expiry
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    alerts.sort(key=lambda a: (
        priority_order.get(a["priority"], 99),
        a["days_until_expiry"] if isinstance(a["days_until_expiry"], int) else 9999,
    ))

    return alerts


# ============================================================
# VELOCITY METRICS
# ============================================================

def compute_velocity_metrics(records, today):
    """
    Build rows for the velocity_metrics CSV.

    Columns: payer_name, avg_days_to_enroll, success_rate, pending_count,
             active_count, denied_count, revalidation_due_count
    """
    payer_stats = defaultdict(lambda: {
        "days_to_enroll": [],
        "total": 0,
        "active": 0,
        "pending": 0,
        "denied": 0,
        "terminated": 0,
        "expired": 0,
        "revalidation_needed": 0,
    })

    for rec in records:
        payer = rec.get("PAYER_NAME", "Unknown")
        status = rec.get("ENROLLMENT_STATUS", "")
        stats = payer_stats[payer]
        stats["total"] += 1

        status_lower = status.lower().replace(" ", "_")
        if status_lower in stats:
            stats[status_lower] += 1

        # Compute time-to-enrollment (enrollment_date -> effective_date)
        enroll_dt = parse_date(rec.get("ENROLLMENT_DATE", ""))
        eff_dt = parse_date(rec.get("EFFECTIVE_DATE", ""))
        if enroll_dt is not None and eff_dt is not None:
            days = (eff_dt - enroll_dt).days
            if days >= 0:
                stats["days_to_enroll"].append(days)

    rows = []
    for payer in sorted(payer_stats.keys()):
        stats = payer_stats[payer]
        dte_list = stats["days_to_enroll"]
        avg_days = (sum(dte_list) / len(dte_list)) if dte_list else 0.0

        # Success rate = active / total (where total excludes pending)
        non_pending = stats["total"] - stats["pending"]
        success_rate = (
            (stats["active"] / non_pending * 100.0) if non_pending > 0
            else 0.0
        )

        rows.append({
            "payer_name": payer,
            "avg_days_to_enroll": round(avg_days, 1),
            "success_rate": round(success_rate, 1),
            "pending_count": stats["pending"],
            "active_count": stats["active"],
            "denied_count": stats["denied"],
            "revalidation_due_count": stats["revalidation_needed"],
        })

    # Sort by avg_days_to_enroll ascending (fastest first)
    rows.sort(key=lambda r: r["avg_days_to_enroll"])
    return rows


# ============================================================
# CREDENTIAL COMPLIANCE SUMMARY (for dashboard / report)
# ============================================================

def compute_credential_compliance(records, today, alert_days):
    """
    Return a dict summarising credential compliance across all records.
    """
    total = len(records)
    if total == 0:
        return {}

    caqh_active = sum(1 for r in records if r.get("CAQH_STATUS") == "ACTIVE")
    caqh_expired = sum(1 for r in records if r.get("CAQH_STATUS") == "EXPIRED")
    caqh_pending = sum(1 for r in records if r.get("CAQH_STATUS") == "PENDING")

    board_yes = sum(1 for r in records if r.get("BOARD_CERTIFIED") == "YES")
    board_no = sum(1 for r in records if r.get("BOARD_CERTIFIED") == "NO")

    cred_approved = sum(
        1 for r in records if r.get("CREDENTIALING_STATUS") == "APPROVED")
    cred_in_process = sum(
        1 for r in records if r.get("CREDENTIALING_STATUS") == "IN_PROCESS")
    cred_expired = sum(
        1 for r in records if r.get("CREDENTIALING_STATUS") == "EXPIRED")
    cred_denied = sum(
        1 for r in records if r.get("CREDENTIALING_STATUS") == "DENIED")

    # Expiring credentials within alert window
    license_expiring = 0
    dea_expiring = 0
    malpractice_expiring = 0
    license_expired = 0
    dea_expired = 0
    malpractice_expired = 0

    for rec in records:
        for field, counter_expiring, counter_expired in [
            ("LICENSE_EXPIRATION", "license_expiring", "license_expired"),
            ("DEA_EXPIRATION", "dea_expiring", "dea_expired"),
            ("MALPRACTICE_EXPIRATION", "malpractice_expiring",
             "malpractice_expired"),
        ]:
            dt = parse_date(rec.get(field, ""))
            if dt is None:
                continue
            days_until = (dt - today).days
            if days_until < 0:
                if "license" in field.lower():
                    license_expired += 1
                elif "dea" in field.lower():
                    dea_expired += 1
                else:
                    malpractice_expired += 1
            elif days_until <= alert_days:
                if "license" in field.lower():
                    license_expiring += 1
                elif "dea" in field.lower():
                    dea_expiring += 1
                else:
                    malpractice_expiring += 1

    return {
        "total_records": total,
        "caqh": {
            "active": caqh_active,
            "expired": caqh_expired,
            "pending": caqh_pending,
            "compliance_rate": round(caqh_active / total * 100, 1),
        },
        "board_certification": {
            "certified": board_yes,
            "not_certified": board_no,
            "rate": round(board_yes / total * 100, 1),
        },
        "credentialing": {
            "approved": cred_approved,
            "in_process": cred_in_process,
            "expired": cred_expired,
            "denied": cred_denied,
            "approval_rate": round(cred_approved / total * 100, 1),
        },
        "expiring_credentials": {
            "license_expiring": license_expiring,
            "license_expired": license_expired,
            "dea_expiring": dea_expiring,
            "dea_expired": dea_expired,
            "malpractice_expiring": malpractice_expiring,
            "malpractice_expired": malpractice_expired,
        },
    }


# ============================================================
# CREDENTIALING VELOCITY
# ============================================================

def compute_credentialing_days(records):
    """
    Estimate average days to credentialing completion per payer.

    Uses enrollment_date to effective_date for APPROVED records as a proxy
    for the credentialing timeline.
    """
    payer_days = defaultdict(list)
    for rec in records:
        if rec.get("CREDENTIALING_STATUS") != "APPROVED":
            continue
        enroll_dt = parse_date(rec.get("ENROLLMENT_DATE", ""))
        eff_dt = parse_date(rec.get("EFFECTIVE_DATE", ""))
        if enroll_dt and eff_dt:
            days = (eff_dt - enroll_dt).days
            if days >= 0:
                payer = rec.get("PAYER_NAME", "Unknown")
                payer_days[payer].append(days)

    result = {}
    for payer, days_list in sorted(payer_days.items()):
        result[payer] = round(sum(days_list) / len(days_list), 1)
    return result


# ============================================================
# CSV WRITERS
# ============================================================

def write_csv(filepath, fieldnames, rows):
    """Write a list of dicts to a CSV file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# DASHBOARD JSON
# ============================================================

def build_dashboard(all_data, today, alert_days):
    """
    Build the consolidated dashboard dict that will be written as JSON.

    all_data: dict mapping practice_type -> list of records
    """
    dashboard = {
        "generated_at": today.strftime("%Y-%m-%d %H:%M:%S"),
        "alert_window_days": alert_days,
        "practice_types": {},
        "consolidated": {},
    }

    grand_total = 0
    grand_status_dist = defaultdict(int)
    grand_cred_compliance = defaultdict(lambda: defaultdict(int))
    all_velocity_records = []

    for ptype in sorted(all_data.keys()):
        records = all_data[ptype]
        grand_total += len(records)

        # Status distribution
        status_dist = defaultdict(int)
        for rec in records:
            s = rec.get("ENROLLMENT_STATUS", "UNKNOWN")
            status_dist[s] += 1
            grand_status_dist[s] += 1

        # Credential compliance
        compliance = compute_credential_compliance(records, today, alert_days)

        # Velocity metrics
        velocity = compute_velocity_metrics(records, today)

        # Credentialing days
        cred_days = compute_credentialing_days(records)

        dashboard["practice_types"][ptype] = {
            "total_providers": len(records),
            "enrollment_status_distribution": dict(status_dist),
            "credential_compliance": compliance,
            "velocity_by_payer": velocity,
            "avg_credentialing_days_by_payer": cred_days,
        }

        all_velocity_records.extend(records)

    # Consolidated velocity
    consolidated_velocity = compute_velocity_metrics(all_velocity_records,
                                                     today)
    consolidated_cred_days = compute_credentialing_days(all_velocity_records)
    consolidated_compliance = compute_credential_compliance(
        all_velocity_records, today, alert_days)

    dashboard["consolidated"] = {
        "total_providers": grand_total,
        "enrollment_status_distribution": dict(grand_status_dist),
        "credential_compliance": consolidated_compliance,
        "velocity_by_payer": consolidated_velocity,
        "avg_credentialing_days_by_payer": consolidated_cred_days,
    }

    return dashboard


# ============================================================
# TEXT REPORT
# ============================================================

def build_text_report(all_data, today, alert_days):
    """
    Produce the enrollment_velocity_report.txt content.
    """
    lines = []

    def sep(char="=", width=78):
        lines.append(char * width)

    def heading(title):
        sep()
        lines.append(title.upper())
        sep()
        lines.append("")

    heading("Enrollment Velocity Report")
    lines.append("Generated: {}".format(today.strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("Alert Window: {} days".format(alert_days))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 1: Total providers per practice type
    # ------------------------------------------------------------------
    heading("1. Total Providers Processed Per Practice Type")
    total_all = 0
    for ptype in sorted(all_data.keys()):
        count = len(all_data[ptype])
        total_all += count
        lines.append("  {:<35s} {:>6d} providers".format(ptype, count))
    lines.append("")
    lines.append("  {:<35s} {:>6d} providers".format("GRAND TOTAL", total_all))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 2: Enrollment status distribution
    # ------------------------------------------------------------------
    heading("2. Enrollment Status Distribution")

    all_records = []
    for recs in all_data.values():
        all_records.extend(recs)

    status_counts = defaultdict(int)
    for rec in all_records:
        status_counts[rec.get("ENROLLMENT_STATUS", "UNKNOWN")] += 1

    total = len(all_records)
    for status in ["ACTIVE", "PENDING", "REVALIDATION_NEEDED", "TERMINATED",
                    "EXPIRED", "DENIED"]:
        count = status_counts.get(status, 0)
        pct = (count / total * 100.0) if total > 0 else 0.0
        bar = "#" * int(pct / 2)
        lines.append("  {:<25s} {:>5d}  ({:>5.1f}%)  {}".format(
            status, count, pct, bar))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 3: Credential compliance summary
    # ------------------------------------------------------------------
    heading("3. Credential Compliance Summary")
    compliance = compute_credential_compliance(all_records, today, alert_days)
    if compliance:
        caqh = compliance["caqh"]
        lines.append("  CAQH Profile Status:")
        lines.append("    Active:  {:>5d}  ({:.1f}%)".format(
            caqh["active"], caqh["compliance_rate"]))
        lines.append("    Expired: {:>5d}".format(caqh["expired"]))
        lines.append("    Pending: {:>5d}".format(caqh["pending"]))
        lines.append("")

        board = compliance["board_certification"]
        lines.append("  Board Certification:")
        lines.append("    Certified:     {:>5d}  ({:.1f}%)".format(
            board["certified"], board["rate"]))
        lines.append("    Not Certified: {:>5d}".format(
            board["not_certified"]))
        lines.append("")

        cred = compliance["credentialing"]
        lines.append("  Credentialing Status:")
        lines.append("    Approved:   {:>5d}  ({:.1f}%)".format(
            cred["approved"], cred["approval_rate"]))
        lines.append("    In Process: {:>5d}".format(cred["in_process"]))
        lines.append("    Expired:    {:>5d}".format(cred["expired"]))
        lines.append("    Denied:     {:>5d}".format(cred["denied"]))
        lines.append("")

        exp = compliance["expiring_credentials"]
        lines.append("  Credential Expiration Summary (within {} days):".format(
            alert_days))
        lines.append("    State License - Expiring: {:>4d}  Expired: {:>4d}"
                      .format(exp["license_expiring"], exp["license_expired"]))
        lines.append("    DEA Registration - Expiring: {:>4d}  Expired: {:>4d}"
                      .format(exp["dea_expiring"], exp["dea_expired"]))
        lines.append("    Malpractice Ins. - Expiring: {:>4d}  Expired: {:>4d}"
                      .format(exp["malpractice_expiring"],
                              exp["malpractice_expired"]))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 4: Top enrollment bottlenecks
    # ------------------------------------------------------------------
    heading("4. Top Enrollment Bottlenecks")

    # Bottlenecks = payers with longest avg time to enroll
    velocity = compute_velocity_metrics(all_records, today)
    # Sort slowest first
    slowest = sorted(velocity, key=lambda r: r["avg_days_to_enroll"],
                     reverse=True)
    lines.append("  Slowest Payers (by avg days to enrollment):")
    lines.append("  {:<30s} {:>10s} {:>12s} {:>8s}".format(
        "Payer", "Avg Days", "Success Rate", "Denied"))
    lines.append("  " + "-" * 64)
    for row in slowest[:10]:
        lines.append("  {:<30s} {:>10.1f} {:>11.1f}% {:>8d}".format(
            row["payer_name"],
            row["avg_days_to_enroll"],
            row["success_rate"],
            row["denied_count"],
        ))
    lines.append("")

    # Payers with most denials
    most_denied = sorted(velocity, key=lambda r: r["denied_count"],
                         reverse=True)
    lines.append("  Payers with Most Denials:")
    lines.append("  {:<30s} {:>8s} {:>8s}".format(
        "Payer", "Denied", "Total"))
    lines.append("  " + "-" * 50)
    for row in most_denied[:10]:
        if row["denied_count"] == 0:
            break
        total_for_payer = (row["active_count"] + row["pending_count"]
                           + row["denied_count"]
                           + row["revalidation_due_count"])
        lines.append("  {:<30s} {:>8d} {:>8d}".format(
            row["payer_name"], row["denied_count"], total_for_payer))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 5: Upcoming expirations (30/60/90 day alerts)
    # ------------------------------------------------------------------
    heading("5. Upcoming Credential Expirations")

    for bucket in ALERT_BUCKETS:
        exp_count = {"license": 0, "dea": 0, "malpractice": 0}
        for rec in all_records:
            for field, key in [("LICENSE_EXPIRATION", "license"),
                               ("DEA_EXPIRATION", "dea"),
                               ("MALPRACTICE_EXPIRATION", "malpractice")]:
                dt = parse_date(rec.get(field, ""))
                if dt is None:
                    continue
                days_until = (dt - today).days
                if 0 <= days_until <= bucket:
                    exp_count[key] += 1

        lines.append("  Expiring within {} days:".format(bucket))
        lines.append("    State Licenses:       {:>4d}".format(
            exp_count["license"]))
        lines.append("    DEA Registrations:    {:>4d}".format(
            exp_count["dea"]))
        lines.append("    Malpractice Policies: {:>4d}".format(
            exp_count["malpractice"]))
        lines.append("")

    # Already expired
    exp_count = {"license": 0, "dea": 0, "malpractice": 0}
    for rec in all_records:
        for field, key in [("LICENSE_EXPIRATION", "license"),
                           ("DEA_EXPIRATION", "dea"),
                           ("MALPRACTICE_EXPIRATION", "malpractice")]:
            dt = parse_date(rec.get(field, ""))
            if dt is None:
                continue
            if (dt - today).days < 0:
                exp_count[key] += 1
    lines.append("  Already expired:")
    lines.append("    State Licenses:       {:>4d}".format(
        exp_count["license"]))
    lines.append("    DEA Registrations:    {:>4d}".format(
        exp_count["dea"]))
    lines.append("    Malpractice Policies: {:>4d}".format(
        exp_count["malpractice"]))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 6: Enrollment velocity by payer (fastest to slowest)
    # ------------------------------------------------------------------
    heading("6. Enrollment Velocity by Payer (Fastest to Slowest)")
    lines.append("  {:<30s} {:>10s} {:>12s} {:>8s} {:>8s} {:>8s}".format(
        "Payer", "Avg Days", "Success Rate", "Active", "Pending", "Reval"))
    lines.append("  " + "-" * 80)
    for row in velocity:
        lines.append(
            "  {:<30s} {:>10.1f} {:>11.1f}% {:>8d} {:>8d} {:>8d}".format(
                row["payer_name"],
                row["avg_days_to_enroll"],
                row["success_rate"],
                row["active_count"],
                row["pending_count"],
                row["revalidation_due_count"],
            ))
    lines.append("")

    # Credentialing days
    cred_days = compute_credentialing_days(all_records)
    if cred_days:
        lines.append("  Avg Days to Credentialing Completion (APPROVED only):")
        lines.append("  {:<30s} {:>10s}".format("Payer", "Avg Days"))
        lines.append("  " + "-" * 42)
        for payer in sorted(cred_days.keys(),
                            key=lambda p: cred_days[p]):
            lines.append("  {:<30s} {:>10.1f}".format(
                payer, cred_days[payer]))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 7: Action items
    # ------------------------------------------------------------------
    heading("7. Action Items for Denied/Expired/Pending Enrollments")

    action_items = []
    for rec in all_records:
        status = rec.get("ENROLLMENT_STATUS", "")
        if status not in ("DENIED", "EXPIRED", "TERMINATED", "PENDING",
                          "REVALIDATION_NEEDED"):
            continue
        npi = rec.get("PROVIDER_NPI", "")
        name = "{}, {}".format(rec.get("PROVIDER_LAST_NAME", ""),
                               rec.get("PROVIDER_FIRST_NAME", ""))
        payer = rec.get("PAYER_NAME", "")
        ptype = rec.get("_practice_type", "")

        if status == "DENIED":
            action = "Appeal denial or resubmit enrollment application"
            priority = "CRITICAL"
        elif status == "EXPIRED":
            action = "Re-enroll provider with payer"
            priority = "HIGH"
        elif status == "TERMINATED":
            action = "Investigate termination reason; initiate re-enrollment"
            priority = "HIGH"
        elif status == "REVALIDATION_NEEDED":
            action = "Complete revalidation before due date"
            priority = "MEDIUM"
        else:  # PENDING
            action = "Follow up on pending enrollment application"
            priority = "LOW"

        action_items.append({
            "priority": priority,
            "npi": npi,
            "name": name,
            "payer": payer,
            "status": status,
            "practice_type": ptype,
            "action": action,
        })

    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    action_items.sort(key=lambda a: (priority_order.get(a["priority"], 99),
                                     a["npi"]))

    lines.append("  {:<10s} {:<14s} {:<28s} {:<22s} {:<20s}".format(
        "Priority", "NPI", "Provider Name", "Payer", "Status"))
    lines.append("  " + "-" * 96)

    for item in action_items:
        lines.append("  {:<10s} {:<14s} {:<28s} {:<22s} {:<20s}".format(
            item["priority"],
            item["npi"],
            item["name"][:26],
            item["payer"][:20],
            item["status"],
        ))
        lines.append("    -> {}".format(item["action"]))

    lines.append("")
    lines.append("  Total action items: {}".format(len(action_items)))
    lines.append("    CRITICAL: {}".format(
        sum(1 for a in action_items if a["priority"] == "CRITICAL")))
    lines.append("    HIGH:     {}".format(
        sum(1 for a in action_items if a["priority"] == "HIGH")))
    lines.append("    MEDIUM:   {}".format(
        sum(1 for a in action_items if a["priority"] == "MEDIUM")))
    lines.append("    LOW:      {}".format(
        sum(1 for a in action_items if a["priority"] == "LOW")))
    lines.append("")

    sep()
    lines.append("END OF REPORT")
    sep()

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Enrollment Velocity Analyzer - processes provider "
                    "enrollment data files and extracts enrollment status, "
                    "credential verification, and enrollment velocity metrics.",
    )
    parser.add_argument(
        "--input-dir",
        default="test_data/enrollment",
        help="Root directory containing practice-type sub-directories with "
             ".dat enrollment files (default: test_data/enrollment)",
    )
    parser.add_argument(
        "--output-dir",
        default="Results/Enrollment_Velocity",
        help="Directory for output CSV, JSON, and TXT files "
             "(default: Results/Enrollment_Velocity)",
    )
    parser.add_argument(
        "--practice-type",
        default=None,
        help="Process only this practice type (default: all)",
    )
    parser.add_argument(
        "--alert-days",
        type=int,
        default=90,
        help="Days ahead to flag expiring credentials (default: 90)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed progress information",
    )

    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir)
    alert_days = args.alert_days
    verbose = args.verbose
    today = datetime.now()

    # Discover practice types
    if args.practice_type:
        practice_types = [args.practice_type]
        # Validate
        subdir = os.path.join(input_dir, args.practice_type)
        if not os.path.isdir(subdir):
            print("ERROR: Practice type directory not found: {}".format(subdir),
                  file=sys.stderr)
            sys.exit(1)
    else:
        practice_types = discover_practice_types(input_dir)

    if not practice_types:
        print("ERROR: No practice type directories found in: {}".format(
            input_dir), file=sys.stderr)
        sys.exit(1)

    if verbose:
        print("Enrollment Velocity Analyzer")
        print("  Input:  {}".format(input_dir))
        print("  Output: {}".format(output_dir))
        print("  Alert window: {} days".format(alert_days))
        print("  Practice types: {}".format(len(practice_types)))
        print()

    os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Process each practice type
    # ------------------------------------------------------------------
    all_data = {}  # practice_type -> list of records

    for ptype in practice_types:
        if verbose:
            print("Processing: {} ...".format(ptype))

        records = load_practice_records(input_dir, ptype)
        all_data[ptype] = records

        if not records:
            if verbose:
                print("  WARNING: No records found for {}".format(ptype))
            continue

        if verbose:
            print("  Loaded {} records from {} files".format(
                len(records),
                len(set(r["_source_file"] for r in records)),
            ))

    # ------------------------------------------------------------------
    # Consolidated outputs
    # ------------------------------------------------------------------
    if verbose:
        print()
        print("Building consolidated outputs ...")

    # Consolidated detailed CSV for all practices
    all_records = []
    for recs in all_data.values():
        all_records.extend(recs)
    consolidated_rows = []
    for rec in all_records:
        eff_date = rec.get("EFFECTIVE_DATE", "")
        term_date = rec.get("TERMINATION_DATE", "")
        eff_dt = parse_date(eff_date)
        term_dt = parse_date(term_date)
        if eff_dt is not None:
            end = term_dt if term_dt is not None else today
            days_enrolled = max((end - eff_dt).days, 0)
        else:
            days_enrolled = ""
        rec_copy = {k: v for k, v in rec.items() if k in HEADER_FIELDS}
        rec_copy["days_enrolled"] = days_enrolled
        consolidated_rows.append(rec_copy)
    consolidated_path = os.path.join(output_dir, "enrollment_all_practices_detailed_report.csv")
    write_csv(consolidated_path, HEADER_FIELDS + ["days_enrolled"], consolidated_rows)
    if verbose:
        print("  Wrote: {} (consolidated detailed export)".format(consolidated_path))

    # Dashboard JSON
    dashboard = build_dashboard(all_data, today, alert_days)
    dashboard_path = os.path.join(output_dir, "enrollment_dashboard.json")
    with open(dashboard_path, "w") as fh:
        json.dump(dashboard, fh, indent=2, default=str)
    if verbose:
        print("  Wrote: {}".format(dashboard_path))

    # Velocity report TXT
    report_text = build_text_report(all_data, today, alert_days)
    report_path = os.path.join(output_dir, "enrollment_velocity_report.txt")
    with open(report_path, "w") as fh:
        fh.write(report_text)
    if verbose:
        print("  Wrote: {}".format(report_path))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_records = sum(len(recs) for recs in all_data.values())
    total_alerts = 0
    for ptype in all_data:
        total_alerts += len(compute_credential_alerts(
            all_data[ptype], today, alert_days))

    print()
    print("Enrollment Velocity Analysis Complete")
    print("  Practice types processed: {}".format(len(practice_types)))
    print("  Total enrollment records: {}".format(total_records))
    print("  Total credential alerts:  {}".format(total_alerts))
    print("  Output directory:         {}".format(output_dir))


if __name__ == "__main__":
    main()
