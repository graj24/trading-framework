import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DEFAULT_MAX_TWEETS = int(os.getenv("DEFAULT_MAX_TWEETS", 10))
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"))
