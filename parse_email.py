import json
from pprint import pprint

from custom_langchain import OllamaChatModel

print("parsing emails")


data = {}
with open("mails.json", "r", encoding="utf-8") as f:
    ollama_chat = OllamaChatModel("lfm2.5-thinking:latest")
    json_data = f.read()
    data = json.loads(json_data)
    for mail in data[:4]:
        summary = ollama_chat.summarize(json.dumps(mail))
        pprint(summary.content)
    print(len(data))
