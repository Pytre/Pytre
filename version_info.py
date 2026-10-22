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

from Pytre_Core.about import APP_NAME, AppVersion, AUTHOR, COPYRIGHT_YEAR, LICENSE_NAME

VS_FF_PRERELEASE = 0x2


def version_info(description: str, original_filename: str) -> VSVersionInfo:
    """`description` is the name shown by Windows (task manager, file properties)"""
    version = AppVersion.current()

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version.binary_version,
            prodvers=version.binary_version,
            flags=VS_FF_PRERELEASE if version.is_prerelease else 0x0,
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", AUTHOR),
                        StringStruct("FileDescription", description),
                        StringStruct("FileVersion", str(version)),
                        StringStruct("InternalName", original_filename.removesuffix(".exe")),
                        StringStruct("LegalCopyright", f"Copyright (C) {COPYRIGHT_YEAR} {AUTHOR}"),
                        StringStruct("OriginalFilename", original_filename),
                        StringStruct("ProductName", APP_NAME),
                        StringStruct("ProductVersion", str(version)),
                        StringStruct("Comments", LICENSE_NAME),
                    ],
                )
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
