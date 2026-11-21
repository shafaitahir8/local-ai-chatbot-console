import os
import requests

API_KEY = os.environ["GEMINI_API_KEY"]  # set in your environment; never commit keys
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + API_KEY

headers = {
    "Content-Type": "application/json"
}

payload = {
    "contents": [
        {"parts": [{"text": "Say hello from Gemini Flash!"}]}
    ]
}

response = requests.post(ENDPOINT, headers=headers, json=payload)

print("Status Code:", response.status_code)
print("Response:", response.text)
