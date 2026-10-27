import typing as t
import typing
from typing import cast as c, Any as A  # expect: banned-typing-import banned-typing-import
import typing_extensions as te

x = t.cast(int, 1)  # expect: banned-typing-use
y = c(int, 2)  # expect: banned-typing-use
z: typing.Any = 3  # expect: banned-typing-use
w: A = 4  # expect: banned-typing-use
v = te.cast(str, 5)  # expect: banned-typing-use
