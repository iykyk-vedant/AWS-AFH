import sys, os
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

token = os.getenv("SLACK_BOT_TOKEN")
c = WebClient(token=token)

channel_id = os.getenv("SLACK_TEST_CHANNEL_ID", "")

# Try joining
print("1. Trying to join channel...")
try:
    r = c.conversations_join(channel=channel_id)
    print(f"   Joined: {r.get('ok')}")
except SlackApiError as e:
    print(f"   Join error: {e.response['error']}")

# Try posting
print("2. Trying to post message...")
try:
    r = c.chat_postMessage(channel=channel_id, text="[Amaze on Work] Test message - system online")
    print(f"   Posted: ok={r.get('ok')}, ts={r.get('ts')}")
except SlackApiError as e:
    print(f"   Post error: {e.response['error']}")
    # Check scopes
    print(f"   Required scopes: chat:write, channels:join")
    print(f"   Tip: Add bot to channel manually via Slack: /invite @amaze_on_work")
