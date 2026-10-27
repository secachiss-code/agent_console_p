import abc
from abc import ABC


def f(): ...  # expect: placeholder-body
def g(): pass  # expect: placeholder-body
def h():  # expect: placeholder-body
    """docstring only"""
def i():  # expect: placeholder-body
    raise NotImplementedError("todo")
def j():  # expect: placeholder-body
    raise NotImplementedError
async def a():  # expect: placeholder-body
    ...
class C:
    def m(self):  # expect: placeholder-body
        ...
class B(ABC):
    def m(self):  # expect: placeholder-body
        """no abstractmethod -> still a stub"""
        pass
