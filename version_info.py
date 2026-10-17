"""Windows version resource for the PyInstaller specs, built from Pytre_Core/about.py"""

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from Pytre_Core.about import (
    APP_BUILD,
    APP_NAME,
    APP_STATUS,
    APP_VERSION,
    AUTHOR,
    COPYRIGHT_YEAR,
    full_version,
    LICENSE_NAME,
)

VS_FF_PRERELEASE = 0x2


def version_info(description: str, original_filename: str) -> VSVersionInfo:
    """`description` is the name shown by Windows (task manager, file properties)"""
    numbers = [int(n) for n in APP_VERSION.split(".")][:3]
    numbers += [0] * (3 - len(numbers))
    binary_version = (*numbers, int(APP_BUILD))
    text_version = full_version()

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=binary_version,
            prodvers=binary_version,
            flags=VS_FF_PRERELEASE if APP_STATUS else 0x0,
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", AUTHOR),
                        StringStruct("FileDescription", description),
                        StringStruct("FileVersion", text_version),
                        StringStruct("InternalName", original_filename.removesuffix(".exe")),
                        StringStruct("LegalCopyright", f"Copyright (C) {COPYRIGHT_YEAR} {AUTHOR}"),
                        StringStruct("OriginalFilename", original_filename),
                        StringStruct("ProductName", APP_NAME),
                        StringStruct("ProductVersion", text_version),
                        StringStruct("Comments", LICENSE_NAME),
                    ],
                )
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
