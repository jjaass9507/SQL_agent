import os
from dotenv import load_dotenv

load_dotenv()

for _var in ("PENSIEVE_TOKEN", "PENSIEVE_EMPNO"):
    if not os.getenv(_var):
        raise SystemExit(f"錯誤：請設定環境變數 {_var}（參考 .env.example）")

from agents.orchestrator import Orchestrator

if __name__ == "__main__":
    Orchestrator().run()
