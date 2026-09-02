"""Lianjia-specific adapters.

Everything Lianjia knows about field names, Chinese units and floor wording
lives here. Core services stay source-independent and must not import this
package.
"""

from .listing_parser import (
    LIANJIA_LISTING_PARSER_VERSION,
    LianjiaListingParseContext,
    build_listing_id,
    parse_listing_row,
    parse_listing_rows,
)
from .recorded_transactions import (
    LianjiaSnapshotError,
    LianjiaTransactionSnapshot,
    RecordedLianjiaTransactionProvider,
    load_transaction_snapshot,
)
from .transaction_parser import (
    LIANJIA_SOURCE,
    LIANJIA_TRANSACTION_PARSER_VERSION,
    LianjiaParseContext,
    parse_transaction_row,
    parse_transaction_rows,
)

__all__ = [
    "LIANJIA_LISTING_PARSER_VERSION",
    "LIANJIA_SOURCE",
    "LIANJIA_TRANSACTION_PARSER_VERSION",
    "LianjiaListingParseContext",
    "LianjiaParseContext",
    "LianjiaSnapshotError",
    "LianjiaTransactionSnapshot",
    "RecordedLianjiaTransactionProvider",
    "build_listing_id",
    "load_transaction_snapshot",
    "parse_listing_row",
    "parse_listing_rows",
    "parse_transaction_row",
    "parse_transaction_rows",
]
