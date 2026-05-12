from transformers import pipeline
from typing import List, Dict

_summariser = None

def _get_summariser():
    global _summariser
    if _summariser is None:
        _summariser = pipeline("summarization", model="facebook/bart-large-cnn",
                               max_length=60, min_length=20, truncation=True)
    return _summariser

def _maybe_summarise(text: str) -> str:
    """Summarise text longer than 100 words before scoring."""
    if len(text.split()) > 100:
        try:
            result = _get_summariser()(text[:1024])[0]["summary_text"]
            return result
        except Exception:
            pass
    return text


class SentimentAnalyzer:
    def __init__(self):
        # ProsusAI/finbert — trained on financial news (Reuters, Bloomberg)
        # More accurate than yiyanghkust/finbert-tone for news headlines
        self.classifier = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            top_k=None
        )

    def analyze_sentiment(self, text: str) -> Dict:
        """Summarise if long, then score with FinBERT. Returns % confidence scores."""
        text = _maybe_summarise(text)
        results = self.classifier(text[:512])[0]
        return {
            item['label'].capitalize(): round(item['score'] * 100, 2)
            for item in results
        }

    def analyze_batch(self, texts: List[str]) -> List[Dict]:
        return [self.analyze_sentiment(t) for t in texts]
