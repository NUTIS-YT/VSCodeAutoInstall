# VS Code Deployer

A Python TUI utility for installing and exporting Visual Studio Code extensions from a TOML configuration. Designed for repeatable setup across Astra Linux workstations.

## Features

- Rich terminal interface with progress bars and per-extension results.
- Reads extension IDs and optional pinned versions from candidates.toml.
- Detects the code executable and checks the installed editor version.
- Skips extensions that are already installed; optionally pins exact versions.
- Continues after individual failures, or stops when configured otherwise.
- Exports installed extension IDs and versions to a reusable TOML file.
- Writes an operation log to logs/deployer.log.
- Supports non-interactive install, export, and diagnostic modes.

## Requirements

- Python 3.10 or newer.
- Visual Studio Code CLI available as code in PATH.
- Internet access to the Visual Studio Code Marketplace.
- Python packages from requirements.txt.

The tool invokes the VS Code CLI. It does not install VS Code itself.

## Installation

    git clone https://github.com/NUTIS-YT/VSCodeAutoInstall.git
    cd VSCodeAutoInstall
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt

If python3 -m venv is unavailable on Astra Linux, install the matching system package that provides venv, then repeat the command.

## Configure

Edit candidates.toml:

    [settings]
    editor = "code"
    continue_on_error = true
    skip_installed = true
    timeout_seconds = 600

    [extensions]
    list = [
        "ms-python.python",
        "eamodio.gitlens",
        "ms-vscode.cpptools",
        # Optional exact Marketplace version:
        # "publisher.extension@1.2.3",
    ]

skip_installed = true skips an installed extension when no version is pinned, or when the installed version matches the pinned version. If a different version is pinned, the CLI is asked to install that version.

## Usage

Interactive menu:

    .venv/bin/python main.py

Non-interactive commands:

    # Install extensions from candidates.toml
    .venv/bin/python main.py --install

    # Export currently installed extensions and versions
    .venv/bin/python main.py --export

    # Check VS Code, configuration and installed extensions
    .venv/bin/python main.py --check

    # Use a different TOML file
    .venv/bin/python main.py --install --config exports/extensions-YYYYMMDD-HHMMSS.toml

An exported TOML file can be used directly as a configuration on another workstation. Copy it to the target machine and pass it with --config.

## Output

Runtime files are created beside main.py:

- logs/deployer.log — operation log.
- exports/ — timestamped TOML exports.

These runtime directories are intentionally excluded from Git.

## Notes

- The progress bar tracks completed extension operations. The VS Code CLI does not expose a reliable per-download percentage, so exact download progress for an individual extension is unavailable.
- Marketplace availability, network access, proxy settings and extension compatibility are controlled by the workstation and VS Code.
- Extensions are read from the VS Code profile used by the code command.

## License

MIT. See LICENSE.
