import json
from pathlib import Path

DATASET_PATH = Path("data/data_set.json")
IMAGE_DIR = Path("data/images")


def load_dataset():
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_dataset(dataset):
    print("=" * 60)
    print("DENSO DATASET VALIDATION")
    print("=" * 60)

    print(f"JSON records: {len(dataset)}")

    missing_images = []

    for item in dataset:
        image_name = Path(item["image_path"]).name
        image_path = IMAGE_DIR / image_name

        if not image_path.exists():
            missing_images.append(image_name)

    if missing_images:
        print("\nMissing images:")
        for image in missing_images:
            print(f"  - {image}")
    else:
        print("All referenced images found: OK")

    print("\nQuestion types:")

    question_types = {}

    for item in dataset:
        for qtype in item["question_type"]:
            question_types[qtype] = question_types.get(qtype, 0) + 1

    for qtype, count in sorted(question_types.items()):
        print(f"  {qtype}: {count}")

    print("\nDocuments:")

    doc_ids = sorted(set(item["docId"] for item in dataset))

    print(f"Unique docId: {len(doc_ids)}")

    print("=" * 60)


if __name__ == "__main__":
    dataset = load_dataset()
    validate_dataset(dataset)
