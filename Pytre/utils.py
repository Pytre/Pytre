import codecs
import os
import subprocess
import platform
from pathlib import Path


def get_system() -> str:
    return platform.system()


def startfile(filename: str | Path) -> None:
    validated_filename: Path = validate_path(filename)
    system: str = platform.system()

    if system == "Windows":
        os.startfile(str(validated_filename))
        return
    elif system == "Darwin":  # macOS
        subprocess.Popen(["open", str(validated_filename)])
    elif system == "Linux":
        file_manager = get_file_manager()
        if not validated_filename.is_file() and file_manager:
            subprocess.Popen([file_manager, "--select", str(validated_filename)])
        else:
            subprocess.Popen(["xdg-open", str(validated_filename)])
    else:
        raise ValueError(f"OS not supported: {system}")


def showfile(filename: str | Path) -> None:
    validated_filename: Path = validate_path(filename)

    system: str = platform.system()

    is_file = Path(validated_filename).is_file()
    file_manager = get_file_manager()

    if not file_manager:
        folder = Path(validated_filename).parent if is_file else validated_filename
        startfile(str(folder))
        return

    native_path = str(validated_filename)  # normalisation séparateurs de chemin (/ -> \ sous Windows)
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


def validate_path(filename: str | Path) -> Path:
    """Valide et normalise un chemin de fichier pour des raisons de sécurité."""
    if not filename:
        raise ValueError("Filename cannot be empty")

    filepath = Path(filename).resolve()  # Résolution des symlinks et chemins relatifs

    if not filepath.exists():
        raise FileNotFoundError(f"{filepath} does not exist")

    # Check if filepath is an actual file or folder (not a device, socket, etc.)
    if not (filepath.is_file() or filepath.is_dir()):
        raise ValueError(f"{filepath} is not a regular file or directory")

    # Vérifier les permissions de lecture
    if not os.access(filepath, os.R_OK):
        raise PermissionError(f"No read permission for {filepath}")

    return filepath


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


def detect_encoding(filepath: str | Path, encoding: str | None = None, sample_size_mb: int = 10) -> str:
    # Encodings to test, strictest to most permissive (for decoding no difference between utf-8-sig and utf-8)
    encodings: list[str] = ["utf-8-sig", "cp1252", "iso-8859-1"]

    # If encoding provided, test it first
    default_encoding: str | None = encoding.lower() if encoding and get_valid_encoding(encoding) else None
    if default_encoding in encodings:
        encodings.remove(default_encoding)
    if default_encoding:
        encodings.insert(0, default_encoding)

    # Test encoding on file sample
    for tested_encoding in encodings:
        try:
            with open(filepath, "r", encoding=tested_encoding, newline="") as f:
                f.read(sample_size_mb * 1024 * 1024)
            return tested_encoding
        except UnicodeDecodeError:
            continue
        except LookupError:
            print(f"Unknown encoding format to test provided: '{tested_encoding}'")

    return ""


def get_valid_encoding(encoding: str, fallback: str = "", warn: bool = True) -> str | None:
    if encoding is None:
        raise ValueError("Encoding must be a string, not None")
    try:
        codecs.lookup(encoding)
        return encoding
    except LookupError:
        if warn:
            print(f"Unknown encoding format: '{encoding}'")

    return None if not fallback else get_valid_encoding(fallback)
