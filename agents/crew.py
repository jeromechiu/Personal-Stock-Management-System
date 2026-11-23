"""Crew assembly for portfolio analysis."""

from crewai import Crew, Process

from agents.llm_config import create_llm
from agents.market_analyst import create_market_analyst, configure_market_task
from agents.portfolio_agent import create_portfolio_agent, configure_portfolio_task
from agents.portfolio_manager import create_portfolio_manager, configure_manager_task


def create_crew(
    markets: list[dict],
    portfolio: list[dict],
    portfolio_summary: dict,
    macro_context: dict,
    available_cash: float,
    remaining_orders: int,
    trading_window_open: bool,
    required_data_available: bool,
) -> Crew:
    llm = create_llm()

    market_agent, market_task = create_market_analyst(llm)
    portfolio_agent, portfolio_task = create_portfolio_agent(llm)
    manager_agent, manager_task = create_portfolio_manager(llm)

    configure_market_task(market_task, markets, macro_context)
    configure_portfolio_task(portfolio_task, portfolio, portfolio_summary)
    configure_manager_task(
        manager_task,
        markets,
        portfolio,
        portfolio_summary,
        macro_context,
        available_cash,
        remaining_orders,
        trading_window_open,
        required_data_available,
    )
    manager_task.context = [market_task, portfolio_task]

    return Crew(
        agents=[market_agent, portfolio_agent, manager_agent],
        tasks=[market_task, portfolio_task, manager_task],
        process=Process.sequential,
        verbose=False,
    )
