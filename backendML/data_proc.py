import json
import csv
from pathlib import Path

FOLDER = Path("./DatasetsForML/")  # папка с JSON-файлами

for input_file in FOLDER.glob("*.json"):
    output_file = input_file.with_suffix(".csv")

    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)

    print(f"[OK] {input_file.name} -> {output_file.name}  ({len(data)} строк)")