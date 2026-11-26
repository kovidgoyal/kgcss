Development
===========

kgcss uses `uv <https://docs.astral.sh/uv/>`_:

.. code-block:: sh

   uv sync                   # create .venv, build the C extension, install the development tools
   uv run pytest -n auto     # run the tests in parallel
   uv run ruff check .       # lint: type annotations are required
   uv run ruff format .      # format
   uv run ty check           # type check

``uv sync`` and ``uv run`` rebuild the extension when files in ``csrc/``
change. ``python setup.py build_ext --inplace`` builds it in place. Build
options, as environment variables: ``KGCSS_DEBUG=1`` (a debug build with
assertions), ``KGCSS_SANITIZE=1`` (ASan and UBSan), ``KGCSS_WERROR=1``
(compiler warnings are errors).

More testing
------------

.. code-block:: sh

   # Free-threaded Python
   uv run --no-project -p 3.14t --isolated --with pytest --with pytest-xdist --with hypothesis --with . -- python -m pytest -n auto
   # Many more property-based examples, and longer concurrency stress tests
   HYPOTHESIS_PROFILE=thorough KGCSS_STRESS=10 uv run pytest -n auto
   # The tests against an ASan + UBSan build of the extension
   uv run python tools/sanitize_tests.py -n auto
   # libFuzzer, on the tokenizer, the selector parser and the color parser (needs clang)
   uv run python tools/fuzz.py run --seconds 300
   uv run python tools/fuzz.py run --target selectors --seconds 300
   uv run python tools/fuzz.py run --target colors --seconds 300

Some tests compare kgcss with the libraries it replaces. They need those
libraries, which ``bench/bench_parse.py prepare`` downloads (and css_parser,
from the ``bench`` dependency group); without them, those tests are skipped:

.. code-block:: sh

   uv run --group bench python bench/bench_parse.py prepare
   uv run --group bench pytest -n auto

Benchmarks
----------

Pin benchmarks to fast cores (``taskset`` on Linux):

.. code-block:: sh

   uv run --group bench python bench/bench_parse.py prepare
   taskset -c 0-2 uv run --group bench python bench/bench_parse.py run           # parsing
   taskset -c 0-2 uv run --group bench python bench/bench_select.py              # matching
   taskset -c 0-2 uv run --group bench python bench/ab_compare.py --base HEAD    # A/B against a revision

``bench_color.py``, ``bench_media.py`` and ``bench_fonts.py`` measure the other
parts.

Documentation
-------------

.. code-block:: sh

   uv run --with-requirements docs/requirements.txt sphinx-build -W -b html docs build/docs
   uv run --with-requirements docs/requirements.txt sphinx-build -W -b doctest docs build/doctest

