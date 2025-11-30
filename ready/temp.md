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

[Unit]
Description=Image Recognize FastAPI service
After=network.target

[Service]
User=root
WorkingDirectory=/root/rdx-ai/mercari-image-recognize

# 如果你使用 .env，它会在程序里被 python-dotenv 读取
Environment="PYTHONUNBUFFERED=1"

# 使用 uv 创建的虚拟环境里的 uvicorn
ExecStart=/root/rdx-ai/mercari-image-recognize/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 39008

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target


sudo tee /etc/nginx/sites-available/image-recognize > /dev/null << 'EOF'
server {
    listen 80;
    server_name 43.133.171.134;  # 修改为你的域名或服务器IP
    
    client_max_body_size 10M;  # 允许上传大文件
    
    location / {
        proxy_pass http://127.0.0.1:39008;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF