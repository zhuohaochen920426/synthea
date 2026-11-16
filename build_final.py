#!/usr/bin/env python3
"""Build the compact, agent-ready patient profiles from Synthea CSV output.

Usage: build_final.py <csv_dir> <out_dir> [reference_date=YYYY-MM-DD]

Profiles kept: alive-in-simulation adults (18+).
Dropped as "odd": under 18, died during the simulation, blank marital status at age 28+.
Adults aged 18-27 have no marital status in Synthea (it is assigned at age 28); they are
kept with marital = "S" (single), which is an assumption.

Shortening rules:
- SNOMED type tags like "(disorder)" are stripped from descriptions.
- health_problems: ongoing + resolved within the last 2 years.
- encounters: 5 most recent distinct encounter types, with last date.
- medication_details / careplan_details: ongoing only (with reasons).
- allergies: distinct, minus the generic "Allergic disposition" placeholder.
- procedures_detail: non-routine procedures from the last 10 years,
  8 most recent, with count and last year. Routine screenings, assessments,
  education, referrals, exams etc. are excluded.
- observations_summary: one short text (latest vitals, key labs, social, flags).
"""
import csv, json, re, sys, datetime, pathlib
from collections import defaultdict

csv_dir = pathlib.Path(sys.argv[1])
out_dir = pathlib.Path(sys.argv[2])
ref = datetime.date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else datetime.date.today()
out_dir.mkdir(parents=True, exist_ok=True)

TAG = re.compile(r"\s*\((finding|disorder|procedure|regime/therapy|record artifact|situation|"
                 r"substance|organism|physical object|observable entity|morphologic abnormality|person)\)$")
ROUTINE = re.compile(r"screen|assess|reconcil|educat|referral|examin|consult|counsel|discuss|evaluat|"
                     r"review|auscult|questionnaire|test|monitor|measure|record|interview|check|x-ray|"
                     r"history taking|inspection|palpation|scale|plan of care|prone position", re.I)


def clean(d):
    return TAG.sub("", d.strip())


def to_date(s):
    return datetime.date.fromisoformat(s[:10])


def active(stop):
    return not stop or to_date(stop) > ref


def age_on(b):
    return ref.year - b.year - ((ref.month, ref.day) < (b.month, b.day))


def rows(name):
    return csv.DictReader(open(csv_dir / f"{name}.csv", newline=""))


# ---- patients (filter) ----
P = {}
for r in rows("patients"):
    age = age_on(to_date(r["BIRTHDATE"]))
    # Synthea assigns marital status only at age 28, so younger adults have none.
    # Treat them as single ("S"); any adult 28+ with a blank value is dropped.
    marital = r["MARITAL"] or ("S" if age < 28 else "")
    if age < 18 or r["DEATHDATE"] or not marital:
        continue
    P[r["Id"]] = {"patient_id": r["Id"], "age": age, "marital": marital,
                  "race": r["RACE"], "ethnicity": r["ETHNICITY"], "gender": r["GENDER"],
                  "city": r["CITY"], "state": r["STATE"],
                  "pregnancy_status": "not pregnant" if r["GENDER"] == "F" else "not applicable"}

# ---- conditions: health problems + pregnancy ----
hp = defaultdict(dict)  # pid -> desc -> (status, last_stop)
for r in rows("conditions"):
    pid, d = r["PATIENT"], r["DESCRIPTION"].strip()
    if pid not in P:
        continue
    if d.startswith("Normal pregnancy") and active(r["STOP"]):
        P[pid]["pregnancy_status"] = "pregnant"
    if d.endswith("(disorder)"):
        status = "ongoing" if active(r["STOP"]) else "resolved"
        stop = datetime.date.max if status == "ongoing" else to_date(r["STOP"])
        prev = hp[pid].get(d)
        if not prev or stop > prev[1]:
            hp[pid][d] = (status, stop)
for pid in P:
    keep = [(d, s, stop) for d, (s, stop) in hp[pid].items()
            if s == "ongoing" or (ref - stop).days <= 2 * 365]
    keep.sort(key=lambda x: (x[1] != "ongoing", -x[2].toordinal()))
    P[pid]["health_problems"] = [{"description": clean(d), "status": s} for d, s, _ in keep]

# ---- encounters: 5 most recent distinct ----
enc = defaultdict(dict)
for r in rows("encounters"):
    pid = r["PATIENT"]
    if pid in P:
        d, dt = clean(r["DESCRIPTION"]), to_date(r["START"])
        if d not in enc[pid] or dt > enc[pid][d]:
            enc[pid][d] = dt
for pid in P:
    top = sorted(enc[pid].items(), key=lambda x: -x[1].toordinal())[:5]
    P[pid]["encounters"] = [{"description": d, "last_date": dt.isoformat()} for d, dt in top]


# ---- medications / care plans: ongoing only, with reasons ----
def ongoing(table):
    out = defaultdict(dict)
    for r in rows(table):
        pid, d = r["PATIENT"], r["DESCRIPTION"].strip()
        if pid not in P or not d or not active(r["STOP"][:10] if r["STOP"] else ""):
            continue
        e = out[pid].setdefault(d, {"description": clean(d), "reasons": []})
        rd = clean(r["REASONDESCRIPTION"])
        if rd and rd not in e["reasons"]:
            e["reasons"].append(rd)
    return out


meds, plans = ongoing("medications"), ongoing("careplans")
for pid in P:
    P[pid]["medication_details"] = list(meds[pid].values())
    P[pid]["careplan_details"] = list(plans[pid].values())

# ---- allergies ----
al = defaultdict(list)
for r in rows("allergies"):
    pid, d = r["PATIENT"], clean(r["DESCRIPTION"])
    if pid in P and not d.startswith("Allergic disposition") and d not in al[pid]:
        al[pid].append(d)
for pid in P:
    P[pid]["allergies"] = al[pid]

# ---- procedures: notable, last 10 years, 8 most recent ----
pr = defaultdict(dict)
for r in rows("procedures"):
    pid, d = r["PATIENT"], r["DESCRIPTION"].strip()
    if pid not in P or ROUTINE.search(d):
        continue
    dt = to_date(r["START"])
    if (ref - dt).days > 10 * 365:
        continue
    n, last = pr[pid].get(d, (0, dt))
    pr[pid][d] = (n + 1, max(last, dt))
for pid in P:
    top = sorted(pr[pid].items(), key=lambda x: -x[1][1].toordinal())[:8]
    P[pid]["procedures_detail"] = [{"description": clean(d), "occurrences": n, "last_year": last.year}
                                   for d, (n, last) in top]

# ---- observations: latest value per key observation ----
KEY = {  # description prefix -> short label
    "Systolic Blood Pressure": "sbp", "Diastolic Blood Pressure": "dbp", "Heart rate": "hr",
    "Respiratory rate": "rr", "Body mass index (BMI) [Ratio]": "bmi", "Body Weight": "weight",
    "Body Height": "height", "Pain severity": "pain", "Oxygen saturation in Arterial blood": "spo2",
    "Body temperature": "temp",
    "Hemoglobin A1c/Hemoglobin.total in Blood": "hba1c", "Glucose [Mass/volume] in Blood": "glucose",
    "Creatinine [Mass/volume] in Blood": "creatinine",
    "Glomerular filtration rate": "egfr", "Cholesterol [Mass/volume] in Serum or Plasma": "chol",
    "Cholesterol in LDL": "ldl", "Cholesterol in HDL": "hdl", "Triglyceride [Mass/volume]": "trig",
    "Hemoglobin [Mass/volume] in Blood": "hgb",
    "Tobacco smoking status": "tobacco", "Stress level": "stress",
}
latest = defaultdict(dict)  # pid -> key -> (date, value, units)
for r in rows("observations"):
    pid = r["PATIENT"]
    if pid not in P:
        continue
    d = r["DESCRIPTION"]
    for prefix, key in KEY.items():
        if d.startswith(prefix):
            dt = to_date(r["DATE"])
            if key not in latest[pid] or dt >= latest[pid][key][0]:
                latest[pid][key] = (dt, r["VALUE"], r["UNITS"])
            break


def num(v):
    try:
        return float(v)
    except ValueError:
        return None


def fmt(v, units=""):
    f = num(v)
    s = f"{f:.1f}".rstrip("0").rstrip(".") if f is not None else clean(v)
    units = {"Cel": "°C"}.get(units, units)
    if f is None:
        return s
    return f"{s}{units}" if units.startswith("/") else f"{s} {units}".strip()


def summary(pid):
    L, parts, flags = latest[pid], [], []
    get = lambda k: L.get(k)
    vit = []
    if get("sbp") and get("dbp"):
        vit.append(f"BP {fmt(get('sbp')[1])}/{fmt(get('dbp')[1])} mmHg")
        if num(get("sbp")[1]) >= 140 or num(get("dbp")[1]) >= 90:
            flags.append("high blood pressure")
    for k, lab in (("hr", "HR"), ("rr", "RR"), ("spo2", "SpO2"), ("temp", "temp"), ("bmi", "BMI"),
                   ("weight", "weight"), ("height", "height"), ("pain", "pain")):
        if get(k):
            vit.append(f"{lab} {fmt(get(k)[1], get(k)[2] if k != 'pain' else '/10')}")
    if get("bmi"):
        b = num(get("bmi")[1])
        if b is not None and b >= 30:
            flags.append("obese BMI")
        elif b is not None and b < 18.5:
            flags.append("underweight BMI")
    if vit:
        vd = max(get(k)[0] for k in ("sbp", "hr", "bmi", "weight", "pain") if get(k))
        parts.append(f"Vitals ({vd}): " + "; ".join(vit))
    labs = []
    for k, lab in (("hba1c", "HbA1c"), ("glucose", "glucose"), ("creatinine", "creatinine"),
                   ("egfr", "eGFR"), ("chol", "total cholesterol"), ("ldl", "LDL"), ("hdl", "HDL"),
                   ("trig", "triglycerides"), ("hgb", "hemoglobin")):
        if get(k):
            labs.append(f"{lab} {fmt(get(k)[1], get(k)[2])}")
    if get("hba1c") and num(get("hba1c")[1]) is not None:
        a = num(get("hba1c")[1])
        if a >= 6.5:
            flags.append("HbA1c in diabetic range")
        elif a >= 5.7:
            flags.append("HbA1c in prediabetic range")
    if get("ldl") and (num(get("ldl")[1]) or 0) >= 160:
        flags.append("high LDL")
    if get("egfr") and num(get("egfr")[1]) is not None and num(get("egfr")[1]) < 60:
        flags.append("reduced kidney function (eGFR<60)")
    if labs:
        ld = max(get(k)[0] for k in ("hba1c", "glucose", "creatinine", "egfr", "chol", "ldl", "hdl", "trig", "hgb")
                 if get(k))
        parts.append(f"Labs ({ld}): " + "; ".join(labs))
    soc = [f"{lab}: {fmt(get(k)[1])}" for k, lab in (("tobacco", "tobacco"), ("stress", "stress")) if get(k)]
    if soc:
        parts.append("Social: " + "; ".join(soc))
    if flags:
        parts.append("Flags: " + "; ".join(flags))
    return ". ".join(parts)


for pid in P:
    P[pid]["observations_summary"] = summary(pid)

COLS = ["patient_id", "age", "marital", "race", "ethnicity", "gender", "city", "state",
        "pregnancy_status", "health_problems", "encounters", "medication_details", "allergies",
        "careplan_details", "procedures_detail", "observations_summary"]
records = [{c: p[c] for c in COLS} for p in P.values()]

with open(out_dir / "patients_final.jsonl", "w") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cell(r, c):
    v = r[c]
    if c == "health_problems":
        return " | ".join(f"{h['description']} [{h['status']}]" for h in v)
    if c == "encounters":
        return " | ".join(f"{h['description']} [{h['last_date']}]" for h in v)
    if c in ("medication_details", "careplan_details"):
        return " | ".join(h["description"] + (f" for: {'; '.join(h['reasons'])}" if h["reasons"] else "")
                          for h in v)
    if c == "allergies":
        return " | ".join(v)
    if c == "procedures_detail":
        return " | ".join(f"{h['description']} [{h['occurrences']}x, last {h['last_year']}]" for h in v)
    return v


with open(out_dir / "patients_final.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(COLS)
    for r in records:
        w.writerow([cell(r, c) for c in COLS])

print(f"{len(records)} patients written to {out_dir} (reference date {ref})")
