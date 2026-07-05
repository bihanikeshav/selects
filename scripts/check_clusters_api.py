"""Verify the /api/clusters endpoint."""
import json
import urllib.request

url = "http://127.0.0.1:8000/api/clusters"
try:
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())
    print(f"Total photos in clusters: {data['total']}")
    print(f"Number of clusters: {len(data['clusters'])}")
    print()
    for c in data["clusters"]:
        print(f"  {c['tag']:15s}: {c['count']:4d} photos  cover={c['cover_sha256'][:16]}...")
except Exception as e:
    print(f"Error: {e}")
