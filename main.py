#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VS Code Deployer: install and export VS Code extensions from TOML."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

APP_NAME = "VS Code Deployer"
VERSION = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "candidates.toml"
LOG_DIR = BASE_DIR / "logs"
EXPORT_DIR = BASE_DIR / "exports"

console = Console()
log = logging.getLogger("vscode-deployer")
EXTENSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9][A-Za-z0-9._-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class DeployerError(Exception):
    """Expected application error."""


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "deployer.log", encoding="utf-8")],
        force=True,
    )


def show_header() -> None:
    title = Text()
    title.append("VS CODE ", style="bold white")
    title.append("DEPLOYER", style="bold bright_blue")
    title.append(f"  v{VERSION}", style="dim")
    console.print()
    console.print(
        Panel(
            title,
            subtitle="[dim]ASTRA LINUX • EXTENSION MANAGEMENT[/dim]",
            border_style="bright_blue",
            box=box.DOUBLE,
            padding=(1, 2),
        )
    )


def pause() -> None:
    console.input("\n[dim]Нажмите Enter для продолжения...[/dim]")


def split_extension(value: str) -> tuple[str, str | None]:
    if "@" not in value:
        return value.strip(), None
    extension_id, version = value.rsplit("@", 1)
    return extension_id.strip(), version.strip()


def load_config(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise DeployerError(
            "Не найден TOML-парсер. Установите зависимости: "
            "python3 -m pip install -r requirements.txt"
        )
    if not path.is_file():
        raise DeployerError(f"Файл конфигурации не найден: {path}")

    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise DeployerError(f"Не удалось прочитать TOML: {exc}") from exc

    settings = raw.get("settings", {})
    extension_section = raw.get("extensions", {})
    if not isinstance(settings, dict) or not isinstance(extension_section, dict):
        raise DeployerError("Секции [settings] и [extensions] должны быть таблицами.")

    extensions = extension_section.get("list", [])
    if not isinstance(extensions, list):
        raise DeployerError("[extensions].list должен быть массивом строк.")

    validated: list[str] = []
    seen: set[str] = set()
    for value in extensions:
        if not isinstance(value, str):
            raise DeployerError(f"ID расширения должен быть строкой: {value!r}")
        extension_id, version = split_extension(value)
        if not EXTENSION_ID_RE.fullmatch(extension_id):
            raise DeployerError(f"Некорректный ID расширения: {value!r}")
        if version is not None and not VERSION_RE.fullmatch(version):
            raise DeployerError(f"Некорректная версия расширения: {value!r}")
        key = extension_id.casefold()
        if key not in seen:
            validated.append(value)
            seen.add(key)

    timeout = settings.get("timeout_seconds", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise DeployerError("settings.timeout_seconds должен быть целым числом больше нуля.")

    for key in ("continue_on_error", "skip_installed"):
        if key in settings and not isinstance(settings[key], bool):
            raise DeployerError(f"settings.{key} должен быть true или false.")

    editor = settings.get("editor", "code")
    if not isinstance(editor, str) or not editor.strip():
        raise DeployerError("settings.editor должен содержать имя команды.")

    return {
        "editor": editor.strip(),
        "continue_on_error": settings.get("continue_on_error", True),
        "skip_installed": settings.get("skip_installed", True),
        "timeout_seconds": timeout,
        "extensions": validated,
    }


def find_editor(editor_name: str) -> str:
    executable = shutil.which(editor_name)
    if not executable:
        raise DeployerError(
            f"Команда '{editor_name}' не найдена в PATH. Проверьте установку VS Code."
        )
    return executable


def run_cli(
    executable: str,
    arguments: list[str],
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeployerError(f"Команда превысила таймаут ({timeout} сек.).") from exc
    except OSError as exc:
        raise DeployerError(f"Не удалось запустить VS Code CLI: {exc}") from exc


def check_editor(executable: str) -> str:
    result = run_cli(executable, ["--version"], timeout=30)
    if result.returncode != 0:
        raise DeployerError(result.stderr.strip() or "VS Code CLI завершился с ошибкой.")
    return result.stdout.strip()


def get_installed_extensions(executable: str) -> dict[str, str | None]:
    result = run_cli(executable, ["--list-extensions", "--show-versions"], timeout=120)
    if result.returncode != 0:
        raise DeployerError(
            result.stderr.strip() or "Не удалось получить список расширений."
        )

    installed: dict[str, str | None] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            extension_id, version = split_extension(line)
            installed[extension_id.casefold()] = version
    return installed


def install_extensions(executable: str, config: dict[str, Any]) -> dict[str, int]:
    extensions = config["extensions"]
    stats = {"total": len(extensions), "installed": 0, "skipped": 0, "failed": 0}

    if not extensions:
        console.print("[yellow]Список расширений пуст.[/yellow]")
        return stats

    installed = get_installed_extensions(executable)
    console.print(
        Panel(
            f"Расширений в списке: [bold]{len(extensions)}[/bold]\n"
            f"Пропуск установленных: [bold]{'да' if config['skip_installed'] else 'нет'}[/bold]",
            title="Установка расширений",
            border_style="cyan",
        )
    )

    results = Table(box=box.SIMPLE_HEAVY, expand=True)
    results.add_column("Расширение", style="white")
    results.add_column("Статус", justify="right")

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Подготовка...", total=len(extensions))

        for item in extensions:
            extension_id, target_version = split_extension(item)
            key = extension_id.casefold()
            current_version = installed.get(key)
            is_current = key in installed and (
                target_version is None or current_version == target_version
            )

            if config["skip_installed"] and is_current:
                stats["skipped"] += 1
                results.add_row(item, "[yellow]SKIPPED[/yellow]")
                log.info("SKIPPED %s (installed version: %s)", item, current_version)
                progress.update(task, advance=1, description=f"[yellow]Пропуск: {item}")
                continue

            progress.update(task, description=f"[cyan]Установка: {item}")
            log.info("INSTALL START %s", item)
            result = run_cli(
                executable,
                ["--install-extension", item],
                timeout=config["timeout_seconds"],
            )

            if result.returncode == 0:
                stats["installed"] += 1
                results.add_row(item, "[green]SUCCESS[/green]")
                log.info("INSTALL SUCCESS %s", item)
            else:
                stats["failed"] += 1
                results.add_row(item, "[red]FAILED[/red]")
                error = result.stderr.strip() or result.stdout.strip()
                log.error("INSTALL FAILED %s: %s", item, error or result.returncode)
                if error:
                    console.print(f"[dim red]{error}[/dim red]")

            progress.update(task, advance=1)
            if stats["failed"] and not config["continue_on_error"]:
                remaining = (
                    len(extensions)
                    - stats["installed"]
                    - stats["skipped"]
                    - stats["failed"]
                )
                if remaining:
                    console.print(
                        f"[yellow]Остановка по настройке continue_on_error=false. "
                        f"Не обработано: {remaining}[/yellow]"
                    )
                break

    console.print(results)
    return stats


def show_summary(stats: dict[str, int]) -> None:
    table = Table(title="Итоги", box=box.ROUNDED, expand=True)
    table.add_column("Результат")
    table.add_column("Количество", justify="right")
    table.add_row("Всего в конфигурации", str(stats["total"]))
    table.add_row("Установлено", f"[green]{stats['installed']}[/green]")
    table.add_row("Пропущено", f"[yellow]{stats['skipped']}[/yellow]")
    table.add_row("Ошибок", f"[red]{stats['failed']}[/red]")
    console.print(table)
    log.info("SUMMARY %s", stats)


def export_extensions(executable: str) -> Path:
    installed = get_installed_extensions(executable)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"extensions-{datetime.now().strftime('%Y%m%d-%H%M%S')}.toml"
    destination = EXPORT_DIR / filename

    lines = [
        "# Generated by VS Code Deployer",
        f"# Exported: {datetime.now().astimezone().isoformat()}",
        "",
        "[settings]",
        'editor = "code"',
        "continue_on_error = true",
        "skip_installed = true",
        "timeout_seconds = 600",
        "",
        "[extensions]",
        "list = [",
    ]
    for extension_id, version in sorted(installed.items()):
        value = f"{extension_id}@{version}" if version else extension_id
        lines.append(f"    {json.dumps(value, ensure_ascii=False)},")
    lines.extend(["]", ""])

    destination.write_text("\n".join(lines), encoding="utf-8")
    log.info("EXPORTED %d extensions to %s", len(installed), destination)
    console.print(f"[green]Экспортировано расширений: {len(installed)}[/green]")
    console.print(f"[cyan]Файл: {destination}[/cyan]")
    return destination


def system_check(config: dict[str, Any], executable: str) -> None:
    version = check_editor(executable)
    installed = get_installed_extensions(executable)
    table = Table(title="Диагностика", box=box.ROUNDED, expand=True)
    table.add_column("Проверка")
    table.add_column("Результат")
    table.add_row("Исполняемый файл", executable)
    table.add_row("Версия VS Code", version.splitlines()[0] if version else "unknown")
    table.add_row("Конфигурация", "[green]OK[/green]")
    table.add_row("В списке", str(len(config["extensions"])))
    table.add_row("Установлено", str(len(installed)))
    table.add_row("Лог", str(LOG_DIR / "deployer.log"))
    table.add_row("Экспорт", str(EXPORT_DIR))
    console.print(table)


def show_menu() -> str:
    table = Table(title="Главное меню", box=box.ROUNDED, border_style="bright_blue")
    table.add_column("№", style="bright_blue", width=5)
    table.add_column("Операция", style="bold")
    table.add_column("Описание", style="dim")
    table.add_row("1", "Установка расширений", "Установить список из TOML")
    table.add_row("2", "Экспорт расширений", "Сохранить ID и версии")
    table.add_row("3", "Проверка системы", "Проверить VS Code и конфигурацию")
    table.add_row("4", "Выход", "Завершить программу")
    console.print(table)
    return console.input("\n[bold cyan]Выбери действие [1-4]: [/bold cyan]").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Установка и экспорт расширений Visual Studio Code."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Путь к TOML-конфигурации (по умолчанию candidates.toml).",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--install", action="store_true", help="Установить расширения.")
    actions.add_argument("--export", action="store_true", help="Экспортировать расширения.")
    actions.add_argument("--check", action="store_true", help="Проверить систему.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_logging()
    show_header()

    try:
        config = load_config(args.config.expanduser().resolve())
        executable = find_editor(config["editor"])
        version = check_editor(executable)
        console.print(
            f"[green]✓ VS Code обнаружен:[/green] "
            f"{version.splitlines()[0] if version else executable}"
        )

        if args.check:
            system_check(config, executable)
            return 0
        if args.install:
            stats = install_extensions(executable, config)
            show_summary(stats)
            return 1 if stats["failed"] else 0
        if args.export:
            export_extensions(executable)
            return 0

        while True:
            choice = show_menu()
            try:
                if choice == "1":
                    stats = install_extensions(executable, config)
                    show_summary(stats)
                    pause()
                elif choice == "2":
                    export_extensions(executable)
                    pause()
                elif choice == "3":
                    system_check(config, executable)
                    pause()
                elif choice == "4":
                    console.print("[cyan]Завершение работы.[/cyan]")
                    return 0
                else:
                    console.print("[red]Неизвестная команда.[/red]")
            except DeployerError as exc:
                console.print(f"[red]Ошибка: {exc}[/red]")
                log.exception("OPERATION ERROR")
                pause()
    except KeyboardInterrupt:
        console.print("\n[yellow]Операция прервана пользователем.[/yellow]")
        log.warning("INTERRUPTED")
        return 130
    except (DeployerError, OSError) as exc:
        console.print(f"[red]Ошибка запуска: {exc}[/red]")
        log.exception("STARTUP ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())
