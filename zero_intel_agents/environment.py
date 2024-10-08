import os
import logging
from pydantic import BaseModel, Field, computed_field, model_validator
from typing import List, Tuple, Optional
from functools import cached_property
import matplotlib.pyplot as plt
from collections import defaultdict

from ziagents import ZIAgent, create_zi_agent

# Set up logger
logger = logging.getLogger(__name__)

class CurvePoint(BaseModel):
    quantity: float = Field(..., description="Quantity of goods")
    price: float = Field(..., description="Price of goods")

class BaseCurve(BaseModel):
    points: List[CurvePoint] = Field(..., description="List of points defining the curve")

    def get_x_y_values(self) -> Tuple[List[float], List[float]]:
        x_values = []
        y_values = []
        for point in self.points:
            x_values.extend([point.quantity, point.quantity])
            y_values.extend([point.price, point.price])
        return x_values, y_values

class InitialDemandCurve(BaseCurve):
    @model_validator(mode='after')
    def validate_monotonicity(self):
        sorted_points = sorted(self.points, key=lambda p: p.quantity)
        for i in range(1, len(sorted_points)):
            if sorted_points[i].price > sorted_points[i-1].price:
                
                raise ValueError("Initial demand curve must be monotonically decreasing")
        return self

class InitialSupplyCurve(BaseCurve):
    @model_validator(mode='after')
    def validate_monotonicity(self):
        sorted_points = sorted(self.points, key=lambda p: p.quantity)
        for i in range(1, len(sorted_points)):
            if sorted_points[i].price < sorted_points[i-1].price:
                raise ValueError("Initial supply curve must be monotonically increasing")
        return self



class Environment(BaseModel):
    agents: List[ZIAgent] = Field(..., description="List of all agents in the environment")

    @cached_property
    def buyers(self) -> List[ZIAgent]:
        return [agent for agent in self.agents if agent.is_buyer]

    @cached_property
    def sellers(self) -> List[ZIAgent]:
        return [agent for agent in self.agents if not agent.is_buyer]

    @computed_field
    @cached_property
    def initial_demand_curve(self) -> InitialDemandCurve:
        return self._generate_initial_demand_curve()

    @computed_field
    @cached_property
    def initial_supply_curve(self) -> InitialSupplyCurve:
        return self._generate_initial_supply_curve()

    @computed_field
    @property
    def current_demand_curve(self) -> BaseCurve:
        return self._generate_current_demand_curve()

    @computed_field
    @property
    def current_supply_curve(self) -> BaseCurve:
        return self._generate_current_supply_curve()

    def _generate_initial_demand_curve(self) -> InitialDemandCurve:
        aggregated_demand = defaultdict(float)
        for buyer in self.buyers:
            for quantity, value in buyer.preference_schedule.values.items():
                aggregated_demand[value] += quantity
        
        points = []
        cumulative_quantity = 0
        for price, quantity in sorted(aggregated_demand.items(), reverse=True):
            cumulative_quantity += quantity
            points.append(CurvePoint(quantity=cumulative_quantity, price=price))
        
        return InitialDemandCurve(points=points)

    def _generate_initial_supply_curve(self) -> InitialSupplyCurve:
        aggregated_supply = defaultdict(float)
        for seller in self.sellers:
            for quantity, cost in seller.preference_schedule.values.items():
                aggregated_supply[cost] += quantity
        
        points = []
        cumulative_quantity = 0
        for price, quantity in sorted(aggregated_supply.items()):
            cumulative_quantity += quantity
            points.append(CurvePoint(quantity=cumulative_quantity, price=price))
        
        return InitialSupplyCurve(points=points)

    def _generate_current_demand_curve(self) -> BaseCurve:
        aggregated_demand = defaultdict(float)
        for buyer in self.buyers:
            for quantity, value in buyer.preference_schedule.values.items():
                if buyer.allocation.goods < quantity:
                    aggregated_demand[value] += (quantity - buyer.allocation.goods)
        
        points = []
        cumulative_quantity = 0
        for price, quantity in sorted(aggregated_demand.items(), reverse=True):
            cumulative_quantity += quantity
            points.append(CurvePoint(quantity=cumulative_quantity, price=price))
        
        return BaseCurve(points=points)

    def _generate_current_supply_curve(self) -> BaseCurve:
        aggregated_supply = defaultdict(float)
        for seller in self.sellers:
            for quantity, cost in seller.preference_schedule.values.items():
                if seller.allocation.goods >= quantity:
                    aggregated_supply[cost] += seller.allocation.goods - quantity + 1
        
        points = []
        cumulative_quantity = 0
        for price, quantity in sorted(aggregated_supply.items()):
            cumulative_quantity += quantity
            points.append(CurvePoint(quantity=cumulative_quantity, price=price))
        
        return BaseCurve(points=points)

    @computed_field
    @property
    def remaining_trade_opportunities(self) -> int:
        potential_trades = 0
        for buyer in self.buyers:
            for seller in self.sellers:
                if buyer.allocation.cash > 0 and seller.allocation.goods > 0:
                    buyer_value = buyer.preference_schedule.get_value(buyer.allocation.goods + 1)
                    seller_cost = seller.preference_schedule.get_value(seller.allocation.goods)
                    if buyer_value > seller_cost and buyer.allocation.cash >= seller_cost:
                        potential_trades += 1
        return potential_trades

    @computed_field
    @property
    def remaining_surplus(self) -> float:
        remaining_surplus = 0.0
        for buyer in self.buyers:
            for seller in self.sellers:
                if buyer.allocation.cash > 0 and seller.allocation.goods > 0:
                    buyer_value = buyer.preference_schedule.get_value(buyer.allocation.goods + 1)
                    seller_cost = seller.preference_schedule.get_value(seller.allocation.goods)
                    if buyer_value > seller_cost:
                        remaining_surplus += (buyer_value - seller_cost)
        return remaining_surplus

    @computed_field
    @property
    def total_utility(self) -> float:
        return sum(agent.individual_surplus for agent in self.agents)

    @computed_field
    @property
    def ce_price(self) -> float:
        return self.calculate_equilibrium(initial=False)[0]

    @computed_field
    @property
    def ce_quantity(self) -> float:
        return self.calculate_equilibrium(initial=False)[1]

    @computed_field
    @property
    def ce_buyer_surplus(self) -> float:
        return self.calculate_equilibrium(initial=False)[2]

    @computed_field
    @property
    def ce_seller_surplus(self) -> float:
        return self.calculate_equilibrium(initial=False)[3]

    @computed_field
    @property
    def ce_total_surplus(self) -> float:
        return self.ce_buyer_surplus + self.ce_seller_surplus

    @computed_field
    @property
    def efficiency(self) -> float:
        extracted_surplus = self.total_utility
        theoretical_surplus = self.ce_total_surplus
        if theoretical_surplus <= 0:
            raise ValueError("Theoretical surplus is zero or negative")
        efficiency = extracted_surplus / theoretical_surplus
        if efficiency < 0:
            raise ValueError("Negative efficiency detected")
        return efficiency

    def get_agent(self, agent_id: int) -> Optional[ZIAgent]:
        """Retrieve an agent by their ID."""
        return next((agent for agent in self.agents if agent.id == agent_id), None)

    def print_market_state(self):
        logger.info("Market State:")
        for agent in self.agents:
            role = "Buyer" if agent.is_buyer else "Seller"
            logger.info(f"Agent {agent.id} ({role}):")
            logger.info(f"  Goods: {agent.allocation.goods}")
            logger.info(f"  Cash: {agent.allocation.cash:.2f}")
            logger.info(f"  Utility: {agent.individual_surplus:.2f}")
        logger.info(f"Total Market Utility: {self.total_utility:.2f}")
        logger.info(f"Remaining Trade Opportunities: {self.remaining_trade_opportunities}")
        logger.info(f"Remaining Surplus: {self.remaining_surplus:.2f}")
        logger.info(f"Market Efficiency: {self.efficiency:.2%}")

    def calculate_equilibrium(self, initial: bool = True) -> Tuple[float, float, float, float, float]:
        demand_curve = self.initial_demand_curve if initial else self.current_demand_curve
        supply_curve = self.initial_supply_curve if initial else self.current_supply_curve

        demand_points = sorted(demand_curve.points, key=lambda p: p.quantity)
        supply_points = sorted(supply_curve.points, key=lambda p: p.quantity)

        ce_quantity = 0
        ce_price = 0
        d_index = 0
        s_index = 0

        while d_index < len(demand_points) and s_index < len(supply_points):
            if demand_points[d_index].price >= supply_points[s_index].price:
                ce_quantity = min(demand_points[d_index].quantity, supply_points[s_index].quantity)
                ce_price = (demand_points[d_index].price + supply_points[s_index].price) / 2
                if demand_points[d_index].quantity < supply_points[s_index].quantity:
                    d_index += 1
                else:
                    s_index += 1
            else:
                break

        buyer_surplus = sum(max(p.price - ce_price, 0) * (p.quantity - prev_q)
                            for prev_q, p in zip([0] + [p.quantity for p in demand_points[:-1]], demand_points)
                            if p.quantity <= ce_quantity)

        seller_surplus = sum(max(ce_price - p.price, 0) * (p.quantity - prev_q)
                             for prev_q, p in zip([0] + [p.quantity for p in supply_points[:-1]], supply_points)
                             if p.quantity <= ce_quantity)

        total_surplus = buyer_surplus + seller_surplus

        return ce_price, ce_quantity, buyer_surplus, seller_surplus, total_surplus

    def plot_supply_demand_curves(self, initial: bool = True, save_location: Optional[str] = None):
        demand_curve = self.initial_demand_curve if initial else self.current_demand_curve
        supply_curve = self.initial_supply_curve if initial else self.current_supply_curve

        demand_x, demand_y = demand_curve.get_x_y_values()
        supply_x, supply_y = supply_curve.get_x_y_values()

        ce_price, ce_quantity, buyer_surplus, seller_surplus, total_surplus = self.calculate_equilibrium(initial)
        
        fig, ax = plt.subplots(figsize=(12, 8))
        
        ax.step(demand_x, demand_y, where='pre', label='Demand', color='blue', linestyle='-', linewidth=2)
        ax.step(supply_x, supply_y, where='pre', label='Supply', color='red', linestyle='-', linewidth=2)
        
        max_x = max(max(demand_x), max(supply_x))
        min_y = min(min(demand_y), min(supply_y))
        max_y = max(max(demand_y), max(supply_y))
        
        ax.set_xlim(1, max_x)  # Start x-axis from 1
        ax.set_ylim(min_y * 0.9, max_y * 1.1)

        ax.plot(ce_quantity, ce_price, 'go', markersize=10, label='Equilibrium')
        
        ax.axvline(x=ce_quantity, color='green', linestyle='--', label=f'CE Quantity: {ce_quantity}')
        ax.axhline(y=ce_price, color='purple', linestyle='--', label=f'CE Price: {ce_price:.2f}')

        ax.set_xlabel('Quantity', fontsize=12)
        ax.set_ylabel('Price', fontsize=12)
        ax.set_title('Supply and Demand Curves', fontsize=14)
        ax.legend(fontsize=10, loc='upper right')
        ax.grid(True, linestyle=':', alpha=0.7)

        ax.text(0.05, 0.95, f'Buyer Surplus: {buyer_surplus:.2f}', transform=ax.transAxes, fontsize=10, verticalalignment='top')
        ax.text(0.05, 0.90, f'Seller Surplus: {seller_surplus:.2f}', transform=ax.transAxes, fontsize=10, verticalalignment='top')
        ax.text(0.05, 0.85, f'Total Surplus: {total_surplus:.2f}', transform=ax.transAxes, fontsize=10, verticalalignment='top')

        plt.tight_layout()

        if save_location:
            file_name = "initial_supply_demand.png" if initial else "current_supply_demand.png"
            file_path = os.path.join(save_location, file_name)
            fig.savefig(file_path, dpi=300, bbox_inches='tight')
            logger.info(f"Plot saved to {file_path}")
        
        return fig

def generate_market_agents(
    num_agents: int = Field(..., description="Total number of agents to generate"),
    num_units: int = Field(..., description="Number of units for each agent"),
    buyer_base_value: int = Field(..., description="Base value for buyers"),
    seller_base_value: int = Field(..., description="Base value for sellers"),
    spread: float = Field(..., description="Maximum relative price spread")
) -> List[ZIAgent]:
    agents = []
    for i in range(num_agents):
        is_buyer = i < num_agents // 2
        base_value = buyer_base_value if is_buyer else seller_base_value
        
        agent = create_zi_agent(
            agent_id=i,
            is_buyer=is_buyer,
            num_units=num_units,
            base_value=base_value,
            initial_cash=1000 if is_buyer else 0,
            initial_goods=0 if is_buyer else num_units,
            max_relative_spread=spread
        )
        agents.append(agent)
    
    return agents

if __name__ == "__main__":
    # Generate test agents
    num_buyers = 5
    num_sellers = 5
    spread = 0.5

    agents = generate_market_agents(
        num_agents=num_buyers + num_sellers, 
        num_units=5, 
        buyer_base_value=100, 
        seller_base_value=80, 
        spread=spread
    )
    
    # Create the environment
    env = Environment(agents=agents)

    # Print initial market state
    env.print_market_state()

    # Plot initial supply and demand curves
    env.plot_supply_demand_curves(initial=True, save_location=".")

    # Simulate some trades (this is where you'd normally run your auction)
    # For demonstration, let's just modify some agent allocations
    for i in range(3):  # Simulate 3 trades
        buyer = env.buyers[i]
        seller = env.sellers[i]
        trade_price = (buyer.base_value + seller.base_value) / 2
        buyer.allocation.goods += 1
        buyer.allocation.cash -= trade_price
        seller.allocation.goods -= 1
        seller.allocation.cash += trade_price

    # Print final market state
    print("\nAfter simulated trades:")
    env.print_market_state()

    # Plot current supply and demand curves
    env.plot_supply_demand_curves(initial=False, save_location=".")

    # Print equilibrium values
    ce_price, ce_quantity, ce_buyer_surplus, ce_seller_surplus, ce_total_surplus = env.calculate_equilibrium(initial=False)
    print(f"\nCompetitive Equilibrium:")
    print(f"Price: {ce_price:.2f}")
    print(f"Quantity: {ce_quantity}")
    print(f"Buyer Surplus: {ce_buyer_surplus:.2f}")
    print(f"Seller Surplus: {ce_seller_surplus:.2f}")
    print(f"Total Surplus: {ce_total_surplus:.2f}")
    print(f"Market Efficiency: {env.efficiency:.2%}")