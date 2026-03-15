import csv
from pathlib import Path

INPUT_FILE    = Path("./DatasetsForML/wireless-network-card.csv")
OUTPUT_FILE   = Path("./DatasetsForML/wireless-network-card2.csv")
CHECK_COLUMNS = ["price", "protocol", "interface"]  # строки с null в любом из этих столбцов будут удалены

with open(INPUT_FILE, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    data = list(reader)

clean_data = [
    row for row in data
    if all(row[col].strip() != "" for col in CHECK_COLUMNS)
]

removed = len(data) - len(clean_data)

with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(clean_data)

print(f"✅ {INPUT_FILE} → {OUTPUT_FILE}")
print(f"   Столбцы проверки: {CHECK_COLUMNS}")
print(f"   Всего: {len(data)}  |  Удалено: {removed}  |  Осталось: {len(clean_data)}")