import pandas as pd
from collections import Counter

# 读取CSV文件
input_file = '/Users/youbo/Desktop/remote-jobs/image-recognize/data/category.csv'
output_file = '/Users/youbo/Desktop/remote-jobs/image-recognize/data/category_updated.csv'

# 读取数据
df = pd.read_csv(input_file)

print("=" * 80)
print("原始数据信息:")
print("=" * 80)
print(f"总记录数: {len(df)}")
print(f"\n前5条记录:")
print(df.head())
print(f"\ngroup_name列的空值数量: {df['group_name'].isna().sum()}")

# 从path中提取一级类别
# 使用 " > " 作为分隔符，取第一个部分
df['group_name'] = df['path'].str.split(' > ').str[0]

print("\n" + "=" * 80)
print("处理后的数据信息:")
print("=" * 80)
print(f"\n前10条记录（显示更新后的group_name）:")
print(df.head(10)[['category_id', 'path', 'group_name']])

# 统计信息
print("\n" + "=" * 80)
print("数据统计:")
print("=" * 80)

# 1. 不同group_name的数量
unique_groups = df['group_name'].nunique()
print(f"\n1. 不同的一级类别（group_name）数量: {unique_groups}")

# 2. 每个group对应的数据条目数量
group_counts = df['group_name'].value_counts().sort_values(ascending=False)
print(f"\n2. 每个一级类别对应的数据条目数量:")
print("-" * 80)
for group_name, count in group_counts.items():
    percentage = (count / len(df)) * 100
    print(f"{group_name:<40} {count:>6} 条 ({percentage:>6.2f}%)")

# 3. 额外统计信息
print("\n" + "=" * 80)
print("其他统计信息:")
print("=" * 80)
print(f"最大类别条目数: {group_counts.max()}")
print(f"最小类别条目数: {group_counts.min()}")
print(f"平均每个类别的条目数: {group_counts.mean():.2f}")
print(f"中位数: {group_counts.median():.0f}")

# 4. Top 10 一级类别
print("\n" + "=" * 80)
print("Top 10 数据最多的一级类别:")
print("=" * 80)
for i, (group_name, count) in enumerate(group_counts.head(10).items(), 1):
    percentage = (count / len(df)) * 100
    print(f"{i:2d}. {group_name:<40} {count:>6} 条 ({percentage:>6.2f}%)")

# 5. 保存更新后的数据
df.to_csv(output_file, index=False, encoding='utf-8')
print("\n" + "=" * 80)
print(f"✓ 数据已保存到: {output_file}")
print("=" * 80)

# 6. 显示一些示例，验证更新是否正确
print("\n验证示例（随机选取5条）:")
print("-" * 80)
sample_df = df.sample(min(5, len(df)))
for idx, row in sample_df.iterrows():
    print(f"\nPath: {row['path']}")
    print(f"Group: {row['group_name']}")
