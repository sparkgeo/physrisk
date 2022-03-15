from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import numpy as np

from physrisk.kernel import calculation
from physrisk.kernel.assets import Asset
from physrisk.kernel.calculation import calculate_impacts
from physrisk.kernel.hazard_model import HazardModel
from physrisk.kernel.impact_distrib import ImpactDistrib, ImpactType
from physrisk.kernel.vulnerability_model import VulnerabilityModelBase


class FinancialDataProvider(ABC):
    @abstractmethod
    def get_asset_value(self, asset: Asset, currency: str) -> float:
        """Return the current value of the asset in specified currency."""
        ...

    @abstractmethod
    def get_asset_aggregate_cashflows(self, asset: Asset, start: datetime, end: datetime, currency: str) -> float:
        """Return the expected sum of the cashflows generated by the Asset between start and end, in
        specified currency."""
        ...


class Aggregator(ABC):
    @abstractmethod
    def get_aggregation_keys(self, asset: Asset, impact: ImpactDistrib) -> List:
        ...


class DefaultAggregator(Aggregator):
    def get_aggregation_keys(self, asset: Asset, impact: ImpactDistrib) -> List:
        return [(impact.event_type.__name__), ("root")]


class FinancialModel:
    def __init__(
        self,
        hazard_model: Optional[HazardModel] = None,
        vulnerability_models: Optional[Dict[type, List[VulnerabilityModelBase]]] = None,
    ):
        self.hazard_model = calculation.get_default_hazard_model() if hazard_model is None else hazard_model
        self.vulnerability_models = (
            calculation.get_default_vulnerability_models() if vulnerability_models is None else vulnerability_models
        )

    """Calculates the financial impact on a list of assets."""

    def get_financial_impacts(
        self,
        assets: Sequence[Asset],
        *,
        data_provider: FinancialDataProvider,
        scenario: str,
        year: int,
        aggregator: Optional[Aggregator] = None,
        currency: str = "EUR",
        sims: int = 100000
    ):

        if aggregator is None:
            aggregator = DefaultAggregator()

        aggregation_pools: Dict[str, np.ndarray] = {}

        results = calculate_impacts(assets, self.hazard_model, self.vulnerability_models, scenario=scenario, year=year)
        # the impacts in the results are either fractional damage or a fractional disruption

        rg = np.random.Generator(np.random.MT19937(seed=111))

        for asset, result in results.items():
            # look up keys for results
            impact = result.impact
            keys = aggregator.get_aggregation_keys(asset, impact)
            # transform units of impact into currency for aggregation

            if impact.impact_type == ImpactType.damage:
                trans = data_provider.get_asset_value(asset, currency)
            else:  # impact.impact_type == ImpactType.disruption:
                trans = data_provider.get_asset_aggregate_cashflows(
                    asset, datetime(year, 1, 1), datetime(year, 12, 31), currency
                )

            # Monte-Carlo approach: note that if correlations of distributions are simple and model is otherwise linear
            # then calculation by closed-form expression is preferred
            loss = trans * self.uncorrelated_samples(impact, sims, rg)

            for key in keys:
                if key not in aggregation_pools:
                    aggregation_pools[key] = np.zeros(sims)
                aggregation_pools[key] += loss  # type: ignore

        measures = {}
        percentiles = [0, 10, 20, 40, 60, 80, 90, 95, 97.5, 99, 99.5, 99.9]
        for key, loss in aggregation_pools.items():
            measures[key] = {
                "percentiles": percentiles,
                "percentile_values": np.percentile(loss, percentiles),
                "mean": np.mean(loss),
            }

        return measures

    def uncorrelated_samples(self, impact: ImpactDistrib, samples: int, generator: np.random.Generator) -> np.ndarray:
        return impact.to_exceedance_curve().get_samples(generator.uniform(size=samples))