"""Market analyst agent and task."""

from crewai import Agent, Task

from agents.decision_models import MarketAnalysis


SHARED_ANALYSIS_FOOTER = (
    "只能使用提供的資料，不可捏造價格、事件或持倉。技術分析須依據提供的"
    "三年股價歷史；宏觀分析須參考美國聯準會利率政策及走向、國際油價及費半指數 "
    "(^SOX)。必要資料缺漏或過期時，應降低信心並優先選擇持平。說明數據依據與不確定性。"
)


def create_market_analyst(llm) -> tuple[Agent, Task]:
    agent = Agent(
        role="Market Analyst",
        goal="Analyze supplied market prices and identify actionable signals.",
        backstory="A disciplined financial market analyst focused on evidence.",
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    task = Task(
        description="",
        expected_output=(
            "Structured market signals for every tracked ticker, with a reason "
            "grounded in the supplied three-year price and macro data."
        ),
        agent=agent,
        output_pydantic=MarketAnalysis,
    )
    return agent, task


def configure_market_task(
    task: Task,
    markets: list[dict],
    macro_context: dict,
) -> None:
    task.description = (
        "請以繁體中文分析每一檔持股標的。採用三年、一年、半年、三個月、"
        "一個月與五日報酬、波動度、最大回撤及 52 週區間等歷史統計；"
        "並分別判讀日線、週線、月線、年線 OHLC 趨勢、均線與 KD 指標，"
        "說明不同週期訊號是否一致。年線 KD 若歷史資料不足，必須明確說明。"
        "同時分析美國聯準會利率政策及走向、國際油價與費半指數的方向。"
        "不得將相關性描述為因果，也不得把歷史表現當成未來保證。"
        f"\n股票歷史與最新報價：{repr(markets)}\n"
        f"宏觀指標與聯準會利率歷史：{repr(macro_context)}\n"
        f"{SHARED_ANALYSIS_FOOTER}"
    )
