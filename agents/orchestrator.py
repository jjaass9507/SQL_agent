from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents.interviewer import Interviewer
from models.schema import TableSpec
from models.session import Phase, SessionState

console = Console()

CONFIRM_WORDS = {"ok", "確認", "yes", "confirm", "好", "可以", "沒問題"}


def _is_confirmation(text: str) -> bool:
    return text.strip().lower() in CONFIRM_WORDS


def _print_summary(tables: list[TableSpec]) -> None:
    console.print("\n[bold cyan]── 需求摘要 ──────────────────────────────[/bold cyan]")
    for t in tables:
        tbl = Table(title=f"[bold]{t.table_name}[/bold]  {t.description}", show_lines=True)
        tbl.add_column("欄位", style="green")
        tbl.add_column("型態")
        tbl.add_column("NULL")
        tbl.add_column("說明")
        for c in t.columns:
            flags = []
            if c.is_primary_key:
                flags.append("PK")
            if c.is_foreign_key:
                flags.append(f"FK→{c.references}")
            if c.is_unique:
                flags.append("UNIQUE")
            tbl.add_row(
                c.name,
                c.data_type + (f"({c.length})" if c.length else ""),
                "Y" if c.nullable else "N",
                c.description + (" [" + ", ".join(flags) + "]" if flags else ""),
            )
        console.print(tbl)
    console.print()


class Orchestrator:
    def __init__(self):
        self._state = SessionState()
        self._interviewer = Interviewer()

    def run(self) -> None:
        console.print(Panel(
            "[bold]資料庫建檔管理 Agent[/bold]\n"
            "請描述您想建立的資料表需求，Agent 會協助您完善細節。\n"
            "輸入 [bold cyan]exit[/bold cyan] 可隨時離開。",
            border_style="cyan",
        ))

        while self._state.phase != Phase.DONE:
            try:
                user_input = console.input("[bold green]您>[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]已結束。[/dim]")
                break

            if not user_input:
                continue
            if user_input.lower() == "exit":
                console.print("[dim]已結束。[/dim]")
                break

            if self._state.phase == Phase.COLLECTING:
                self._handle_collecting(user_input)

            elif self._state.phase == Phase.CONFIRMING:
                if _is_confirmation(user_input):
                    self._state.phase = Phase.GENERATING
                    self._generate_outputs()
                    self._state.phase = Phase.DONE
                else:
                    # User wants changes — go back to collecting
                    console.print("[yellow]好的，請繼續說明修改內容。[/yellow]\n")
                    self._state.phase = Phase.COLLECTING
                    self._handle_collecting(user_input)

    def _handle_collecting(self, user_input: str) -> None:
        with console.status("[dim]思考中...[/dim]", spinner="dots"):
            text, tables = self._interviewer.chat(user_input)

        if text:
            console.print(f"\n[bold blue]Agent>[/bold blue] {text}\n")

        if tables:
            self._state.tables = tables
            _print_summary(tables)
            console.print(
                "[bold yellow]需求已收集完整！[/bold yellow]\n"
                "請確認以上摘要，輸入 [bold cyan]OK[/bold cyan] 開始產生文件，"
                "或直接說明需要修改的地方。\n"
            )
            self._state.phase = Phase.CONFIRMING

    def _generate_outputs(self) -> None:
        from agents.writers.spec_writer import SpecWriter
        from agents.writers.diagram_writer import DiagramWriter
        from agents.writers.ddl_writer import DDLWriter
        from agents.writers.security_writer import SecurityWriter
        from utils.file_writer import create_session_dir, write_outputs

        session_dir = create_session_dir()
        console.print(f"\n[bold cyan]開始產生文件至 {session_dir} ...[/bold cyan]\n")

        outputs: dict[str, str] = {}
        writers = [
            ("01_specification.md", "規格書與資料字典", SpecWriter()),
            ("02_er_diagram.md", "結構與關聯圖", DiagramWriter()),
            ("03_ddl.sql", "DDL 腳本", DDLWriter()),
            ("04_security_plan.md", "效能與安全規劃書", SecurityWriter()),
        ]

        failed = []
        for filename, label, writer in writers:
            with console.status(f"[dim]產生{label}...[/dim]", spinner="dots"):
                content = writer.generate(self._state.tables)
            if content and content.strip():
                outputs[filename] = content
                console.print(f"  [green]✓[/green] {label}  ({filename})")
            else:
                failed.append(label)
                console.print(f"  [red]✗[/red] {label} 產出失敗（API 無回應）")

        write_outputs(session_dir, outputs)

        if failed:
            console.print(f"\n[yellow]警告：{', '.join(failed)} 未能產出，請檢查 API 連線後重試。[/yellow]")

        console.print(Panel(
            "[bold green]文件產生完成！[/bold green]\n\n"
            + "\n".join(f"  • {session_dir}/{f}" for f in outputs),
            border_style="green",
        ))
