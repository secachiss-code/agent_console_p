from mylib import cast
from mylib.types import Any as MyAny


def cast_local(x):
    return x


s = "typing.cast is discussed here"
# typing.cast is only a comment
v = cast(int, 1)
w: MyAny = 2
