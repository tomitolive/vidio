import os
import requests
import re

DOOD_API_KEY = os.getenv("DOODSTREAM_API_KEY")

def fetch_all_videos():
    if not DOOD_API_KEY:
        print("Error: DOODSTREAM_API_KEY is missing.")
        return

    url = f"https://doodapi.com/api/file/list?key={DOOD_API_KEY}"
    try:
        response = requests.get(url).json()
    except Exception as e:
        print(f"Error connecting to DoodStream API: {e}")
        return

    if response.get("status") != 200:
        print(f"API Error: {response.get('msg')}")
        return

    files = response.get("result", {}).get("files", [])
    
    print("\n" + "="*50)
    print(f" Total Videos Found: {len(files)}")
    print("="*50 + "\n")

    for idx, f in enumerate(files, 1):
        title = f.get("title", "Unknown")
        code = f.get("file_code", "N/A")
        
        # تصنيف أولي حسب الاسم
        if re.search(r'(الحلقة|حلقة|S\d+E\d+|E\d+)', title, re.IGNORECASE):
            v_type = "TV Series Episode"
        else:
            v_type = "Movie"

        print(f"{idx}. [{v_type}] {title}")
        print(f"   Code: {code}\n")

if __name__ == "__main__":
    fetch_all_videos()
  
