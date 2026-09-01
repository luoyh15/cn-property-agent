"""Reader for the small YAML subset used by city profile files.

City profiles are flat configuration documents: top-level scalars plus one
level of named sections (``providers``, ``benchmarks``, ``units``). Reading
them needs no YAML engine, and the project deliberately carries no YAML
dependency, so this module implements exactly that subset and rejects
everything else.

Supported::

    key: value          # top-level scalar
    section:            # section header
      key: value        # section entry (consistent indentation)

Not supported, and reported as an error rather than guessed at: sequences,
nesting deeper than one level, anchors/aliases, multi-line scalars, tab
indentation and duplicate keys. ``#`` starts a comment only at the beginning
of a line, so a value may contain ``#``. Every value is returned as a string;
interpretation belongs to the profile model.
"""

from __future__ import annotations

YamlMapping = dict[str, str | dict[str, str]]

_QUOTES = ("'", '"')


def parse_yaml_mapping(text: str) -> YamlMapping:
    """Parse the supported subset, or raise ``ValueError`` naming the line."""
    root: YamlMapping = {}
    section: dict[str, str] | None = None
    section_key = ""
    section_indent: int | None = None

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        content = line.strip()
        if not content or content.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent] or line[indent : indent + 1] == "\t":
            raise ValueError(f"line {number}: tab indentation is not supported")
        if content.startswith("-"):
            raise ValueError(f"line {number}: sequences are not supported")

        key, value = _split_entry(content, number)

        if indent == 0:
            if key in root:
                raise ValueError(f"line {number}: duplicate key {key!r}")
            if value is None:
                section = {}
                section_key = key
                section_indent = None
                root[key] = section
            else:
                section = None
                section_indent = None
                root[key] = value
            continue

        if section is None:
            raise ValueError(f"line {number}: indented entry {key!r} has no parent section")
        if section_indent is None:
            section_indent = indent
        elif indent != section_indent:
            raise ValueError(f"line {number}: inconsistent indentation in section {section_key!r}")
        if value is None:
            raise ValueError(f"line {number}: nesting deeper than one level is not supported")
        if key in section:
            raise ValueError(f"line {number}: duplicate key {key!r} in section {section_key!r}")
        section[key] = value

    return root


def _split_entry(content: str, number: int) -> tuple[str, str | None]:
    """Split ``key: value`` into its parts; a section header has value ``None``."""
    key, separator, value = content.partition(":")
    if not separator:
        raise ValueError(f"line {number}: expected 'key: value', got {content!r}")
    key = key.strip()
    if not key:
        raise ValueError(f"line {number}: entry has an empty key")
    value = value.strip()
    return key, _unquote(value) if value else None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        return value[1:-1]
    return value
