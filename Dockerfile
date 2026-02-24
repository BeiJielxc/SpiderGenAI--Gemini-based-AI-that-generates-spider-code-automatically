# 浣跨敤寰蒋瀹樻柟鐨?Playwright Python 闀滃儚 (鑷甫娴忚鍣ㄥ拰Python鐜)
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# 璁剧疆瀹瑰櫒鍐呯殑宸ヤ綔鐩綍
WORKDIR /app

# 1. 鍏堟妸渚濊禆鏂囦欢鎷疯繘鍘?(鍒╃敤缂撳瓨鏈哄埗鍔犻€熸瀯寤?
COPY pygen/requirements.txt ./requirements.txt

# 2. 瀹夎 Python 渚濊禆
# 娉ㄦ剰锛歅laywright 搴撴湰韬凡缁忓湪闀滃儚閲屼簡锛屼絾浣犲彲鑳借繕闇€瑕?requests, beautifulsoup 绛?
RUN pip install --no-cache-dir -r requirements.txt

# 3. 瀹夎娴忚鍣?(杩欎竴姝ラ€氬父闀滃儚鑷甫浜嗭紝浣嗕负浜嗕繚闄╁彲浠ュ姞锛屾垨鑰呭湪浠ｇ爜閲屽姩鎬佸畨瑁?
RUN playwright install chromium

# 4. 鎶婁綘鐨勬墍鏈変唬鐮佹嫹杩涘幓
COPY . .

# 璁剧疆鐜鍙橀噺锛岀‘淇濇棤澶存ā寮忎笅灞忓箷娓叉煋姝ｅ父 (闃叉鏌愪簺鍙嶇埇妫€娴?
ENV HEADLESS=true
ENV PYTHONUNBUFFERED=1

# 瀹瑰櫒鍚姩鏃堕粯璁ゆ墽琛岀殑鍛戒护 (姣斿鍚姩浣犵殑 Agent 鍏ュ彛)
CMD ["python", "pygen/api.py"]
