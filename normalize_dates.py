#!/usr/bin/env python3
"""Convert absolute dates in the final patient profiles into time-relative values.

Usage: normalize_dates.py <final_dir> <raw_csv_dir> <out_dir> [reference_date=YYYY-MM-DD]

Why: the profiles were simulated as of a reference date (2026-11-15). Absolute dates go
stale as real time passes, so every date is replaced by "how long before the reference
date", which stays valid whenever the data is used ("today" = the day you use it).

Every dated item gets:
  days_ago  - exact whole days before the reference date (number, shiftable)
  time_ago  - human phrase: "3 days ago", "2 weeks ago", "7 months ago", "2 years ago"
  recency   - category: within_last_week, within_last_month, 1-6_months, 6-12_months,
              1-3_years, 3-10_years, over_10_years

Inputs : <final_dir>/patients_final.{jsonl,csv}  and  <raw_csv_dir>/procedures.csv
         (procedures.csv is used to recover the exact last date; the final file keeps
          only the year)
Outputs: <out_dir>/patients_final_relative.{jsonl,csv}
"""
import csv, json, re, sys, datetime, pathlib

final_dir = pathlib.Path(sys.argv[1])
raw_dir = pathlib.Path(sys.argv[2])
out_dir = pathlib.Path(sys.argv[3])
ref = datetime.date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else datetime.date.today()
out_dir.mkdir(parents=True, exist_ok=True)

TAG = re.compile(r"\s*\((finding|disorder|procedure|regime/therapy|record artifact|situation|"
                 r"substance|organism|physical object|observable entity|morphologic abnormality|person)\)$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def days_between(date_str):
    return max((ref - datetime.date.fromisoformat(date_str[:10])).days, 0)


def time_ago(d):
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    if d < 14:
        return f"{d} days ago"
    if d < 60:
        return f"{d // 7} weeks ago"
    if d < 365:
        return f"{d // 30} months ago"
    y = d // 365
    return "1 year ago" if y == 1 else f"{y} years ago"


def recency(d):
    if d <= 7:
        return "within_last_week"
    if d <= 30:
        return "within_last_month"
    if d <= 182:
        return "1-6_months"
    if d <= 365:
        return "6-12_months"
    if d <= 3 * 365:
        return "1-3_years"
    if d <= 10 * 365:
        return "3-10_years"
    return "over_10_years"


def rel(date_str):
    d = days_between(date_str)
    return {"days_ago": d, "time_ago": time_ago(d), "recency": recency(d)}


records = [json.loads(l) for l in open(final_dir / "patients_final.jsonl")]
ids = {r["patient_id"] for r in records}

# exact last date of each procedure (the final file only has the year)
last = {}
for row in csv.DictReader(open(raw_dir / "procedures.csv", newline="")):
    pid = row["PATIENT"]
    if pid in ids:
        key = (pid, TAG.sub("", row["DESCRIPTION"].strip()))
        dt = row["START"][:10]
        if key not in last or dt > last[key]:
            last[key] = dt

for r in records:
    pid = r["patient_id"]
    r["reference_date"] = ref.isoformat()
    # encounters
    for e in r["encounters"]:
        e.update(rel(e.pop("last_date")))
    # procedures
    for p in r["procedures_detail"]:
        dt = last.get((pid, p["description"]))
        if dt is None:  # fall back to mid-year of the recorded year
            dt = f"{p['last_year']}-07-01"
        p.pop("last_year")
        p.update(rel(dt))
    # observation summary: replace dates with phrases; keep numeric days as columns
    s = r["observations_summary"]
    v = re.search(r"Vitals \((\d{4}-\d{2}-\d{2})\)", s)
    lb = re.search(r"Labs \((\d{4}-\d{2}-\d{2})\)", s)
    r["vitals_days_ago"] = days_between(v.group(1)) if v else None
    r["labs_days_ago"] = days_between(lb.group(1)) if lb else None
    s = DATE.sub(lambda m: time_ago(days_between(m.group(0))), s)
    r["observations_summary"] = s

COLS = ["patient_id", "age", "marital", "race", "ethnicity", "gender", "city", "state",
        "pregnancy_status", "health_problems", "encounters", "medication_details", "allergies",
        "careplan_details", "procedures_detail", "observations_summary",
        "vitals_days_ago", "labs_days_ago", "reference_date"]
records = [{c: r[c] for c in COLS} for r in records]

with open(out_dir / "patients_final_relative.jsonl", "w") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cell(r, c):
    v = r[c]
    if c == "health_problems":
        return " | ".join(f"{h['description']} [{h['status']}]" for h in v)
    if c == "encounters":
        return " | ".join(f"{h['description']} [{h['time_ago']}]" for h in v)
    if c in ("medication_details", "careplan_details"):
        return " | ".join(h["description"] + (f" for: {'; '.join(h['reasons'])}" if h["reasons"] else "")
                          for h in v)
    if c == "allergies":
        return " | ".join(v)
    if c == "procedures_detail":
        return " | ".join(f"{h['description']} [{h['occurrences']}x, last {h['time_ago']}]" for h in v)
    return "" if v is None else v


with open(out_dir / "patients_final_relative.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(COLS)
    for r in records:
        w.writerow([cell(r, c) for c in COLS])

print(f"{len(records)} patients written to {out_dir} (reference date {ref})")
