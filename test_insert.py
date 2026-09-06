import os, dotenv, supabase
dotenv.load_dotenv("/home/tomito/Desktop/vidio/.env")
client = supabase.create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
data = {
    "tmdb_id": 9001111,
    "series_title": "Test Series",
    "season_number": 1,
    "episode_number": 1,
    "title": "Test",
    "filecode": "t5st",
    "doodstream_url": "https://dood/e/t5st"
}
try:
    res = client.table("tv_episodes").insert(data).execute()
    print("Inserted successfully:", res.data)
except Exception as e:
    print("Exception during insert:", e)
