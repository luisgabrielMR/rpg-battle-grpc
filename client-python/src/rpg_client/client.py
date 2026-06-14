from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

import grpc
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[2]
PROTO_DIR = PROJECT_ROOT / "proto"
PROTO_FILE = PROTO_DIR / "battle.proto"
GENERATED_DIR = APP_DIR / "generated"

DEFAULT_TARGET = "localhost:50051"
PLAYER_NAME = "Guerreiro"
SAMPLE_FILE = PROJECT_ROOT / "client-python" / "sample-files" / "ficha_heroi.txt"
DOWNLOAD_DIR = PROJECT_ROOT / "client-python" / "downloads"
REQUEST_DIR = PROJECT_ROOT / "client-python" / "generated-requests"
CHUNK_SIZE = 64 * 1024
INVALID_FILE_NAME_CHARS = set('<>:"/\\|?*')
RESERVED_WINDOWS_FILE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def ensure_generated_proto(console: Console | None = None) -> None:
    """Generate Python gRPC stubs when they are missing or older than the proto."""
    generated_files = [
        GENERATED_DIR / "battle_pb2.py",
        GENERATED_DIR / "battle_pb2_grpc.py",
    ]
    proto_mtime = PROTO_FILE.stat().st_mtime
    needs_generation = any(not file.exists() or file.stat().st_mtime < proto_mtime for file in generated_files)

    if not needs_generation:
        return

    try:
        from grpc_tools import protoc
    except ImportError as exc:
        raise RuntimeError(
            "grpcio-tools nao esta instalado. Rode: python -m pip install -r client-python/requirements.txt"
        ) from exc

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / "__init__.py").touch()

    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{PROTO_DIR}",
            f"--python_out={GENERATED_DIR}",
            f"--pyi_out={GENERATED_DIR}",
            f"--grpc_python_out={GENERATED_DIR}",
            str(PROTO_FILE),
        ]
    )

    if result != 0:
        raise RuntimeError(f"Falha ao gerar stubs Python do gRPC. Codigo: {result}")

    if console:
        console.print("[dim]Stubs Python gerados a partir de proto/battle.proto.[/dim]")


def load_grpc_modules() -> tuple[Any, Any]:
    if str(GENERATED_DIR) not in sys.path:
        sys.path.insert(0, str(GENERATED_DIR))

    battle_pb2 = importlib.import_module("battle_pb2")
    battle_pb2_grpc = importlib.import_module("battle_pb2_grpc")
    return battle_pb2, battle_pb2_grpc


def hp_color(percent: float) -> str:
    if percent > 0.55:
        return "green"
    if percent > 0.25:
        return "yellow"
    return "red"


def hp_bar(current: int, maximum: int, width: int = 12) -> Text:
    percent = current / maximum if maximum else 0
    filled = round(percent * width)
    empty = width - filled
    color = hp_color(percent)

    text = Text("HP: ", style="bold")
    text.append("[", style="dim")
    text.append("#" * filled, style=color)
    text.append("-" * empty, style="dim")
    text.append("] ", style="dim")
    text.append(f"{current}/{maximum}", style=color)
    return text


def combatant_panel(combatant: Any, border_style: str) -> Panel:
    title = Text(combatant.name, style="bold")
    status = Text.assemble(
        ("VIVO" if combatant.alive else "DERROTADO", "bold green" if combatant.alive else "bold red"),
    )

    body = Group(
        title,
        Text(combatant.role, style="dim"),
        hp_bar(combatant.hp, combatant.max_hp),
        status,
    )
    return Panel(body, border_style=border_style, box=box.ROUNDED, padding=(1, 2))


def log_panel(state: Any, last_message: str) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(ratio=1)

    if last_message:
        table.add_row(Text(f"[RPC] {last_message}", style="bold cyan"))

    for item in state.log[-6:]:
        table.add_row(Text(f"- {item}", style="white"))

    return Panel(table, title="LOG", border_style="cyan", box=box.ROUNDED)


def actions_panel(state: Any) -> Panel:
    table = Table.grid(padding=(0, 3))
    table.add_column(justify="left")
    table.add_column(justify="left")
    table.add_row("[bold]1[/bold] Atacar", f"[bold]2[/bold] Usar pocao ({state.potions_left})")
    table.add_row("[bold]3[/bold] Atualizar status", "[bold]4[/bold] Reiniciar batalha")
    table.add_row("[bold]0[/bold] Sair", "")
    return Panel(table, title="ACOES", border_style="magenta", box=box.ROUNDED)


def outcome_text(battle_pb2: Any, outcome: int) -> Text:
    if outcome == battle_pb2.PLAYER_WON:
        return Text("VITORIA DO JOGADOR", style="bold green")
    if outcome == battle_pb2.MONSTER_WON:
        return Text("VITORIA DO DRAGAO", style="bold red")
    return Text("BATALHA EM ANDAMENTO", style="bold yellow")


def render_screen(console: Console, battle_pb2: Any, state: Any, last_message: str = "") -> None:
    console.clear()
    console.print(Rule("[bold cyan]STATUS DA BATALHA[/bold cyan]"))
    console.print(
        Align.center(
            Columns(
                [
                    combatant_panel(state.player, "green"),
                    combatant_panel(state.monster, "red"),
                ],
                equal=True,
                expand=True,
            )
        )
    )
    console.print(log_panel(state, last_message))
    console.print(actions_panel(state))
    console.print(Align.center(outcome_text(battle_pb2, state.outcome)))


def call_status(stub: Any, battle_pb2: Any) -> Any:
    return stub.GetStatus(battle_pb2.StatusRequest(), timeout=5)


def safe_remote_file_name(file_name: str) -> str:
    value = str(file_name or "").strip()
    base_name = value.split(".", 1)[0].upper()
    has_invalid_char = any(ord(char) < 32 or char in INVALID_FILE_NAME_CHARS for char in value)

    if (
        not value
        or value in {".", ".."}
        or len(value) > 255
        or has_invalid_char
        or base_name in RESERVED_WINDOWS_FILE_NAMES
        or Path(value).name != value
    ):
        raise ValueError("Nome de arquivo remoto invalido.")
    return value


def file_chunks(battle_pb2: Any, file_path: Path) -> Any:
    sent_any_chunk = False

    with file_path.open("rb") as file:
        while True:
            content = file.read(CHUNK_SIZE)
            if not content:
                break
            sent_any_chunk = True
            yield battle_pb2.FileChunk(file_name=file_path.name, content=content)

    if not sent_any_chunk:
        yield battle_pb2.FileChunk(file_name=file_path.name, content=b"")


def upload_file(file_stub: Any, battle_pb2: Any, file_path: Path) -> Any:
    source = file_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo local nao encontrado: {source}")

    return file_stub.UploadFile(file_chunks(battle_pb2, source), timeout=15)


def list_files(file_stub: Any, battle_pb2: Any) -> Any:
    return file_stub.ListFiles(battle_pb2.ListFilesRequest(), timeout=5)


def download_file(file_stub: Any, battle_pb2: Any, file_name: str, output_dir: Path) -> Path:
    remote_name = safe_remote_file_name(file_name)
    target_dir = output_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / remote_name
    partial_path = target_dir / f".{remote_name}.part"

    try:
        with partial_path.open("wb") as file:
            for chunk in file_stub.DownloadFile(battle_pb2.FileRequest(file_name=remote_name), timeout=15):
                if chunk.file_name and safe_remote_file_name(chunk.file_name) != remote_name:
                    raise ValueError("Servidor enviou chunks de outro arquivo.")
                file.write(chunk.content)
        partial_path.replace(target_path)
    except (grpc.RpcError, OSError, ValueError):
        if partial_path.exists():
            partial_path.unlink()
        raise

    return target_path


def print_file_list(console: Console, files_response: Any) -> None:
    table = Table(title="Arquivos no servidor gRPC", box=box.SIMPLE)
    table.add_column("Nome", style="cyan")
    table.add_column("Bytes", justify="right")

    for item in files_response.files:
        table.add_row(item.file_name, str(item.size_bytes))

    if not files_response.files:
        table.add_row("(nenhum arquivo)", "0")

    console.print(table)


def run_file_demo(file_stub: Any, battle_pb2: Any, console: Console) -> None:
    console.print(Rule("[bold cyan]TRANSFERENCIA DE ARQUIVO VIA gRPC[/bold cyan]"))
    upload_result = upload_file(file_stub, battle_pb2, SAMPLE_FILE)
    console.print(f"[green]Upload OK:[/green] {upload_result.message} ({upload_result.size_bytes} bytes)")

    files_response = list_files(file_stub, battle_pb2)
    print_file_list(console, files_response)

    downloaded = download_file(file_stub, battle_pb2, upload_result.file_name, DOWNLOAD_DIR)
    console.print(f"[green]Download OK:[/green] arquivo salvo em {downloaded}")


def write_action_request_file(action: str, actor: str, output_dir: Path) -> Path:
    target_dir = output_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "acao_batalha.txt"
    target_path.write_text(
        "\n".join(
            [
                "tipo=requisicao_batalha",
                f"acao={action}",
                f"ator={actor}",
                "",
            ]
        ),
        encoding="utf8",
    )
    return target_path


def result_file_name_for(request_file: Path) -> str:
    return f"resultado_{request_file.stem}{request_file.suffix or '.txt'}"


def run_action_file_demo(
    file_stub: Any,
    battle_pb2: Any,
    console: Console,
    action: str = "attack",
    actor: str = PLAYER_NAME,
) -> None:
    console.print(Rule("[bold cyan]ACAO DE BATALHA POR ARQUIVO VIA gRPC[/bold cyan]"))
    request_file = write_action_request_file(action, actor, REQUEST_DIR)
    console.print(f"[cyan]Arquivo de requisicao gerado:[/cyan] {request_file}")

    upload_result = upload_file(file_stub, battle_pb2, request_file)
    console.print(f"[green]Upload OK:[/green] {upload_result.message} ({upload_result.size_bytes} bytes)")

    result_name = result_file_name_for(request_file)
    files_response = list_files(file_stub, battle_pb2)
    print_file_list(console, files_response)

    downloaded = download_file(file_stub, battle_pb2, result_name, DOWNLOAD_DIR)
    console.print(f"[green]Resultado baixado:[/green] {downloaded}")
    console.print(Panel(downloaded.read_text(encoding="utf8"), title=result_name, border_style="green", box=box.ROUNDED))


def run_demo(battle_stub: Any, file_stub: Any, battle_pb2: Any, console: Console) -> None:
    reset_result = battle_stub.ResetBattle(battle_pb2.ResetRequest(), timeout=5)
    attack_result = battle_stub.Attack(battle_pb2.ActionRequest(actor_name=PLAYER_NAME), timeout=5)

    state = attack_result.state
    render_screen(console, battle_pb2, state, attack_result.message or reset_result.message)
    console.print("[bold green]Demo da batalha concluida: ResetBattle e Attack responderam com sucesso.[/bold green]")
    run_file_demo(file_stub, battle_pb2, console)
    run_action_file_demo(file_stub, battle_pb2, console)
    console.print("[bold green]Demo gRPC concluida com batalha e transferencia de arquivo.[/bold green]")


def run_interactive(stub: Any, battle_pb2: Any, console: Console) -> None:
    last_message = "Conectado ao servidor gRPC."
    state = call_status(stub, battle_pb2)

    while True:
        render_screen(console, battle_pb2, state, last_message)

        if state.outcome != battle_pb2.BATTLE_IN_PROGRESS:
            choice = Prompt.ask("Batalha encerrada. [4] Reiniciar ou [0] Sair", choices=["4", "0"], default="4")
        else:
            choice = Prompt.ask("Escolha sua acao", choices=["1", "2", "3", "4", "0"], default="1")

        try:
            if choice == "1":
                result = stub.Attack(battle_pb2.ActionRequest(actor_name=PLAYER_NAME), timeout=5)
                state = result.state
                last_message = result.message
            elif choice == "2":
                result = stub.UsePotion(battle_pb2.ActionRequest(actor_name=PLAYER_NAME), timeout=5)
                state = result.state
                last_message = result.message
            elif choice == "3":
                state = call_status(stub, battle_pb2)
                last_message = "Status atualizado pelo servidor."
            elif choice == "4":
                result = stub.ResetBattle(battle_pb2.ResetRequest(), timeout=5)
                state = result.state
                last_message = result.message
            elif choice == "0":
                console.print("[bold cyan]Ate a proxima batalha.[/bold cyan]")
                return
        except grpc.RpcError as exc:
            last_message = f"Erro gRPC: {exc.details() or exc.code().name}"


def run_file_commands(args: argparse.Namespace, file_stub: Any, battle_pb2: Any, console: Console) -> None:
    if args.upload:
        upload_result = upload_file(file_stub, battle_pb2, args.upload)
        console.print(f"[green]Upload OK:[/green] {upload_result.message} ({upload_result.size_bytes} bytes)")

    if args.list_files:
        print_file_list(console, list_files(file_stub, battle_pb2))

    if args.download:
        downloaded = download_file(file_stub, battle_pb2, args.download, args.download_dir)
        console.print(f"[green]Download OK:[/green] arquivo salvo em {downloaded}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cliente Rich para RPG distribuido via gRPC.")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"Endereco do servidor gRPC. Padrao: {DEFAULT_TARGET}")
    parser.add_argument("--demo", action="store_true", help="Executa batalha e transferencia de arquivo automaticamente.")
    parser.add_argument("--file-demo", action="store_true", help="Executa apenas upload, listagem e download de arquivo.")
    parser.add_argument(
        "--action-file-demo",
        action="store_true",
        help="Gera um arquivo de acao, envia ao servidor e baixa o resultado processado.",
    )
    parser.add_argument(
        "--action",
        choices=["attack", "use_potion", "reset", "status"],
        default="attack",
        help="Acao usada no --action-file-demo. Padrao: attack.",
    )
    parser.add_argument("--actor", default=PLAYER_NAME, help=f"Ator usado no arquivo de acao. Padrao: {PLAYER_NAME}")
    parser.add_argument("--upload", type=Path, help="Envia um arquivo local para o servidor gRPC.")
    parser.add_argument("--list-files", action="store_true", help="Lista arquivos armazenados no servidor gRPC.")
    parser.add_argument("--download", help="Baixa um arquivo do servidor gRPC pelo nome.")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR, help=f"Pasta de download. Padrao: {DOWNLOAD_DIR}")
    parser.add_argument("--generate-only", action="store_true", help="Apenas gera os stubs Python a partir do .proto.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    console = Console()

    ensure_generated_proto(console)
    if args.generate_only:
        console.print("[bold green]Stubs Python prontos.[/bold green]")
        return

    battle_pb2, battle_pb2_grpc = load_grpc_modules()

    try:
        with grpc.insecure_channel(args.target) as channel:
            grpc.channel_ready_future(channel).result(timeout=5)
            battle_stub = battle_pb2_grpc.BattleServiceStub(channel)
            file_stub = battle_pb2_grpc.FileServiceStub(channel)

            if args.demo:
                run_demo(battle_stub, file_stub, battle_pb2, console)
            elif args.file_demo:
                run_file_demo(file_stub, battle_pb2, console)
            elif args.action_file_demo:
                run_action_file_demo(file_stub, battle_pb2, console, args.action, args.actor)
            elif args.upload or args.list_files or args.download:
                run_file_commands(args, file_stub, battle_pb2, console)
            else:
                run_interactive(battle_stub, battle_pb2, console)
    except grpc.FutureTimeoutError:
        console.print(f"[bold red]Nao consegui conectar em {args.target}.[/bold red]")
        console.print("Inicie o servidor com: npm --prefix server-node start")
    except FileNotFoundError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
    except ValueError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
    except grpc.RpcError as exc:
        console.print(f"[bold red]Erro gRPC: {exc.details() or exc.code().name}[/bold red]")


if __name__ == "__main__":
    main()
