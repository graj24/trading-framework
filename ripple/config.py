import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DEFAULT_MAX_TWEETS = int(os.getenv("DEFAULT_MAX_TWEETS", 10))
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/Users/anantamanoranjan/Desktop/ripple/output")
