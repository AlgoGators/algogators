"""
Research modules for prediction market strategy development.

Contains comprehensive analysis programs that generate reports
for building more complex strategies.

Modules:
- whale_features: ML feature engineering for whale detection
- regime_detection: Market regime classification
- ensemble_research: Multi-strategy combination analysis
- alpha_discovery: Signal mining and validation
"""

from pathlib import Path

RESEARCH_OUTPUT_DIR = Path("data/research_reports")
RESEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
