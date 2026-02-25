**Healthcare RCM:** Enrollment Velocity Tracker
**Technical Lead:** Melinda Corbett, CPSO, CPC, CPPM, CPB  
**Target Impact:** Provider Enrollment and Cashflow Acceleration

# Enrollment Velocity Tracker

Processes provider enrollment test data files and extracts enrollment status, credential verification, and velocity metrics across 16 medical practice types. Includes a PECOS-style enrollment tracker engine prototype.

## Repository Structure

```
Enrollment_Velocity_Tracker/
├── enrollment_velocity.py            # Main entry point — enrollment analysis pipeline
├── Velocity_Enrollment_Tracker.py    # Engine prototype (PECOS tracker simulation)
└── generators/
    ├── generate_enrollment.py        # Enrollment test data generator (.dat files)
    ├── test_data_commons.py          # Shared data catalog (practice types, CPT codes, payers)
    └── __init__.py
```

## What It Does

1. **Enrollment Parsing** — Reads pipe-delimited `.dat` enrollment files across 16 practice types, extracting provider NPI, enrollment status, effective dates, credential details, and payer assignments.
2. **Status Tracking** — Classifies each provider's enrollment state (Active, Pending, Expired, Terminated) and flags credential expirations approaching within a configurable alert window.
3. **Velocity Metrics** — Calculates enrollment processing speed, average days-to-enrollment, and throughput rates by practice type and payer.
4. **Reporting** — Generates per-practice CSVs plus consolidated outputs:
   - `{practice_type}_enrollment_status.csv` — Provider-level enrollment status
   - `{practice_type}_credential_alerts.csv` — Expiring credential warnings
   - `{practice_type}_velocity_metrics.csv` — Processing speed metrics
   - `enrollment_dashboard.json` — Consolidated JSON dashboard
   - `enrollment_velocity_report.txt` — Human-readable summary report

## Prerequisites

- Python 3.12+
- No additional pip packages required

## Usage

```bash
# Generate test enrollment data (if needed)
python generators/generate_enrollment.py

# Run the full enrollment analysis
python enrollment_velocity.py

# Target a specific practice type
python enrollment_velocity.py --practice-type cardiology

# Customize alert threshold
python enrollment_velocity.py --alert-days 60 --verbose
```

## Output

Results are written to `Results/Enrollment_Velocity/` with per-practice CSVs and consolidated dashboard files.

## 🎓 About the Author
Melinda Corbett is an Executive Transformation Leader with 12+ years of experience in healthcare operations and AI-driven optimization.She specializes in translating complex aggregate platform data into board-level narratives.
