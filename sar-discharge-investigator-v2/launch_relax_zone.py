"""Set up the portable Relax Zone Python environment and start the explorer."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys


APP = Path(__file__).resolve().parent
ENVIRONMENT = APP / ".relax-zone-venv"
REQUIREMENTS = APP / "requirements-explorer.txt"


def prepare_environment() -> Path:
    if sys.version_info < (3, 11):
        raise RuntimeError("Use Python 3.11 or newer to run Relax Zone.")
    executable = ENVIRONMENT / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        print("Creating the Relax Zone virtual environment.", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(ENVIRONMENT)], cwd=APP, check=True)
    stamp = ENVIRONMENT / "requirements.sha256"
    signature = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != signature:
        print("Installing the Relax Zone Python dependencies.", flush=True)
        subprocess.run([str(executable), "-m", "pip", "--disable-pip-version-check", "install",
                        "-r", str(REQUIREMENTS)], cwd=APP, check=True)
        stamp.write_text(signature + "\n", encoding="ascii")
    return executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4174)
    parser.add_argument("--public-origin", help="HTTPS origin printed by the Cloudflare tunnel")
    parser.add_argument("--without-ollama", action="store_true", help="Use the standalone assistant")
    parser.add_argument("--setup-only", action="store_true", help="Install Python dependencies and exit")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    executable = prepare_environment()
    if args.setup_only:
        return 0
    command = [str(executable), "-B", str(APP / "explorer_server.py"), "--port", str(args.port)]
    if args.public_origin:
        command.extend(("--public-origin", args.public_origin))
    if args.without_ollama:
        command.append("--without-ollama")
    return subprocess.run(command, cwd=APP).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Relax Zone setup could not finish: {error}", file=sys.stderr)
        raise SystemExit(1)
