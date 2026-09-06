"""A 20-year macro / policy-cycle knowledge base (stylized, editable theses).

Each entry is a REGIME with a date window and a `sector_bias` — the structural
tailwind (+) or headwind (-) a policy environment created for a sector. Multiple
regimes overlap (a background administration + specific funding bills / shocks);
the MacroEngine sums the active ones for any date.

These are HYPOTHESES, not facts — directional and debatable. Tune them; they are
meant to encode "what policy weather were we trading in," e.g. cannabis peaking
into 2021 then bleeding (SAFE Banking never passed), clean energy lifted by the
IRA (2022), semis pressured by the 2018 and 2025 tariff/export-control regimes,
oil & rare-earth favored under Trump-era energy/critical-minerals policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Map the tickers we care about (plus common proxies) to a sector theme.
TICKER_SECTOR: dict[str, str] = {
    # semiconductors / AI infrastructure
    "MRVL": "semiconductors", "NVDA": "semiconductors", "AMD": "semiconductors",
    "SMH": "semiconductors", "ANET": "ai_infra", "ALAB": "ai_infra",
    "AEIS": "ai_infra", "VRT": "ai_infra", "AVGO": "semiconductors",
    # EV / autos
    "TSLA": "ev", "RIVN": "ev",
    # clean energy / hydrogen
    "BE": "clean_energy", "ENPH": "clean_energy", "FSLR": "clean_energy",
    "ICLN": "clean_energy", "PLUG": "clean_energy",
    # oil & gas / midstream
    "ET": "oil_gas", "XOM": "oil_gas", "CVX": "oil_gas", "XLE": "oil_gas", "USO": "oil_gas",
    # rare earth / critical minerals
    "MP": "rare_earth", "REMX": "rare_earth",
    # cannabis
    "TLRY": "cannabis", "CGC": "cannabis", "MSOS": "cannabis", "CRON": "cannabis",
    # defense
    "LMT": "defense", "ITA": "defense", "RTX": "defense",
    # banks / financials / fintech
    "XLF": "banks", "JPM": "banks", "BULL": "fintech", "HOOD": "fintech",
    # crypto-adjacent
    "COIN": "crypto", "MSTR": "crypto",
    # china tech / consumer / misc
    "TCEHY": "china_tech", "WEN": "consumer", "ABEV": "consumer",
    "RDDT": "tech_growth", "CCXI": "biotech", "JEPQ": "broad",
}


def sector_of(symbol: str) -> str:
    return TICKER_SECTOR.get(symbol.upper(), "broad")


@dataclass
class MacroRegime:
    key: str
    name: str
    start: str                     # YYYY-MM-DD
    end: str                       # YYYY-MM-DD (inclusive-ish)
    kind: str                      # administration | policy | macro
    description: str
    sector_bias: dict[str, float] = field(default_factory=dict)   # sector -> -1..1

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "start": self.start, "end": self.end,
                "kind": self.kind, "description": self.description,
                "sector_bias": self.sector_bias}


# --- the library (2004 -> 2027) -------------------------------------------
REGIMES: list[MacroRegime] = [
    # ---- administrations (background weather) ----
    MacroRegime("bush2", "Bush 2nd term", "2005-01-20", "2009-01-20", "administration",
                "Commodity supercycle, housing/credit boom, financial leverage.",
                {"oil_gas": 0.5, "banks": 0.4, "defense": 0.4}),
    MacroRegime("obama1", "Obama 1st term", "2009-01-20", "2013-01-20", "administration",
                "Post-GFC recovery, ZIRP + QE, ACA, Dodd-Frank on banks, early clean-energy subsidies.",
                {"clean_energy": 0.4, "banks": -0.2, "biotech": 0.3, "tech_growth": 0.3}),
    MacroRegime("obama2", "Obama 2nd term", "2013-01-20", "2017-01-20", "administration",
                "Shale boom then 2014-16 oil crash, strong tech/growth, biotech bull then 2015-16 wobble.",
                {"tech_growth": 0.4, "oil_gas": -0.2, "biotech": 0.3, "semiconductors": 0.3}),
    MacroRegime("trump1", "Trump 1st term", "2017-01-20", "2021-01-20", "administration",
                "Tax cuts + buybacks, energy deregulation (oil/gas), defense, but tariff/trade-war pressure on semis/industrials.",
                {"oil_gas": 0.5, "defense": 0.4, "banks": 0.4, "rare_earth": 0.3,
                 "semiconductors": -0.2, "clean_energy": -0.3}),
    MacroRegime("biden", "Biden term", "2021-01-20", "2025-01-20", "administration",
                "Fiscal stimulus, IRA clean-energy + CHIPS semis funding; cannabis reform stalled; 2022 rate-hike growth crush.",
                {"clean_energy": 0.4, "semiconductors": 0.3, "ai_infra": 0.3,
                 "oil_gas": -0.1, "cannabis": -0.2, "ev": 0.3}),
    MacroRegime("trump2", "Trump 2nd term", "2025-01-20", "2029-01-20", "administration",
                "Tariffs + export controls, 'drill baby drill' energy, US rare-earth/critical-minerals push, "
                "deregulation, crypto-friendly; IRA/EV subsidy rollback; AI build-out continues.",
                {"oil_gas": 0.5, "rare_earth": 0.6, "defense": 0.4, "banks": 0.3, "crypto": 0.5,
                 "ai_infra": 0.3, "semiconductors": -0.2, "clean_energy": -0.4, "ev": -0.2}),

    # ---- specific policy events / funding (sharper, time-boxed) ----
    MacroRegime("gfc", "Global Financial Crisis", "2008-06-01", "2009-06-30", "macro",
                "Credit crash; financials collapse; broad de-risking.",
                {"banks": -0.9, "broad": -0.6, "oil_gas": -0.5, "gold": 0.5}),
    MacroRegime("trade_war_1", "US-China trade war (tariffs)", "2018-03-01", "2019-12-31", "policy",
                "Tariffs and export friction hit semis, industrials, China exposure.",
                {"semiconductors": -0.5, "china_tech": -0.6, "ai_infra": -0.3, "defense": 0.2}),
    MacroRegime("hemp_2018", "2018 Farm Bill / cannabis run", "2018-06-01", "2019-03-31", "policy",
                "Hemp legalization sparks a cannabis run (then a 2019 bust).",
                {"cannabis": 0.6}),
    MacroRegime("covid_crash", "COVID crash", "2020-02-19", "2020-03-31", "macro",
                "Pandemic shock; sharp broad drawdown, oil collapse.",
                {"broad": -0.9, "oil_gas": -0.9, "defense": -0.3}),
    MacroRegime("covid_stimulus", "COVID stimulus / reflation", "2020-04-01", "2021-02-28", "macro",
                "Massive fiscal+monetary stimulus; tech, EV, clean energy, cannabis, SPAC mania.",
                {"tech_growth": 0.6, "ev": 0.7, "clean_energy": 0.6, "cannabis": 0.6, "crypto": 0.6}),
    MacroRegime("meme_nft_2021", "Meme-stock / NFT / crypto mania", "2021-01-01", "2021-11-30", "macro",
                "GameStop short squeeze, NFT + crypto boom; retail hype, extreme beta. A regime to "
                "size DOWN into, not chase — the hype unwinds hard in 2022.",
                {"crypto": 0.7, "fintech": 0.5, "tech_growth": 0.4}),
    MacroRegime("cannabis_peak", "Cannabis peak -> long decline", "2021-02-01", "2024-06-30", "policy",
                "Reform hopes peak Feb 2021; SAFE Banking stalls; multi-year bleed. (Exit signal.)",
                {"cannabis": -0.6}),
    MacroRegime("rate_hikes_2022", "2022 Fed hiking cycle", "2022-01-01", "2022-12-31", "macro",
                "Aggressive rate hikes crush growth/tech/crypto; energy outperforms.",
                {"tech_growth": -0.6, "semiconductors": -0.4, "ai_infra": -0.4, "crypto": -0.7,
                 "oil_gas": 0.6, "clean_energy": -0.3}),
    MacroRegime("ira_chips_2022", "IRA + CHIPS Act funding", "2022-08-01", "2024-12-31", "policy",
                "Inflation Reduction Act (clean energy/EV/battery) + CHIPS Act (US semis).",
                {"clean_energy": 0.5, "ev": 0.4, "semiconductors": 0.4, "ai_infra": 0.3}),
    MacroRegime("ai_boom", "AI build-out", "2023-01-01", "2027-12-31", "macro",
                "Generative-AI capex supercycle; semis, data-center power/cooling, networking.",
                {"semiconductors": 0.6, "ai_infra": 0.7}),
    MacroRegime("rare_earth_2025", "Critical-minerals / rare-earth push", "2025-01-20", "2027-12-31", "policy",
                "US onshoring of rare-earth extraction and processing; defense demand.",
                {"rare_earth": 0.7, "defense": 0.3}),
]
