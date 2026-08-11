import re
from pathlib import Path


class FileParser:
    def __init__(self, filename: Path = Path(), encoding: str = "utf-8"):
        self.filename: Path = filename
        self.content: str = self._read_file(encoding)

    def _read_file(self, encoding: str) -> str:
        file_content = ""

        # if filename has not been set then immediatly return without error
        if self.filename == Path():
            return file_content

        # set alternative encoding to try if failing to decode
        encodings: list = ["utf-8", "cp1252", "ansi"]
        encodings.remove(encoding) if encoding in encodings else None
        encodings.insert(0, encoding)

        # test all possible decoding formats
        decoded: bool = False
        for encoding in encodings:
            try:
                with open(self.filename, mode="r", encoding=encoding) as f:
                    file_content = f.read()
                decoded = True
                break
            except (UnicodeDecodeError, LookupError):  # LookupError if format doesn't exist (eg : ansi on linux)
                pass

        # if all try failed raise an exception
        if not decoded:
            raise TypeError(f"couldn't decode file using {', '.join(encodings)}")

        return file_content

    def extract_header_infos(self) -> dict:
        infos = {}
        regex_match = re.search(
            r"^\/\*[^\n]*?\n(.*?)\n^\*\/", self.content, re.MULTILINE | re.DOTALL
        )  # récupération des infos d'entête de la requete (premier bloc de commentaires)

        if regex_match is not None:
            for line in regex_match.group(1).splitlines():
                regex_infos = re.search(r"^([^:]*?)\s*:\s*(.*$)", line)
                if regex_infos is not None:
                    info_key = regex_infos.group(1).lower()

                    convert_to_list = ["grp_authorized", "servers"]
                    if info_key in convert_to_list:
                        info_value = [item.lower().strip() for item in regex_infos.group(2).split(",")]
                    else:
                        info_value = regex_infos.group(2)

                    infos[info_key] = info_value  # rajout dans un dictionnaire de l'info

        return infos

    def extract_raw_command(self) -> str:
        regex_match = re.search(r"^DECLARE[^;]*;[\s]*(.*)", self.content, re.MULTILINE | re.DOTALL)
        raw_cmd = regex_match.group(1) if regex_match is not None else self.content
        return raw_cmd
