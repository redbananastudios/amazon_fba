"""OA-feed importer registry.

Online-arbitrage workflows feed in pre-filtered candidate CSVs from
third-party tools (SellerAmp 2DSorter, Tactical Arbitrage, OAXray). Each
tool has its own column layout; the importer abstraction normalises them
to a canonical `OaCandidate` shape.

v1 implementation (per PRD §6.3):
  - SellerAmp 2DSorter — full implementation
  - Tactical Arbitrage / OAXray — `NotImplementedError` stubs with
    documented column-mapping TODOs

To register a new importer, drop a module under this package, instantiate
your `OaFeedImporter` class, and register it in `IMPORTERS` below.
"""
from .base import IMPORTERS, OaCandidate, OaFeedImporter
from .selleramp import SellerAmp2DSorterImporter
from .tactical_arbitrage import TacticalArbitrageImporter
from .oaxray import OaxrayImporter

# Register the v1 importers. Order doesn't matter — registry is dict-keyed
# by feed_id.
IMPORTERS["selleramp"] = SellerAmp2DSorterImporter()
IMPORTERS["tactical_arbitrage"] = TacticalArbitrageImporter()
IMPORTERS["oaxray"] = OaxrayImporter()


__all__ = [
    "IMPORTERS",
    "OaCandidate",
    "OaFeedImporter",
    "SellerAmp2DSorterImporter",
    "TacticalArbitrageImporter",
    "OaxrayImporter",
]
