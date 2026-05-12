import requests
import yfinance as yf
import os
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

HEADERS = {"User-Agent": "stock-sentiment/1.0 (personal research tool)"}

class StockDataCollector:
    def get_reddit_posts(self, stock_symbol: str, limit: int = 10) -> List[Dict]:
        posts = []
        per_sub = limit // 3 + 1
        for subreddit in ["stocks", "wallstreetbets", "investing"]:
            url = f"https://www.reddit.com/r/{subreddit}/search.json?q={stock_symbol}&sort=new&limit={per_sub}&restrict_sr=1"
            try:
                data = requests.get(url, headers=HEADERS, timeout=10).json()
                for child in data.get("data", {}).get("children", []):
                    p = child["data"]
                    posts.append({
                        "source": "reddit",
                        "text": f"{p['title']}. {p.get('selftext','')[:200]}".strip(),
                        "created_at": str(p["created_utc"]),
                        "score": p.get("score", 0)
                    })
            except Exception:
                pass
        return posts[:limit]

    def get_yahoo_news(self, stock_symbol: str, limit: int = 10) -> List[Dict]:
        ticker = yf.Ticker(stock_symbol)
        news = ticker.news or []
        return [
            {
                "source": "yahoo_finance",
                "text": item.get("content", {}).get("title", ""),
                "created_at": str(item.get("content", {}).get("pubDate", "")),
                "score": 0
            }
            for item in news[:limit]
            if item.get("content", {}).get("title")
        ]

    def search_stock_tweets(self, stock_symbol: str, max_results: int = 10) -> List[Dict]:
        """Fetch from both Reddit and Yahoo Finance."""
        half = max_results // 2
        yahoo = self.get_yahoo_news(stock_symbol, half + max_results % 2)
        reddit = self.get_reddit_posts(stock_symbol, half)
        combined = yahoo + reddit
        print(f"  Yahoo Finance: {len(yahoo)} articles, Reddit: {len(reddit)} posts")
        return combined[:max_results]
