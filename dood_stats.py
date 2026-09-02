import requests
import re

# حط الـ API Key ديالك هنا بين الجوج علامات التنصيص
DOOD_API_KEY = "576580si8p199m63k5gmvx"

def fetch_all_videos():
    if not DOOD_API_KEY or DOOD_API_KEY == "YOUR_API_KEY_HERE":
        print("Error: DOOD_API_KEY is missing or not set.")
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

    movies_count = 0
    episodes_count = 0

    for idx, f in enumerate(files, 1):
        title = f.get("title", "Unknown")
        code = f.get("file_code", "N/A")
        
        # التمييز بين المسلسلات والأفلام حسب العنوان
        if re.search(r'(الحلقة|حلقة|S\d+E\d+|E\d+)', title, re.IGNORECASE):
            v_type = "TV Series Episode"
            episodes_count += 1
        else:
            v_type = "Movie"
            movies_count += 1

        print(f"{idx}. [{v_type}] {title}")
        print(f"   Code: {code}\n")

    print("="*50)
    print(f" Summary: {movies_count} Movies | {episodes_count} Episodes")
    print("="*50)

if __name__ == "__main__":
    fetch_all_videos()
