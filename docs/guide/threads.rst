Threads and performance
=======================

Threads
-------

kgcss supports free-threaded (no-GIL) Python: its C extension declares that
it does not need the GIL, and it is tested with many threads parsing,
matching and serializing at once. On regular Python, the long parts of the C
code release the GIL: tokenizing large inputs and matching selectors.

What can be shared between threads:

- **Immutable objects**: value components and :class:`kgcss.PropertyValue`,
  parsed selectors, media queries, colors, tokens and syntax trees cannot be
  changed, so any number of threads can read them. This matters because
  some are shared through caches: two calls to
  :func:`kgcss.parse_media_queries` with the same text return the same
  objects.
- **Sheets and their rules**: kgcss guards their lazily built parts (blocks
  parsed on first use, selector lists, nested rules) with locks, for parsed
  sheets and for rules made with the API alike, so several threads can read
  the same sheet at once and always see the same objects. Changing a sheet
  while other threads read or change it needs your own locking, as with any
  mutable Python object: a property written while another thread serializes
  it can be written with some fields old and some new.
- **Callbacks** that you give kgcss, the ``fetcher`` of ``@import`` and the
  ``log`` handler, are never called while kgcss holds a lock, so they can
  wait for other threads that use the same sheet.
- **Select**: a ``Select`` can be used from several threads at once,
  including with element classes of your own whose ``text`` or ``tail`` are
  Python properties.
- **Caches** of parsed selectors, media queries and grammar matches are
  shared and thread-safe.

There are no global settings: every option is passed per call.

Performance
-----------

The tokenizer, the parser of the syntax tree, the selector parser and
matcher, the color parser and the writer of values are in C. Blocks are
parsed on first use, so reading part of a large sheet is cheap, and
unchanged text is reused when writing in ``preserve`` style.

For the fastest matching, make one :class:`kgcss.Select` for a document and
use it for all selectors: the snapshot and indexes are built once.
