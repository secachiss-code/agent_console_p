# pyright: basic  # expect: pyright-file-mode
x = 1  # pyright: ignore[reportUnknownMemberType]  # expect: pyright-ignore
y = 2  # pyright: reportAny=false  # expect: pyright-file-mode
