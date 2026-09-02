import os
import re
import requests

API_KEY = os.environ.get("DOODSTREAM_API_KEY")

if not API_KEY:
    print("Error: DOODSTREAM_API_KEY environment variable is missing.")
    exit(1)

movies_count = 0
tv_episodes_count = 0
other_count = 0
total_files = 0

# RegEx Patterns
tv_pattern = re.compile(r'(S\d+E\d+|E\d+|\bEpisode\b|\bSeason\b|\bEp\b)', re.IGNORECASE)
movie_pattern = re.compile(r'(\b19\d{2}\b|\b20\d{2}\b|1080p|720p|4k|Bluray|WEB-DL|AMZN)', re.IGNORECASE)

page = 1

while True:
    url = f"https://doodapi.com/api/file/list?key={API_KEY}&page={page}"
    response = requests.get(url)
    
    if response.status_code != 200:
        print(f"Failed to fetch page {page}")
        break

    data = response.json()

    if data.get("status") != 200 or not data.get("result", {}).get("files"):
        break

    files = data["result"]["files"]
    if not files:
        break

    for file in files:
        title = file.get("title", "")
        total_files += 1

        if tv_pattern.search(title):
            tv_episodes_count += 1
        elif movie_pattern.search(title):
            movies_count += 1
        else:
            other_count += 1

    # Check if there are more pages
    total_pages = data.get("result", {}).get("total_pages", 1)
    if page >= total_pages:
        break

    page += 1

print("==========================================")
print(f"Total Videos Scanned: {total_files}")
print(f"Movies: {movies_count} | TV Episodes: {tv_episodes_count} | Other: {other_count}")
print("==========================================")
