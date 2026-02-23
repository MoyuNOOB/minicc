import os
from typing import Literal

try:
    from tavily import TavilyClient
except Exception:
    TavilyClient = None

tavily_api_key = os.getenv("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=tavily_api_key) if (TavilyClient and tavily_api_key) else None

def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search"""
    if TavilyClient is None:
        return "Error: tavily package is not installed. Please install tavily-python."
    if tavily_client is None:
        return "Error: TAVILY_API_KEY is not set"
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )
