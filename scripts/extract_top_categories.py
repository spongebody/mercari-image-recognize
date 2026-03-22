import csv
from collections import Counter

csv_path = "data/rdx_category.csv"

top_categories = []

with open(csv_path, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        path = row["path_name_jp"]
        top = path.split(">")[0].strip()
        top_categories.append(top)

counter = Counter(top_categories)
unique_cats = sorted(counter.keys())

print("=" * 50)
print("一级分类列表（格式参考）：")
print("=" * 50)
for i, cat in enumerate(unique_cats, 1):
    print(f"   {i}. {cat}")

print()
print("=" * 50)
print(f"统计信息：共 {len(unique_cats)} 个一级分类，合计 {sum(counter.values())} 条记录")
print("=" * 50)
print()
print("各一级分类包含的子分类数量：")
for cat in unique_cats:
    print(f"  {cat}：{counter[cat]} 条")
