"""Shanghai official market-source adapters.

Everything the Shanghai official market tables know about column names,
indicator wording, region levels, period notation and published units lives
here. Core services stay source-independent and must not import this package.
"""

from .errors import (
    ShanghaiOfficialDatasetError,
    ShanghaiOfficialMarketError,
    ShanghaiOfficialParseError,
    UnsupportedCityError,
)
from .market_parser import (
    SHANGHAI_CITY_CODE,
    SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION,
    SHANGHAI_OFFICIAL_SOURCE,
    ShanghaiOfficialParseContext,
    build_observation_id,
    parse_market_row,
    parse_market_rows,
)
from .recorded_market import (
    RecordedShanghaiOfficialMarketProvider,
    ShanghaiOfficialMarketDataset,
    load_market_dataset,
)

__all__ = [
    "SHANGHAI_CITY_CODE",
    "SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION",
    "SHANGHAI_OFFICIAL_SOURCE",
    "RecordedShanghaiOfficialMarketProvider",
    "ShanghaiOfficialDatasetError",
    "ShanghaiOfficialMarketDataset",
    "ShanghaiOfficialMarketError",
    "ShanghaiOfficialParseContext",
    "ShanghaiOfficialParseError",
    "UnsupportedCityError",
    "build_observation_id",
    "load_market_dataset",
    "parse_market_row",
    "parse_market_rows",
]
