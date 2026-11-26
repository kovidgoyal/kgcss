Installation
============

kgcss needs Python 3.14 or later. It works on both regular and free-threaded
(no-GIL) builds of CPython.

.. code-block:: sh

   pip install kgcss

kgcss has a C extension. If there is no wheel for your platform, pip builds
it from source, which needs a C compiler that supports C99.

Optional dependencies
---------------------

kgcss itself has no dependencies. :class:`kgcss.Select` matches selectors
against any ElementTree-style tree: the standard library's
:mod:`xml.etree.ElementTree` works, and so does `lxml <https://lxml.de>`_,
which most programs that work with HTML use.

Building from source
--------------------

In a clone of the repository:

.. code-block:: sh

   uv sync          # creates .venv, builds the extension, installs the development tools
   uv run pytest -n auto

See :doc:`development` for the rest of the tooling.
