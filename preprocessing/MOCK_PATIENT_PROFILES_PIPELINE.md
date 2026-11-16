# How the final dataset was produced

This document describes, step by step, how the 516-patient dataset in
`artifacts/mock_patient_profiles/` was built from the Synthea simulator. For a description of the
dataset itself (columns, formats, caveats), see
[`MOCK_PATIENT_PROFILES_DATASET.md`](MOCK_PATIENT_PROFILES_DATASET.md).

All patients are **synthetic**. No real patient data was used.

## Overview

```
Synthea (rule-based simulator, no LLM)
   │  generate_diverse.sh          48 runs × 10 living patients, 12 states × 4 age bands
   │  generate_extra.sh            extra batch: young adults 18-29 and pregnant women
   ▼
output_diverse/{fhir,csv}          735 patients (636 living + 99 died in simulation)
   │  add_csv_headers.py           restore header rows lost by CSV append mode
   ▼
output_diverse/csv/*.csv           raw relational tables (patients, encounters, ...)
   │  build_final.py               filter profiles, shorten, summarize
   ▼
artifacts/mock_patient_profiles/    516 compact patient profiles (CSV + JSONL)
   │  normalize_dates.py           replace absolute dates with time-before-reference-date
   ▼
artifacts/mock_patient_profiles/    same 516 profiles with relative dates (CSV + JSONL)
```

`build_dataset.py` is an earlier intermediate step that produced a longer 500-patient
version (`output_diverse/dataset/`). The final dataset does **not** depend on it,
because `build_final.py` reads the raw CSV tables directly.

## Environment

- Synthea checked out at commit `bd8a52ba0` (master).
- Java: OpenJDK 17 (`brew install openjdk@17`). Before running, make sure it is on the path:
  `export JAVA_HOME=/opt/homebrew/opt/openjdk@17 PATH=/opt/homebrew/opt/openjdk@17/bin:$PATH`
- Python 3 standard library only (no packages). Scripts were run with `python3 -I`.
- Reference date used for ages and recency labels: **2026-11-15**.

## Step 1: Generate diverse patients — `generate_diverse.sh`

Goal: a varied population, not 500 near-identical patients.

```bash
./generate_diverse.sh 10 ./output_diverse
```

Diversity comes from:

| Lever | Values |
|---|---|
| State (different demographics, income, race/ethnicity mix) | Massachusetts, California, Texas, Mississippi, New York, Alaska, Hawaii, Florida, Arizona, Illinois, Georgia, Washington |
| Age band (`-a`) | 0-17, 18-39, 40-64, 65-90 |
| Seed (`-s`) | one distinct seed per run, 1001 to 1048 |
| Gender | left random within each run |

That is 12 states × 4 age bands = **48 runs**, each with `-p 10` (10 *living* patients).
Each run exports FHIR (one JSON bundle per patient) and CSV (shared tables, append mode).
Runs take about 50 seconds each, so the batch takes roughly 40 minutes.

Result of the first batch: **574 patients**. 480 are the requested living patients, and 94
more died during the simulation. Synthea exports patients who died before the reference date
in addition to the requested living ones.

## Step 1b: Extra batch — `generate_extra.sh`

The first batch left few patients aged 18 to 29 (and very few pregnant ones). Reasons: the
18-39 band was spread over two decades, and the first filter dropped adults under 28 (see the
marital note in step 3). An extra batch was appended to the same output folder:

```bash
./generate_extra.sh ./output_diverse
```

| Part | Settings | Count |
|---|---|---|
| Young adults | `-a 18-29`, any gender, 10 per state, seeds 2001 onward | 120 |
| Pregnant women | `-g F -a 18-45`, 3 per state, kept by `preprocessing/keep_pregnant.json` | 36 |

`keep_pregnant.json` is a Synthea *keep module*: a patient is kept only if she has an active
"Normal pregnancy" (SNOMED 72892002) at the end of the simulation, so she is pregnant on the
reference date. Synthea retries until the requested number of patients match.

CSV append mode writes no header rows, so run `add_csv_headers.py` (step 2) after this
batch. In this project the headers had already been added, and the extra rows were appended
to the headered files, which gives the same result. The CSV tables were backed up first.

Result: **735 patients** in total (636 living + 99 died in simulation).

## Step 2: Restore CSV headers — `add_csv_headers.py`

With `exporter.csv.append_mode=true`, Synthea writes no header rows. The script prepends
the standard header to each of the 18 CSV tables:

```bash
python3 -I add_csv_headers.py output_diverse/csv
```

Headers were taken from a small test run (same Synthea version) and, for `allergies.csv`,
from `CSVConstants.java`.

## Step 3: Build the final profiles — `build_final.py`

```bash
python3 -I build_final.py output_diverse/csv output_diverse/final 2026-11-15
```

The script reads the raw tables, selects patients, and compresses each patient's record
into the 16 columns described in the dataset README.

### 3a. Which patients are kept

A patient is kept only if all of these hold:

| Rule | Removed | Why |
|---|---|---|
| Age at 2026-11-15 is 18 or older | 120 living patients under 18 (74 under 10, 46 aged 10 to 17) | Adult profiles only |
| No death date | 99 who died in the simulation | The experiment treats everyone as alive. Their records can contain end-of-life care (hospice, dialysis) that would be inconsistent. |
| Marital status present, or age under 28 | none | See below |

Starting from 735 patients, **516 remain** (280 F, 236 M). The removed counts add up to
219 = 99 + 120, and none of the removed patients falls in both groups.

**Marital status.** Synthea assigns it only at age 28 (`LifecycleModule.java`, `case 28`: 80%
married, 20% single). Everyone younger has a blank value. An earlier version of the script
dropped blank-marital patients, which removed all adults aged 18 to 27. The script now keeps
them and sets `marital` to `S` (single), which is an **assumption**, not data. An adult aged 28
or older with a blank value would still be dropped (none exist). To leave the value blank
instead, change the `marital = ...` line in `build_final.py`.

Age is computed from `BIRTHDATE` to 2026-11-15, treating the patient as alive.

### 3b. How each column is built

Throughout, SNOMED type tags such as `(disorder)`, `(finding)`, `(procedure)` are stripped
from descriptions to save space.

**Status rule (ongoing vs resolved)** used for conditions, medications and care plans:
the source tables have a `STOP` date per row. A blank `STOP`, or one after the reference
date, means **ongoing**. Otherwise it is **resolved**. This status is taken from the data,
not simulated. If the same item occurs several times, it counts as ongoing if any
occurrence is ongoing.

| Column | Built from | Rule |
|---|---|---|
| `patient_id`, `marital`, `race`, `ethnicity`, `gender`, `city`, `state` | `patients.csv` | copied as-is |
| `age` | `patients.csv` | birth date to 2026-11-15 |
| `pregnancy_status` | `patients.csv`, `conditions.csv` | "pregnant" if a "Normal pregnancy" condition is active on the reference date. Otherwise "not pregnant" for female patients and "not applicable" for male patients. |
| `health_problems` | `conditions.csv` | only conditions tagged "(disorder)" (real diseases, not employment or social findings); ongoing ones plus those resolved in the last 2 years; each with a status |
| `encounters` | `encounters.csv` | the 5 most recent distinct encounter types, each with its last date |
| `medication_details` | `medications.csv` | ongoing medications only, with reasons |
| `allergies` | `allergies.csv` | distinct allergy descriptions, minus the generic "Allergic disposition" placeholder |
| `careplan_details` | `careplans.csv` | ongoing care plans only, with reasons |
| `procedures_detail` | `procedures.csv` | non-routine procedures from the last 10 years; the 8 most recent distinct ones, each with an occurrence count and the year of the last occurrence. See the filter below. |
| `observations_summary` | `observations.csv` | one short text built from the latest values. See 3c. |

**Procedure filter.** Routine items are excluded when the description matches any of:
`screen`, `assess`, `reconcil`, `educat`, `referral`, `examin`, `consult`, `counsel`,
`discuss`, `evaluat`, `review`, `auscult`, `questionnaire`, `test`, `monitor`, `measure`,
`record`, `interview`, `check`, `x-ray`, `history taking`, `inspection`, `palpation`,
`scale`, `plan of care`, `prone position`. The pattern is in `build_final.py` (`ROUTINE`).
Dental procedures are not excluded, so they appear often.

### 3c. The observation summary

The raw `observations.csv` holds about 70 distinct observations per patient (labs, survey
answers, quality-of-life scores), which made the full records very long. Instead of
listing them, `observations_summary` keeps only the latest value of a small set:

- **Vitals:** blood pressure, heart rate, respiratory rate, oxygen saturation, temperature,
  BMI, weight, height, pain score (dated by the latest vitals).
- **Labs:** HbA1c, glucose, creatinine, eGFR, total cholesterol, LDL, HDL, triglycerides,
  hemoglobin (dated by the latest lab).
- **Social:** tobacco smoking status, stress level.
- **Flags:** a short list added when a value crosses a common clinical threshold.

| Flag | Rule |
|---|---|
| high blood pressure | systolic ≥ 140 or diastolic ≥ 90 |
| obese BMI / underweight BMI | BMI ≥ 30 / BMI < 18.5 |
| HbA1c in diabetic range / prediabetic range | HbA1c ≥ 6.5 / 5.7 to 6.4 |
| high LDL | LDL ≥ 160 |
| reduced kidney function (eGFR<60) | eGFR < 60 |

**The flag thresholds were chosen by the author of the script, using common clinical
cutoffs. They are not part of Synthea and are not medical advice.** Change them in
`summary()` in `build_final.py` if you need different ones.

## Step 4: Normalize dates — `normalize_dates.py`

The dates in the profiles are tied to the simulation date. To keep them usable later,
this step converts every date into time elapsed before the reference date:

```bash
python3 -I normalize_dates.py artifacts/mock_patient_profiles output_diverse/csv \
    artifacts/mock_patient_profiles 2026-11-15
```

It writes `patients_final_relative.{csv,jsonl}`. Each dated item gets `days_ago` (exact),
`time_ago` (a phrase) and `recency` (a category). Exact procedure dates are recovered from
the raw `procedures.csv`, because the final file only kept the year. Dates inside
`observations_summary` become phrases, with numeric copies in `vitals_days_ago` and
`labs_days_ago`. See the dataset document for the full list of fields and categories.

Checks: no absolute dates remain except the `reference_date` column, every `days_ago` matches
the original encounter dates, and all 2,350 procedure years agree with the original
`last_year` values.

## Output

Four files in `artifacts/mock_patient_profiles/`, two versions of the same 516 patients.
First, the profiles with absolute dates (16 columns):

- `artifacts/mock_patient_profiles/patients_final.jsonl`: one JSON object per line, lists kept as lists
- `artifacts/mock_patient_profiles/patients_final.csv`: list fields flattened into text with ` | `

Second, the time-relative version made by Step 4, which is the one to use if the data will be
used months after 2026-11-15 (19 columns: the same 16 with dates replaced, plus
`vitals_days_ago`, `labs_days_ago` and `reference_date`):

- `artifacts/mock_patient_profiles/patients_final_relative.jsonl`
- `artifacts/mock_patient_profiles/patients_final_relative.csv`

Average record size is about 2,500 characters (about 700 tokens), down from about 17,800
characters for the earlier full version.

## Earlier versions (local only, not committed)

- `output_diverse/dataset/`: 500 patients (age 10 and older), with the longer
  `*_detail` columns, built by `build_dataset.py`.
- `output_diverse/final_full/`: an earlier 500-patient version of the final file with
  16 columns and full-length detail fields. It was written with a one-off command and not by
  a script in this repository.

## Known limitations

- **Synthea's assumptions apply.** The data reflects Synthea's disease modules and
  statistics. It does not contain the messiness of real records and should not be used to
  draw clinical conclusions.
- **Only the last 10 years are exported.** Synthea's `exporter.years_of_history` default is
  10, so older history is missing.
- **Young adults and pregnant women are oversampled.** The extra batch deliberately adds them, so the age and pregnancy mix is not a natural one.
- **Patients are treated as alive.** Ages were computed from birth dates without regard to
  the simulated death.
- **Selections drop detail.** Only the 5 most recent encounter types, 8 procedures, and the
  latest observation values are kept. Resolved medications and care plans are omitted.
- **Counts were spot-checked, not every record was reviewed.**
- **Placeholder values from Synthea** (such as ZIP `00000`) occur in fields that are not in
  the final dataset.

## Reproducing from scratch

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17 PATH=/opt/homebrew/opt/openjdk@17/bin:$PATH
./generate_diverse.sh 10 ./output_diverse
./generate_extra.sh ./output_diverse
python3 -I add_csv_headers.py output_diverse/csv
python3 -I build_final.py output_diverse/csv output_diverse/final 2026-11-15

# copy the two files into the committed artifacts folder
mkdir -p artifacts/mock_patient_profiles
cp output_diverse/final/patients_final.* artifacts/mock_patient_profiles/

# time-relative version
python3 -I normalize_dates.py artifacts/mock_patient_profiles output_diverse/csv \
    artifacts/mock_patient_profiles 2026-11-15
```

The `output_diverse/` working folder is large and is not committed to git.

Seeds are fixed, so the generated patients should be the same on the same Synthea version.
