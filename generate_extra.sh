#!/usr/bin/env bash
# Extra batch for the mock patient profiles:
#   A) young adults aged 18-29 (any gender), 10 per state
#   B) pregnant women aged 18-45, kept by preprocessing/keep_pregnant.json, 3 per state
# Appends to the existing CSV/FHIR output (CSV append mode writes no headers, so the
# headers added earlier by add_csv_headers.py stay valid).
# Usage: ./generate_extra.sh [outdir=./output_diverse]
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-./output_diverse}"
STATES=(Massachusetts California Texas Mississippi "New York" Alaska Hawaii Florida Arizona Illinois Georgia Washington)
COMMON=(--exporter.baseDirectory="$OUT" --exporter.fhir.export=true --exporter.csv.export=true
        --exporter.csv.append_mode=true --exporter.csv.excluded_files=patient_expenses.csv
        --generate.append_numbers_to_person_names=false)
seed=2000
for state in "${STATES[@]}"; do
  seed=$((seed + 1)); echo ">>> young adults 18-29: $state seed=$seed"
  ./run_synthea -s "$seed" -p 10 -a 18-29 "${COMMON[@]}" "$state"
  seed=$((seed + 1)); echo ">>> pregnant 18-45: $state seed=$seed"
  ./run_synthea -s "$seed" -p 3 -g F -a 18-45 -k preprocessing/keep_pregnant.json "${COMMON[@]}" "$state"
done
echo "Done."
