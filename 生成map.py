import pandas as pd
import os
import re
import json

# 專案根目錄
BASE_DIR = "./"
excel_path = os.path.join(BASE_DIR, 'data', '完整型錄資料.xlsx')
output_json = os.path.join(BASE_DIR, 'static', 'properties.json')

# 讀取 Excel
df = pd.read_excel(excel_path)

# 經緯度擷取
def extract_coords(url):
    try:
        match = re.search(r'google=([\d.]+)\s*,\s*([\d.]+)', str(url))
        if match:
            return float(match.group(1)), float(match.group(2))
    except:
        pass
    return None, None

properties = []

for _, row in df.iterrows():
    lat, lng = extract_coords(row.get('地圖連結', ''))
    edm_url = str(row.get('網址', '')).strip()
    
    area_full = str(row.get('區域', '')).strip()
    area_match = re.search(r'([\u4e00-\u9fa5]{1,4}區)', area_full)
    area = area_match.group(1) if area_match else area_full

    layout_raw = str(row.get('房/廳/衛', '')).strip()
    layout_match = re.match(r'\s*(\d+)', layout_raw)
    layout_num = int(layout_match.group(1)) if layout_match else 0

    if lat is not None and lng is not None:
        properties.append({
            "name": str(row.get('房屋標題', '')).strip(),
            "price": str(row.get('委託總價', '')).strip(),
            "area": area,
            "type_status": str(row.get('類型/現況', '')).strip(),
            "layout": layout_raw,
            "layout_num": layout_num,  # 🔹 新增純數字房數，方便排序
            "age": str(row.get('屋齡', '')).strip(),
            "lat": lat,
            "lng": lng,
            "edm_id": str(row.get('物件編號', '')).strip(),
        })

# 儲存成 JSON 檔
os.makedirs(os.path.dirname(output_json), exist_ok=True)
with open(output_json, "w", encoding="utf-8") as f:
    json.dump(properties, f, ensure_ascii=False, indent=2)

print(f"✅ properties.json 已生成：{output_json}（共 {len(properties)} 筆）")
