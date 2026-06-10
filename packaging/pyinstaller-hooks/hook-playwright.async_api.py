from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files(
    "playwright",
    excludes=[
        "driver/package/.local-browsers",
        "driver/package/.local-browsers/**",
    ],
)
