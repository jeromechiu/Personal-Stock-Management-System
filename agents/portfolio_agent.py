"""Portfolio tracking agent and task."""

from crewai import Agent, Task

from agents.market_analyst import SHARED_ANALYSIS_FOOTER


def create_portfolio_agent(llm) -> tuple[Agent, Task]:
    agent = Agent(
        role="Portfolio Agent",
        goal="Assess portfolio allocation, cost basis, and unrealized gains or losses.",
        backstory="A careful portfolio tracker that protects against unsupported claims.",
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    task = Task(
        description="",
        expected_output="Portfolio health and concentration analysis.",
        agent=agent,
    )
    return agent, task


def configure_portfolio_task(
    task: Task,
    portfolio: list[dict],
    portfolio_summary: dict,
) -> None:
    task.description = (
        "請以繁體中文檢視各持股的股數、成本、最新價格、市值與未實現損益；"
        "同時參考該股票日線、週線、月線、年線趨勢，以及各週期 KD 與均線位置；"
        "指出部位風險，不可補造資料。總資產、市值與損益以程式提供的"
        "確定數值為準，不要自行重算或把損益重複加進總資產。\n"
        f"程式計算的資產摘要：{repr(portfolio_summary)}\n"
        f"投資組合快照：{repr(portfolio)}\n"
        f"{SHARED_ANALYSIS_FOOTER}"
    )
