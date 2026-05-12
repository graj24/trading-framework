from transformers import pipeline
from typing import List, Dict

class SentimentAnalyzer:
    def __init__(self):
        # Use FinBERT for financial sentiment analysis
        self.classifier = pipeline(
            "text-classification",
            model="yiyanghkust/finbert-tone",
            top_k=None
        )
    
    def analyze_sentiment(self, text: str) -> Dict:
        """Analyze sentiment and return confidence scores as percentages."""
        results = self.classifier(text)[0]
        
        # Convert to percentage with 2 decimal places
        sentiment_scores = {
            item['label']: round(item['score'] * 100, 2)
            for item in results
        }
        
        return sentiment_scores
    
    def analyze_batch(self, texts: List[str]) -> List[Dict]:
        """Analyze multiple texts."""
        return [self.analyze_sentiment(text) for text in texts]
