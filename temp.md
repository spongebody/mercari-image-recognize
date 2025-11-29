好的。还需要做下小的调整：
1. 不需要做「字符串预筛选」，直接将所有一级类目对应的条目数据都喂给LLM；
2. 我的类别数据集最终格式为：
```
category_id,category_name,group_name
001,CD・DVD・ブルーレイ > CD > K-POP・アジア,CD・DVD・ブルーレイ
002,CD・DVD・ブルーレイ > CD > その他,CD・DVD・ブルーレイ
003,CD・DVD・ブルーレイ > CD > アニメ,CD・DVD・ブルーレイ
...
```
得到一级分类后，根据group_name来获取其所有对应的条目，然后喂给LLM，这里不需要将category_id喂给LLM，减少token消耗。等LLM返回top3的分类后，验证分类是否来自原数据集，验证通过后再去数据集里获取对应的category_id，最终一起返回。
