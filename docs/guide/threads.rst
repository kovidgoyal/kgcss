Threads and performance
=======================

Threads
-------

kgcss supports free-threaded (no-GIL) Python: its C extension declares that
it does not need the GIL, and it is tested with many threads parsing,
matching and serializing at once. On regular Python, the long parts of the C
code release the GIL: tokenizing large inputs and matching selectors.

What can be shared between threads:

- **Immutable objects**: values, parsed selectors, media queries, colors,
  tokens and syntax trees can be read from any number of threads.
- **Sheets and their rules**: kgcss guards its lazily built state (blocks
  parsed on first use, caches) with locks, so several threads can read the
  same sheet at once. Changing a sheet while other threads read or change it
  needs your own locking, as with any mutable Python object.
- **Select**: a ``Select`` can be used from several threads at once.
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
