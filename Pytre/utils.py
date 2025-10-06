import os
import subprocess
import platform
from pathlib import Path


def get_system() -> str:
    return platform.system()


def startfile(filename: str) -> None:
    if not filename or not Path(filename).exists():
        raise FileNotFoundError(f"{filename} does not exist")

    system: str = platform.system()

    if system == "Windows":
        os.startfile(filename)
        return
    elif system == "Darwin":  # macOS
        subprocess.Popen(["open", filename])
    elif system == "Linux":
        file_manager = get_file_manager()
        if not Path(filename).is_file() and file_manager:
            subprocess.Popen([file_manager, "--select", filename])
        else:
            subprocess.Popen(["xdg-open", filename])
    else:
        raise ValueError(f"OS not supported: {system}")


def showfile(filename: str) -> None:
    if not filename or not Path(filename).exists():
        raise FileNotFoundError(f"{filename} does not exist")

    system: str = platform.system()

    is_file = Path(filename).is_file()
    file_manager = get_file_manager()

    if not file_manager:
        folder = Path(filename).parent if is_file else filename
        startfile(folder)
        return

    native_path = str(Path(filename))  # normalisation séparateurs de chemin (/ -> \ sous Windows)
    args = [file_manager, native_path]
    if is_file:
        select_cmd: str = ""
        if system == "Windows":
            select_cmd = "/select,"
        elif system == "Linux":
            select_cmd = "--select"
        elif system == "Darwin":
            select_cmd = "-R"

        args.insert(1, select_cmd)

    subprocess.Popen(args)


def get_file_manager() -> str:
    system: str = platform.system()

    if system == "Windows":
        return "explorer"
    elif system == "Darwin":  # macOS
        return "open"
    elif system == "Linux":
        return _linux_file_manager()
    else:
        raise ValueError(f"OS not supported: {system}")


def _linux_file_manager() -> str:
    try:
        desktop_entry = subprocess.run(
            ["xdg-mime", "query", "default", "inode/directory"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if not desktop_entry:
            return ""
    except subprocess.CalledProcessError:
        return ""

    search_paths = [
        Path.home() / ".local/share/applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
    ]
    desktop_file = next((file / desktop_entry for file in search_paths if (file / desktop_entry).exists()), None)

    if not desktop_file:
        return ""

    with open(desktop_file, "r", encoding="utf-8") as f:
        line: str
        for line in f:
            if line.startswith("Exec="):
                cmd = line[len("Exec=") :].strip()
                cmd = cmd.split()[0]
                return cmd

    return ""
