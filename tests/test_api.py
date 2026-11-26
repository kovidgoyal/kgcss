# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The public API as a whole."""

import annotationlib
import inspect
from collections.abc import Callable, Iterator

import pytest

import kgcss
import kgcss.compat
import kgcss.media
import kgcss.selectors
import kgcss.supports
import kgcss.syntax

MODULES = (kgcss, kgcss.compat, kgcss.media, kgcss.selectors, kgcss.supports, kgcss.syntax)


def public_callables() -> Iterator[tuple[str, Callable[..., object]]]:
    """Every public function and class of the modules, and the public methods and properties of the
    classes, including those they inherit from kgcss classes."""
    for module in MODULES:
        for name in module.__all__:
            obj = getattr(module, name)
            qualname = f'{module.__name__}.{name}'
            if isinstance(obj, type):
                yield qualname, obj
                for cls in obj.__mro__:
                    if not cls.__module__.startswith('kgcss'):
                        continue
                    for key, value in vars(cls).items():
                        if key.startswith('_') and key != '__init__':
                            continue
                        if isinstance(value, property):
                            for f in (value.fget, value.fset):
                                if f is not None:
                                    yield f'{qualname}.{key}', f
                        elif isinstance(value, staticmethod | classmethod):
                            yield f'{qualname}.{key}', value.__func__
                        elif inspect.isfunction(value):
                            yield f'{qualname}.{key}', value
            elif inspect.isfunction(obj):
                yield qualname, obj


CALLABLES = dict(public_callables())


@pytest.mark.parametrize('name', sorted(CALLABLES))
def test_annotations_can_be_evaluated(name: str) -> None:
    # Names imported only for type checkers break help() and inspect.signature(), which evaluate annotations
    obj = CALLABLES[name]
    if not isinstance(obj, type):
        annotationlib.get_annotations(obj, format=annotationlib.Format.VALUE)
    try:
        inspect.signature(obj)
    except ValueError:
        assert isinstance(obj, type)  # classes of the C extension without a signature


def test_coverage() -> None:
    # The test sees the functions and methods that had names imported only for type checkers
    for name in ('kgcss.parse_stylesheet', 'kgcss.Stylesheet.iter_rules', 'kgcss.RuleList.sort', 'kgcss.Select'):
        assert name in CALLABLES
    assert len(CALLABLES) > 400
