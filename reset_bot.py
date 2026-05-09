import requests
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")

# Delete webhook to clear any conflicts
url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true"
response = requests.get(url)
print(f"Delete webhook response: {response.json()}")

# Get bot info to verify token works
url = f"https://api.telegram.org/bot{TOKEN}/getMe"
response = requests.get(url)
print(f"Bot info: {response.json()}")
