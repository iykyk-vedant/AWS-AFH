import sys, os
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
from slack_sdk import WebClient
c = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
r = c.conversations_list(types="public_channel", limit=20)
for ch in r.get("channels", []):
    print(f"  {ch['id']}  #{ch['name']}")
