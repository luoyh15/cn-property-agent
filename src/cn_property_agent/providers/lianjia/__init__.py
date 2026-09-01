"""Lianjia-specific adapters.

Everything Lianjia knows about field names, Chinese units and floor wording
lives here. Core services stay source-independent and must not import this
package.
"""

from .transaction_parser import (
    LIANJIA_SOURCE,
    LIANJIA_TRANSACTION_PARSER_VERSION,
    LianjiaParseContext,
    parse_transaction_row,
    parse_transaction_rows,
)

__all__ = [
    "LIANJIA_SOURCE",
    "LIANJIA_TRANSACTION_PARSER_VERSION",
    "LianjiaParseContext",
    "parse_transaction_row",
    "parse_transaction_rows",
]
