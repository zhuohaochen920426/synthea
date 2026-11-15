#!/usr/bin/env python3
"""Build a compact patient dataset (JSONL + CSV) from Synthea CSV output.

Usage: build_dataset.py <csv_dir> <out_dir> [reference_date=YYYY-MM-DD]

- Treats every patient as alive: age = reference date - birth date.
- Drops patients younger than 10.
- Clinical tables keep only the short DESCRIPTION, de-duplicated per patient
  (first-occurrence order).
"""
import csv, json, sys, datetime, pathlib

csv_dir = pathlib.Path(sys.argv[1])
out_dir = pathlib.Path(sys.argv[2])
ref = datetime.date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else datetime.date.today()
out_dir.mkdir(parents=True, exist_ok=True)

DEMO = ["MARITAL", "RACE", "ETHNICITY", "GENDER", "BIRTHPLACE",
        "CITY", "STATE", "COUNTY", "FIPS", "ZIP", "LAT", "LON"]
CLIN = ["encounters", "medications", "conditions", "procedures",
        "allergies", "observations", "careplans"]


def age_on(birth, ref):
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


patients = {}
for row in csv.DictReader(open(csv_dir / "patients.csv", newline="")):
    age = age_on(datetime.date.fromisoformat(row["BIRTHDATE"]), ref)
    if age < 10:
        continue
    rec = {"patient_id": row["Id"], "age": age}
    rec.update({k.lower(): row[k] for k in DEMO})
    for t in CLIN:
        rec[t] = []
    rec["pregnancy_status"] = ("not pregnant" if row["GENDER"] == "F" else "not applicable")
    rec["health_problems"] = []
    patients[row["Id"]] = rec

for t in CLIN:
    seen = {pid: set() for pid in patients}
    for row in csv.DictReader(open(csv_dir / f"{t}.csv", newline="")):
        pid = row["PATIENT"]
        if pid not in patients:
            continue
        d = row["DESCRIPTION"].strip()
        if d and d not in seen[pid]:
            seen[pid].add(d)
            patients[pid][t].append(d)

# Health problems = conditions tagged "(disorder)", each with a status taken
# from the source STOP date (blank or after the reference date -> ongoing).
# Pregnancy status = "pregnant" if a "Normal pregnancy" condition is active on
# the reference date (female patients only).
def active(stop):
    return not stop or datetime.date.fromisoformat(stop) > ref

problems = {pid: {} for pid in patients}
for row in csv.DictReader(open(csv_dir / "conditions.csv", newline="")):
    pid, d = row["PATIENT"], row["DESCRIPTION"].strip()
    if pid not in patients:
        continue
    if d.startswith("Normal pregnancy") and active(row["STOP"]):
        patients[pid]["pregnancy_status"] = "pregnant"
    if d.endswith("(disorder)"):
        prev = problems[pid].get(d)
        problems[pid][d] = "ongoing" if (prev == "ongoing" or active(row["STOP"])) else "resolved"
for pid, probs in problems.items():
    patients[pid]["health_problems"] = [{"description": d, "status": st} for d, st in probs.items()]

# procedures_detail: per distinct procedure, occurrence count and a recency
# label based on its most recent START date relative to the reference date.
def recency(days):
    if days <= 365:
        return "within last year"
    if days <= 3 * 365:
        return "1-3 years ago"
    if days <= 10 * 365:
        return "3-10 years ago"
    return "more than 10 years ago"

procs = {pid: {} for pid in patients}
for row in csv.DictReader(open(csv_dir / "procedures.csv", newline="")):
    pid, d = row["PATIENT"], row["DESCRIPTION"].strip()
    if pid not in patients or not d:
        continue
    start = datetime.date.fromisoformat(row["START"][:10])
    cnt, last = procs[pid].get(d, (0, start))
    procs[pid][d] = (cnt + 1, max(last, start))
for pid, ps in procs.items():
    patients[pid]["procedures_detail"] = [
        {"description": d, "occurrences": n, "recency": recency((ref - last).days)}
        for d, (n, last) in ps.items()]

# observations_detail: per distinct observation, the most recent value, its
# units and the date it was recorded, plus how many times it was recorded.
obs = {pid: {} for pid in patients}
for row in csv.DictReader(open(csv_dir / "observations.csv", newline="")):
    pid, d = row["PATIENT"], row["DESCRIPTION"].strip()
    if pid not in patients or not d:
        continue
    date = row["DATE"][:10]
    prev = obs[pid].get(d)
    n = prev["recorded_times"] + 1 if prev else 1
    if not prev or date >= prev["last_recorded"]:
        obs[pid][d] = {"description": d, "latest_value": row["VALUE"],
                       "units": row["UNITS"], "last_recorded": date}
    obs[pid][d]["recorded_times"] = n
for pid, od in obs.items():
    patients[pid]["observations_detail"] = list(od.values())

# careplans_detail: per distinct care plan, a status from the source STOP date
# (blank or after the reference date -> ongoing) and the reason(s) recorded.
plans = {pid: {} for pid in patients}
for row in csv.DictReader(open(csv_dir / "careplans.csv", newline="")):
    pid, d = row["PATIENT"], row["DESCRIPTION"].strip()
    if pid not in patients or not d:
        continue
    e = plans[pid].setdefault(d, {"description": d, "status": "resolved", "reasons": []})
    if active(row["STOP"][:10]):
        e["status"] = "ongoing"
    rd = row["REASONDESCRIPTION"].strip()
    if rd and rd not in e["reasons"]:
        e["reasons"].append(rd)
for pid, pl in plans.items():
    patients[pid]["careplans_detail"] = list(pl.values())

# medications_detail: per distinct medication, ongoing if any prescription has a
# blank STOP (or one after the reference date), else resolved; plus reasons.
meds = {pid: {} for pid in patients}
for row in csv.DictReader(open(csv_dir / "medications.csv", newline="")):
    pid, d = row["PATIENT"], row["DESCRIPTION"].strip()
    if pid not in patients or not d:
        continue
    e = meds[pid].setdefault(d, {"description": d, "status": "resolved", "reasons": []})
    if active(row["STOP"][:10]):
        e["status"] = "ongoing"
    rd = row["REASONDESCRIPTION"].strip()
    if rd and rd not in e["reasons"]:
        e["reasons"].append(rd)
for pid, ml in meds.items():
    patients[pid]["medications_detail"] = list(ml.values())

records = list(patients.values())
with open(out_dir / "patients.jsonl", "w") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

cols = ["patient_id", "age"] + [k.lower() for k in DEMO] + ["pregnancy_status", "health_problems"] + CLIN + ["procedures_detail", "observations_detail", "careplans_detail", "medications_detail"]
with open(out_dir / "patients.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for r in records:
        def cell(c):
            if c == "health_problems":
                return " | ".join(f"{h['description']} [{h['status']}]" for h in r[c])
            if c == "procedures_detail":
                return " | ".join(f"{h['description']} [{h['occurrences']}x, {h['recency']}]" for h in r[c])
            if c == "observations_detail":
                return " | ".join(
                    f"{h['description']}: {h['latest_value']}{(' ' + h['units']) if h['units'] else ''}"
                    f" ({h['last_recorded']}, {h['recorded_times']}x)" for h in r[c])
            if c == "medications_detail":
                return " | ".join(
                    f"{h['description']} [{h['status']}]"
                    + (f" for: {'; '.join(h['reasons'])}" if h["reasons"] else "") for h in r[c])
            if c == "careplans_detail":
                return " | ".join(
                    f"{h['description']} [{h['status']}]"
                    + (f" for: {'; '.join(h['reasons'])}" if h["reasons"] else "") for h in r[c])
            return " | ".join(r[c]) if c in CLIN else r[c]
        w.writerow([cell(c) for c in cols])

print(f"{len(records)} patients written to {out_dir} (reference date {ref})")
