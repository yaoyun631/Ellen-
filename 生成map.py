import pandas as pd
import os
import re
import json
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom


# ===== 基本設定 =====
BASE_DIR = "./"

excel_path = os.path.join(BASE_DIR, "data/sale", "完整型錄資料.xlsx")
output_json = os.path.join(BASE_DIR, "static", "properties.json")

# sitemap 輸出資料夾
sitemap_dir = os.path.join(BASE_DIR, "static")

# ✅ 你的正式網址（不要斜線結尾）
BASE_URL = "https://ellenfindhome"


# ===== 讀取 Excel =====
df = pd.read_excel(excel_path)


# ===== 經緯度擷取 =====
def extract_coords(url):
    try:
        match = re.search(r"google=([\d.]+)\s*,\s*([\d.]+)", str(url))
        if match:
            return float(match.group(1)), float(match.group(2))
    except:
        pass
    return None, None


properties = []

for _, row in df.iterrows():
    lat, lng = extract_coords(row.get("地圖連結", ""))
    edm_id = str(row.get("物件編號", "")).strip()

    # 沒物件編號就跳過，避免產生錯誤網址
    if not edm_id:
        continue

    area_full = str(row.get("區域", "")).strip()
    area_match = re.search(r"([\u4e00-\u9fa5]{1,4}區)", area_full)
    area = area_match.group(1) if area_match else area_full

    layout_raw = str(row.get("房/廳/衛", "")).strip()
    layout_match = re.match(r"\s*(\d+)", layout_raw)
    layout_num = int(layout_match.group(1)) if layout_match else 0

    # 保持你原本邏輯：有座標才加入（如果希望所有物件都進 sitemap，可以把這個 if 拿掉）
    if lat is not None and lng is not None:
        properties.append({
            "name": str(row.get("房屋標題", "")).strip(),
            "price": str(row.get("委託總價", "")).strip(),
            "area": area,
            "type_status": str(row.get("類型/現況", "")).strip(),
            "layout": layout_raw,
            "layout_num": layout_num,
            "age": str(row.get("屋齡", "")).strip(),
            "lat": lat,
            "lng": lng,
            "edm_id": edm_id,
        })


# ===== 可選：輸出 properties.json =====
os.makedirs(os.path.dirname(output_json), exist_ok=True)

with open(output_json, "w", encoding="utf-8") as f:
    json.dump(properties, f, ensure_ascii=False, indent=2)

print(f"✅ properties.json 已生成：{output_json}（共 {len(properties)} 筆）")



# ===== 產生 sitemap XML（一份 500 筆） =====

def build_sitemap_xml(url_list):
    """產生漂亮 XML 的函式"""
    urlset = Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    for loc_url in url_list:
        url = SubElement(urlset, "url")
        loc = SubElement(url, "loc")
        loc.text = loc_url

    rough_xml = tostring(urlset, encoding="utf-8")
    reparsed = minidom.parseString(rough_xml)
    pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8")
    return pretty_xml


def build_sitemap_index_xml(sitemap_urls):
    """產生 sitemap_index.xml 的漂亮 XML"""
    sitemapindex = Element("sitemapindex")
    sitemapindex.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    for sm_url in sitemap_urls:
        sm = SubElement(sitemapindex, "sitemap")
        loc = SubElement(sm, "loc")
        loc.text = sm_url

    rough_xml = tostring(sitemapindex, encoding="utf-8")
    reparsed = minidom.parseString(rough_xml)
    pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8")
    return pretty_xml


# 每份 sitemap 最多筆數
CHUNK_SIZE = 500

# 組出所有 URL（依照你的物件頁路徑）
all_urls = [
    f"{BASE_URL}/static/sedm_pages/{p['edm_id']}.html"
    for p in properties
]

os.makedirs(sitemap_dir, exist_ok=True)

sitemap_files = []
sitemap_urls = []  # 存每個 sitemap.xml 對應的完整網址，給 sitemap_index 用

# 分批寫入 sitemap1.xml, sitemap2.xml, ...
for idx in range(0, len(all_urls), CHUNK_SIZE):
    chunk = all_urls[idx:idx + CHUNK_SIZE]
    file_index = idx // CHUNK_SIZE + 1

    xml_bytes = build_sitemap_xml(chunk)

    filename = f"sitemap{file_index}.xml"
    sitemap_filepath = os.path.join(sitemap_dir, filename)
    sitemap_file_url = f"{BASE_URL}/static/{filename}"

    with open(sitemap_filepath, "wb") as f:
        f.write(xml_bytes)

    sitemap_files.append(sitemap_filepath)
    sitemap_urls.append(sitemap_file_url)
    print(f"✅ 已生成 {sitemap_filepath} — {len(chunk)} 筆資料")


# ===== 產生 sitemap_index.xml =====
index_xml_bytes = build_sitemap_index_xml(sitemap_urls)
sitemap_index_path = os.path.join(sitemap_dir, "sitemap_index.xml")

with open(sitemap_index_path, "wb") as f:
    f.write(index_xml_bytes)

print(f"\n✅ sitemap_index.xml 已生成：{sitemap_index_path}")
print("📌 內含以下 sitemap：")
for u in sitemap_urls:
    print("  -", u)

print("\n🎉 全部 sitemap 生成完成！")
print("👉 到 Google Search Console 提交：")
print("   https://ellenfindhome/static/sitemap_index.xml")
