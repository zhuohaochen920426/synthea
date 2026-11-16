#!/usr/bin/env bash
# Generate a demographically and clinically diverse Synthea population.
# Diversity comes from: many states (different race/ethnicity/income/age mixes),
# age bands (pediatric -> geriatric), mixed genders (random per run), and a distinct seed per run.
# Usage: ./generate_diverse.sh [patients_per_run=10] [outdir=./output_diverse]
set -euo pipefail
cd "$(dirname "$0")"

PER_RUN="${1:-10}"
OUT="${2:-./output_diverse}"

STATES=(Massachusetts California Texas Mississippi "New York" Alaska Hawaii Florida Arizona Illinois Georgia Washington)
AGES=(0-17 18-39 40-64 65-90)

seed=1000
runs=0
for state in "${STATES[@]}"; do
  for age in "${AGES[@]}"; do
      seed=$((seed + 1))
      runs=$((runs + 1))
      echo ">>> run $runs: $state age=$age seed=$seed"
      ./run_synthea -s "$seed" -p "$PER_RUN" -a "$age" \
        --exporter.baseDirectory="$OUT" \
        --exporter.fhir.export=true \
        --exporter.csv.export=true \
        --exporter.csv.append_mode=true \
        --exporter.csv.excluded_files=patient_expenses.csv \
        --generate.append_numbers_to_person_names=false \
        "$state"
  done
done
echo "Done: $runs runs x $PER_RUN patients. FHIR in $OUT/fhir, CSV in $OUT/csv"
