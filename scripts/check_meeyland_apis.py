import httpx, re, json

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36'}

urls = [
    'https://meeyland.com/mua-ban-nha-dat',
    'https://meeyland.com/cho-thue-nha-dat',
    'https://meeyland.com/sang-nhuong-nha-dat',
    'https://meeyland.com/du-an',
    'https://meeyland.com/can-ho-chung-cu',
]

for u in urls:
    try:
        r = httpx.get(u, headers=headers, timeout=10.0, follow_redirects=True)
        print(f"\nURL: {u} -> Status: {r.status_code}")
        cat_matches = re.findall(r'"category":\s*"([0-9a-f]{24})"', r.text)
        type_matches = re.findall(r'"type":\s*"([0-9a-f]{24})"', r.text)
        api_matches = re.findall(r'https://api[0-9]?\.meeyland\.com/[a-zA-Z0-9/_?-]+', r.text)
        print(f"  Categories: {set(cat_matches)}")
        print(f"  Types: {set(type_matches[:3])}")
        print(f"  APIs found: {set(api_matches[:3])}")
    except Exception as e:
        print(f"Error {u}: {e}")

