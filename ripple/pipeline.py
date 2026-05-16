import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .twitter_collector import StockDataCollector as TwitterCollector
from .sentiment_analyzer import SentimentAnalyzer

class StockSentimentPipeline:
    def __init__(self):
        self.twitter = TwitterCollector()
        self.analyzer = SentimentAnalyzer()
    
    def run(self, stock_symbol: str, max_tweets: int = 10) -> Dict:
        """Run the complete sentiment analysis pipeline."""
        print(f"Fetching tweets for ${stock_symbol}...")
        tweets = self.twitter.search_stock_tweets(stock_symbol, max_tweets)
        
        if not tweets:
            return {"error": f"No tweets found for ${stock_symbol}"}
        
        print(f"Analyzing sentiment for {len(tweets)} tweets...")
        sentiments = self.analyzer.analyze_batch([t["text"] for t in tweets])
        
        # Combine results
        results = []
        for tweet, sentiment in zip(tweets, sentiments):
            results.append({
                "source": tweet.get("source", "unknown"),
                "text": tweet["text"],
                "created_at": tweet["created_at"],
                "score": tweet.get("score", 0),
                "sentiment": sentiment
            })
        
        # Calculate average sentiment
        avg_sentiment = self._calculate_average_sentiment(sentiments)
        
        return {
            "stock_symbol": stock_symbol,
            "timestamp": datetime.now().isoformat(),
            "total_tweets": len(tweets),
            "tweets": results,
            "summary": {
                "average_sentiment": avg_sentiment,
                "positive_score": avg_sentiment.get("Positive", 0),
                "negative_score": avg_sentiment.get("Negative", 0),
                "neutral_score": avg_sentiment.get("Neutral", 0)
            }
        }
    
    def _calculate_average_sentiment(self, sentiments: List[Dict]) -> Dict:
        """Calculate average sentiment scores."""
        if not sentiments:
            return {}
        
        labels = sentiments[0].keys()
        averages = {}
        
        for label in labels:
            scores = [s.get(label, 0) for s in sentiments]
            averages[label] = round(sum(scores) / len(scores), 2)
        
        return averages
    
    def export_to_json(self, results: Dict, filename: str = None) -> str:
        """Export results to JSON file."""
        if filename is None:
            filename = f"sentiment_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        output_dir = Path(__file__).resolve().parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / filename

        with open(filepath, 'w') as f:
            json.dump(results, f, indent=2)

        return str(filepath)
