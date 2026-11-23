"""Read-only finance tools exposed through the Model Context Protocol."""

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

from database import get_user_portfolio, initialize_database
from main import fetch_stock_price


mcp = FastMCP("finance-tools")


@mcp.tool()
def fetch_stock_price_tool(symbol: str) -> dict:
    """Fetch the latest available market price for a ticker symbol."""
    return fetch_stock_price(symbol)


@mcp.tool()
def get_user_portfolio_tool() -> list[dict]:
    """Get remote MariaDB holdings, average costs, quantities, and currencies."""
    return get_user_portfolio()


if __name__ == "__main__":
    load_dotenv()
    initialize_database()
    mcp.run(transport="stdio")
