"""Portfolio manager agent and task."""

from crewai import Agent, Task

from agents.decision_models import PortfolioDecision


def create_portfolio_manager(llm) -> tuple[Agent, Task]:
    agent = Agent(
        role="Portfolio Manager",
        goal="Maximize long-term risk-adjusted simulated portfolio returns using evidence-based decisions.",
        backstory=(
            "A disciplined, risk-aware portfolio manager who may reject the "
            "market signal, preserves capital when evidence is weak, and never "
            "treats a simulated order as a real brokerage order."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    task = Task(
        description="",
        expected_output=(
            "Structured portfolio decisions in Traditional Chinese. Return "
            "one final decision per holding, including market signal, manager "
            "action, whole 1,000-share lots, and rationale."
        ),
        agent=agent,
        output_pydantic=PortfolioDecision,
    )
    return agent, task


def configure_manager_task(
    task: Task,
    markets: list[dict],
    portfolio: list[dict],
    portfolio_summary: dict,
    macro_context: dict,
    available_cash: float,
    remaining_orders: int,
    trading_window_open: bool,
    required_data_available: bool,
) -> None:
    task.description = (
        "overview 欄位只寫策略摘要，不要自行計算或重述總資產、市值、成本或損益；"
        "這些金額由程式直接計算並另外列出。"
        "請以繁體中文綜合市場分析師與投資組合分析師的結果，逐檔輸出"
        "市場訊號、你是否採納、最終動作與原因。可拒絕市場分析師的建議；"
        "以提高長期風險調整後模擬報酬為目標，但不得為了交易次數而強迫下單，"
        "證據不足時選擇 HOLD。ADD/REDUCE 必須指定正整數張數，每張 1000 股；"
        "HOLD 的張數必須為 0。ADD 不可超過可用現金，REDUCE 不可超過持有張數。"
        "每日委託剩餘次數是硬上限，每一檔一次 ADD/REDUCE算一筆委託。"
        "如果交易時段未開放或必需市場資料不完整，所有最終動作必須為 HOLD。"
        "資產總額、市值、成本與損益必須以程式計算的資產摘要為準，"
        "不可自行心算、修改或重複加計未實現損益；最終報告會另列準確數值。"
        "請綜合三年價格變化，以及日線、週線、月線、年線和 KD/均線訊號；"
        "並使用美國聯準會利率政策及走向、國際油價與費半指數，"
        "不得假稱取得未提供的即時新聞或國際事件。所有理由需具體列出數據依據、"
        "主要風險與是否採納市場訊號。\n"
        f"台灣交易時段是否開放：{trading_window_open}\n"
        f"必需市場資料是否完整：{required_data_available}\n"
        f"今日剩餘委託筆數：{remaining_orders}（每日最多 3 筆）\n"
        f"模擬可用現金：TWD {available_cash:.2f}\n"
        f"程式計算的資產摘要：{repr(portfolio_summary)}\n"
        f"股票歷史與報價：{repr(markets)}\n"
        f"宏觀指標：{repr(macro_context)}\n"
        f"投資組合：{repr(portfolio)}"
    )
