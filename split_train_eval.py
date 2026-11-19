#!/usr/bin/env python3
"""Split the mock patient profiles into train and eval sets (50/50, stratified).

Usage: split_train_eval.py <artifact_dir> <out_dir> [seed=42]

Each patient goes to exactly one split. The split is stratified on
(pregnancy_status, gender, age band) so both halves have the same mix.
The same assignment is applied to both versions of the data
(absolute dates and relative dates), so a patient is in the same split in both.

Outputs in <out_dir>:
  split_assignment.csv                       patient_id,split
  patients_final_{train,eval}.{csv,jsonl}
  patients_final_relative_{train,eval}.{csv,jsonl}
"""
import csv, json, random, sys, pathlib
from collections import defaultdict

art = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42
out.mkdir(parents=True, exist_ok=True)

BANDS = [(18, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 79), (80, 89)]


def band(age):
    for lo, hi in BANDS:
        if lo <= age <= hi:
            return f"{lo}-{hi}"
    return "other"


records = [json.loads(l) for l in open(art / "patients_final.jsonl")]
strata = defaultdict(list)
for r in records:
    strata[(r["pregnancy_status"], r["gender"], band(r["age"]))].append(r["patient_id"])

rng = random.Random(seed)
assign = {}
flip = 0  # odd-sized strata alternate which split gets the extra patient
for key in sorted(strata):
    ids = sorted(strata[key])
    rng.shuffle(ids)
    half = len(ids) // 2
    for i in ids[:half]:
        assign[i] = "train"
    for i in ids[half:2 * half]:
        assign[i] = "eval"
    if len(ids) % 2:
        assign[ids[-1]] = "train" if flip == 0 else "eval"
        flip ^= 1

with open(out / "split_assignment.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["patient_id", "split"])
    for r in records:
        w.writerow([r["patient_id"], assign[r["patient_id"]]])

for stem in ("patients_final", "patients_final_relative"):
    rows = [json.loads(l) for l in open(art / f"{stem}.jsonl")]
    with open(art / f"{stem}.csv", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        csv_rows = list(reader)
    assert [r["patient_id"] for r in rows] == [c[0] for c in csv_rows], "CSV/JSONL order mismatch"
    for split in ("train", "eval"):
        with open(out / f"{stem}_{split}.jsonl", "w") as f:
            for r in rows:
                if assign[r["patient_id"]] == split:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(out / f"{stem}_{split}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            for c in csv_rows:
                if assign[c[0]] == split:
                    w.writerow(c)

n = {s: sum(1 for v in assign.values() if v == s) for s in ("train", "eval")}
print(f"train={n['train']} eval={n['eval']} (seed {seed}) written to {out}")
