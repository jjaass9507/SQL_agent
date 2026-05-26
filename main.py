import os
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("ANTHROPIC_API_KEY"):
    raise SystemExit("錯誤：請設定環境變數 ANTHROPIC_API_KEY（參考 .env.example）")

from agents.orchestrator import Orchestrator

if __name__ == "__main__":
    Orchestrator().run()
