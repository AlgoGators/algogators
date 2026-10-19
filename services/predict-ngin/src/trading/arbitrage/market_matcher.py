"""
Cross-platform market matcher.

Identifies equivalent prediction markets across Polymarket and Kalshi
using text similarity (TF-IDF + cosine) to propose candidates, then HARD
contract-term checks (rule_matcher.validate_pair): same strike/threshold,
comparator, resolution date (+/- tolerance), polarity, and resolution
time/source. Text similarity alone produced false matches (different strikes,
different months: the old date boost fired on a shared year alone).

For families with structured tickers (Fed, elections, NFL/NBA/MLB/NHL games,
championship futures) prefer ``rule_matcher.RuleMatcher``.

Usage:
    from trading.arbitrage.market_matcher import MarketMatcher

    matcher = MarketMatcher()
    pairs = matcher.match(polymarket_markets_df, kalshi_markets_df)
    # pairs is a list of MatchedPair with confidence scores
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .rule_matcher import validate_pair


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MatchedPair:
    """A matched market pair across platforms."""

    pair_id: str
    polymarket_id: str
    kalshi_ticker: str
    polymarket_question: str
    kalshi_title: str
    similarity_score: float  # 0-1, text similarity
    confidence: float  # 0-1, overall match confidence
    match_method: str  # "tfidf", "exact", "manual"
    polymarket_volume: float = 0.0
    kalshi_volume: float = 0.0
    category: str = ""
    metadata: Dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"MatchedPair(pair_id={self.pair_id!r}, "
            f"score={self.confidence:.3f}, "
            f"poly={self.polymarket_question[:50]!r}, "
            f"kalshi={self.kalshi_title[:50]!r})"
        )


# ---------------------------------------------------------------------------
# Text preprocessing
# ---------------------------------------------------------------------------

_STOPWORDS: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "will", "would", "could", "should", "shall", "may", "might", "can",
    "do", "does", "did", "have", "has", "had", "having",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "about", "up", "down",
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most",
    "other", "some", "such", "no", "only", "own", "same", "than", "too",
    "very", "just", "because", "if", "when", "where", "how", "what",
    "which", "who", "whom", "this", "that", "these", "those",
    "it", "its", "he", "she", "they", "them", "we", "you", "i",
    "market", "price", "question", "contract", "event",
}


def _tokenize(text: str) -> List[str]:
    """Lowercase, strip punctuation, remove stopwords."""
    text = text.lower()
    # Replace common separators with spaces
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = text.split()
    return [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]


def _ngrams(tokens: List[str], n: int = 2) -> List[str]:
    """Generate character n-grams from token list for fuzzy matching."""
    joined = " ".join(tokens)
    return [joined[i : i + n] for i in range(max(0, len(joined) - n + 1))]


# ---------------------------------------------------------------------------
# TF-IDF implementation (sklearn sparse for large datasets)
# ---------------------------------------------------------------------------

def _tfidf_fit_transform(docs: List[List[str]]):
    """Fit TF-IDF on tokenized docs. Returns (matrix, is_sparse). Uses sklearn sparse."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        # Join tokens for sklearn; use tokenizer that splits on space (preserve our tokens)
        texts = [" ".join(d) for d in docs]
        vectorizer = TfidfVectorizer(
            tokenizer=lambda x: x.split(),
            lowercase=False,
            max_features=50000,
            sublinear_tf=True,
        )
        return vectorizer.fit_transform(texts), True
    except ImportError:
        return _tfidf_dense_fit_transform(docs), False


def _tfidf_dense_fit_transform(docs: List[List[str]]) -> np.ndarray:
    """Fallback dense TF-IDF (avoid for >50k docs)."""
    vocab: Dict[str, int] = {}
    for doc in docs:
        for token in doc:
            if token not in vocab:
                vocab[token] = len(vocab)
    n_docs = len(docs)
    n_vocab = len(vocab)
    if n_vocab == 0:
        return np.zeros((n_docs, 0))

    tf = np.zeros((n_docs, n_vocab), dtype=np.float64)
    for i, doc in enumerate(docs):
        for token in doc:
            if token in vocab:
                tf[i, vocab[token]] += 1
        if len(doc) > 0:
            tf[i] /= len(doc)
    df = (tf > 0).sum(axis=0).astype(np.float64)
    idf = np.log(n_docs / (1 + df))
    return tf * idf


def _cosine_similarity_matrix(A, B) -> np.ndarray:
    """Compute cosine similarity between rows of A and rows of B. Handles sparse matrices."""
    if A.shape[1] == 0 or B.shape[1] == 0:
        return np.zeros((A.shape[0], B.shape[0]))
    try:
        from sklearn.metrics.pairwise import cosine_similarity
        return cosine_similarity(A, B)
    except ImportError:
        A_np = np.asarray(A) if not isinstance(A, np.ndarray) else A
        B_np = np.asarray(B) if not isinstance(B, np.ndarray) else B
        A_norm = np.linalg.norm(A_np, axis=1, keepdims=True)
        B_norm = np.linalg.norm(B_np, axis=1, keepdims=True)
        A_norm = np.where(A_norm == 0, 1, A_norm)
        B_norm = np.where(B_norm == 0, 1, B_norm)
        return (A_np / A_norm) @ (B_np / B_norm).T


# ---------------------------------------------------------------------------
# Date extraction for temporal alignment
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    # "January 2025", "Feb 2025", etc.
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{4})",
    # "2025-01-15", "2025/01/15"
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
    # "01/15/2025"
    r"(\d{1,2})/(\d{1,2})/(\d{4})",
    # Standalone year
    r"\b(20\d{2})\b",
]


def _extract_dates(text: str) -> Set[str]:
    """Extract date references from text for temporal alignment."""
    text_lower = text.lower()
    dates: Set[str] = set()

    # Month + year
    month_map = {
        "jan": "01", "january": "01", "feb": "02", "february": "02",
        "mar": "03", "march": "03", "apr": "04", "april": "04",
        "may": "05", "jun": "06", "june": "06", "jul": "07", "july": "07",
        "aug": "08", "august": "08", "sep": "09", "september": "09",
        "oct": "10", "october": "10", "nov": "11", "november": "11",
        "dec": "12", "december": "12",
    }

    for match in re.finditer(_DATE_PATTERNS[0], text_lower):
        month_str = match.group(1)
        year = match.group(2)
        month_num = month_map.get(month_str, "00")
        dates.add(f"{year}-{month_num}")

    # ISO dates
    for match in re.finditer(_DATE_PATTERNS[1], text_lower):
        dates.add(f"{match.group(1)}-{match.group(2).zfill(2)}")

    # US dates
    for match in re.finditer(_DATE_PATTERNS[2], text_lower):
        dates.add(f"{match.group(3)}-{match.group(1).zfill(2)}")

    # Standalone years
    for match in re.finditer(_DATE_PATTERNS[3], text_lower):
        dates.add(match.group(1))

    return dates


# ---------------------------------------------------------------------------
# Main matcher
# ---------------------------------------------------------------------------

class MarketMatcher:
    """
    Match prediction markets across Polymarket and Kalshi.

    Uses TF-IDF cosine similarity on market questions/titles, boosted by
    temporal alignment (matching dates in text) and optionally validated
    by price correlation.

    Args:
        min_similarity: Minimum TF-IDF cosine similarity to consider (default: 0.25)
        min_confidence: Minimum combined confidence for a match (default: 0.40)
        date_boost: Extra confidence when date references align (default: 0.15)
        use_ngrams: Mix in character n-grams for fuzzy matching (default: True)
        ngram_weight: Weight for n-gram similarity vs token similarity (default: 0.3)
    """

    def __init__(
        self,
        min_similarity: float = 0.25,
        min_confidence: float = 0.40,
        date_boost: float = 0.15,
        use_ngrams: bool = True,
        ngram_weight: float = 0.3,
        hard_checks: bool = True,
        date_tolerance_days: float = 1.0,
        end_tolerance_days: float = 3.0,
        reject_on_source_mismatch: bool = True,
    ):
        self.min_similarity = min_similarity
        self.min_confidence = min_confidence
        self.date_boost = date_boost
        self.use_ngrams = use_ngrams
        self.ngram_weight = ngram_weight
        self.hard_checks = hard_checks
        self.date_tolerance_days = date_tolerance_days
        self.end_tolerance_days = end_tolerance_days
        self.reject_on_source_mismatch = reject_on_source_mismatch
        self.rejections: List[Dict] = []

        # Manual overrides: (polymarket_id, kalshi_ticker) pairs
        self._manual_matches: List[Tuple[str, str]] = []

    def add_manual_match(self, polymarket_id: str, kalshi_ticker: str) -> None:
        """Register a manual market match (bypasses similarity threshold)."""
        self._manual_matches.append((polymarket_id, kalshi_ticker))

    def match(
        self,
        polymarket_markets: pd.DataFrame,
        kalshi_markets: pd.DataFrame,
        max_pairs: int = 500,
    ) -> List[MatchedPair]:
        """
        Match markets across platforms.

        Args:
            polymarket_markets: DataFrame with at least 'id' and 'question' columns.
                               Optional: 'volume', 'slug', 'end_date'
            kalshi_markets: DataFrame with at least 'ticker' and 'title' columns.
                           Optional: 'volume', 'event_ticker', 'close_time', 'category'
            max_pairs: Maximum number of matched pairs to return.

        Returns:
            List of MatchedPair objects sorted by confidence (descending).
        """
        pairs: List[MatchedPair] = []

        # Normalize column names
        poly_df = self._normalize_polymarket(polymarket_markets)
        kalshi_df = self._normalize_kalshi(kalshi_markets)

        if poly_df.empty or kalshi_df.empty:
            return pairs

        # Add manual matches first
        manual_poly_ids: Set[str] = set()
        manual_kalshi_tickers: Set[str] = set()
        for poly_id, kalshi_ticker in self._manual_matches:
            poly_row = poly_df[poly_df["id"] == poly_id]
            kalshi_row = kalshi_df[kalshi_df["ticker"] == kalshi_ticker]
            if not poly_row.empty and not kalshi_row.empty:
                pr = poly_row.iloc[0]
                kr = kalshi_row.iloc[0]
                pairs.append(MatchedPair(
                    pair_id=f"manual_{poly_id}_{kalshi_ticker}",
                    polymarket_id=poly_id,
                    kalshi_ticker=kalshi_ticker,
                    polymarket_question=pr["question"],
                    kalshi_title=kr["title"],
                    similarity_score=1.0,
                    confidence=1.0,
                    match_method="manual",
                    polymarket_volume=pr.get("volume", 0) or 0,
                    kalshi_volume=kr.get("volume", 0) or 0,
                    category=kr.get("category", ""),
                ))
                manual_poly_ids.add(poly_id)
                manual_kalshi_tickers.add(kalshi_ticker)

        # Filter out manually matched from automatic matching
        poly_auto = poly_df[~poly_df["id"].isin(manual_poly_ids)].reset_index(drop=True)
        kalshi_auto = kalshi_df[~kalshi_df["ticker"].isin(manual_kalshi_tickers)].reset_index(drop=True)

        if poly_auto.empty or kalshi_auto.empty:
            return pairs

        # Tokenize
        poly_texts = poly_auto["question"].tolist()
        kalshi_texts = kalshi_auto["title"].tolist()

        poly_tokens = [_tokenize(t) for t in poly_texts]
        kalshi_tokens = [_tokenize(t) for t in kalshi_texts]

        # TF-IDF on word tokens (sklearn sparse for large datasets)
        all_tokens = poly_tokens + kalshi_tokens
        tfidf_matrix, _ = _tfidf_fit_transform(all_tokens)

        n_poly = len(poly_tokens)
        poly_vecs = tfidf_matrix[:n_poly]
        kalshi_vecs = tfidf_matrix[n_poly:]

        # Optionally precompute n-gram vectors for chunked similarity
        poly_ng = kalshi_ng = None
        w = 0.0
        if self.use_ngrams:
            poly_ngrams = [_ngrams(t, 3) for t in poly_tokens]
            kalshi_ngrams = [_ngrams(t, 3) for t in kalshi_tokens]
            all_ngrams = poly_ngrams + kalshi_ngrams
            ngram_matrix, _ = _tfidf_fit_transform(all_ngrams)
            poly_ng = ngram_matrix[:n_poly]
            kalshi_ng = ngram_matrix[n_poly:]
            w = self.ngram_weight
            del ngram_matrix

        # Extract date references for boosting
        poly_dates = [_extract_dates(t) for t in poly_texts]
        kalshi_dates = [_extract_dates(t) for t in kalshi_texts]

        # Find best matches greedily (each market matched at most once)
        used_poly: Set[int] = set()
        used_kalshi: Set[int] = set()

        # Compute similarity in chunks to avoid OOM (full matrix = 268k x 100k = 200 GB)
        batch_size = 1000  # 1000 x n_kalshi = ~800 MB per block
        candidates: List[Tuple[float, int, int]] = []
        n_batches = (n_poly + batch_size - 1) // batch_size
        for b, start in enumerate(range(0, n_poly, batch_size)):
            if n_batches > 1 and b % 10 == 0:
                print(f"    Matching batch {b+1}/{n_batches}...", flush=True)
            end = min(start + batch_size, n_poly)
            sim_block = _cosine_similarity_matrix(poly_vecs[start:end], kalshi_vecs)
            if poly_ng is not None and kalshi_ng is not None:
                ngram_block = _cosine_similarity_matrix(poly_ng[start:end], kalshi_ng)
                sim_block = (1 - w) * sim_block + w * ngram_block
                del ngram_block
            for i in range(sim_block.shape[0]):
                for j in range(sim_block.shape[1]):
                    s = sim_block[i, j]
                    if s >= self.min_similarity:
                        candidates.append((float(s), start + i, j))
            del sim_block

        candidates.sort(key=lambda x: x[0], reverse=True)

        for sim_score, pi, ki in candidates:
            if pi in used_poly or ki in used_kalshi:
                continue
            if len(pairs) >= max_pairs:
                break

            # Confidence = similarity + date boost. A shared bare year is NOT
            # evidence (it boosted "March 2025" vs "June 2025"); require a
            # shared month-level (YYYY-MM) reference.
            confidence = float(sim_score)
            shared = poly_dates[pi] & kalshi_dates[ki]
            if any("-" in d for d in shared):
                confidence = min(confidence + self.date_boost, 1.0)

            if confidence < self.min_confidence:
                continue

            pr = poly_auto.iloc[pi]
            kr = kalshi_auto.iloc[ki]

            validation = None
            if self.hard_checks:
                v = self._validate(pr, kr)
                if not v.ok:
                    self.rejections.append({
                        "polymarket_id": str(pr["id"]), "kalshi_ticker": str(kr["ticker"]),
                        "similarity": float(sim_score), "reasons": v.reasons, "flags": v.flags,
                    })
                    continue
                validation = v

            pair = MatchedPair(
                pair_id=f"auto_{pr['id']}_{kr['ticker']}",
                polymarket_id=str(pr["id"]),
                kalshi_ticker=str(kr["ticker"]),
                polymarket_question=str(pr["question"]),
                kalshi_title=str(kr["title"]),
                similarity_score=float(sim_score),
                confidence=confidence,
                match_method="tfidf",
                polymarket_volume=float(pr.get("volume", 0) or 0),
                kalshi_volume=float(kr.get("volume", 0) or 0),
                category=str(kr.get("category", "")),
                metadata=(
                    {"validation": "ok", "flags": validation.flags}
                    if validation is not None else {"validation": "skipped"}
                ),
            )
            pairs.append(pair)
            used_poly.add(pi)
            used_kalshi.add(ki)

        # Sort by confidence
        pairs.sort(key=lambda p: p.confidence, reverse=True)
        return pairs

    def match_to_dataframe(
        self,
        polymarket_markets: pd.DataFrame,
        kalshi_markets: pd.DataFrame,
        max_pairs: int = 500,
    ) -> pd.DataFrame:
        """Match markets and return results as a DataFrame."""
        pairs = self.match(polymarket_markets, kalshi_markets, max_pairs)
        if not pairs:
            return pd.DataFrame()

        rows = []
        for p in pairs:
            rows.append({
                "pair_id": p.pair_id,
                "polymarket_id": p.polymarket_id,
                "kalshi_ticker": p.kalshi_ticker,
                "polymarket_question": p.polymarket_question,
                "kalshi_title": p.kalshi_title,
                "similarity_score": p.similarity_score,
                "confidence": p.confidence,
                "match_method": p.match_method,
                "polymarket_volume": p.polymarket_volume,
                "kalshi_volume": p.kalshi_volume,
                "category": p.category,
            })
        return pd.DataFrame(rows)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _validate(self, pr: pd.Series, kr: pd.Series):
        """Hard contract-term checks for a candidate pair."""
        k_text = str(kr.get("title", ""))
        sub = kr.get("subtitle")
        if isinstance(sub, str) and sub.strip(" /"):
            k_text = f"{k_text} {sub}"
        pm_meta = {c: pr.get(c) for c in ("description", "resolution_source") if isinstance(pr.get(c), str)}
        k_meta = {c: kr.get(c) for c in ("rules_primary", "rules_secondary") if isinstance(kr.get(c), str)}

        def _ts(v):
            try:
                t = pd.to_datetime(v, utc=True, errors="coerce")
                return None if pd.isna(t) else t
            except Exception:
                return None

        pm_end = _ts(pr.get("end_date")) if "end_date" in pr.index else None
        k_end = None
        for c in ("expiration_time", "close_time"):
            if c in kr.index and _ts(kr.get(c)) is not None:
                k_end = _ts(kr.get(c))
                break
        return validate_pair(
            str(pr["question"]), k_text, pm_meta, k_meta, pm_end, k_end,
            date_tolerance_days=self.date_tolerance_days,
            end_tolerance_days=self.end_tolerance_days,
            reject_on_source_mismatch=self.reject_on_source_mismatch,
        )

    @staticmethod
    def _normalize_polymarket(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize Polymarket markets DataFrame to expected columns."""
        out = df.copy()

        # Ensure 'id' column
        if "id" not in out.columns:
            for alt in ("market_id", "condition_id", "slug"):
                if alt in out.columns:
                    out["id"] = out[alt].astype(str)
                    break
            else:
                return pd.DataFrame()

        # Ensure 'question' column
        if "question" not in out.columns:
            for alt in ("title", "name", "description"):
                if alt in out.columns:
                    out["question"] = out[alt].astype(str)
                    break
            else:
                return pd.DataFrame()

        out["id"] = out["id"].astype(str)
        out["question"] = out["question"].fillna("").astype(str)
        return out

    @staticmethod
    def _normalize_kalshi(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize Kalshi markets DataFrame to expected columns."""
        out = df.copy()

        # Ensure 'ticker' column
        if "ticker" not in out.columns:
            for alt in ("market_id", "id"):
                if alt in out.columns:
                    out["ticker"] = out[alt].astype(str)
                    break
            else:
                return pd.DataFrame()

        # Ensure 'title' column
        if "title" not in out.columns:
            for alt in ("question", "name", "subtitle"):
                if alt in out.columns:
                    out["title"] = out[alt].astype(str)
                    break
            else:
                return pd.DataFrame()

        out["ticker"] = out["ticker"].astype(str)
        out["title"] = out["title"].fillna("").astype(str)
        return out

    def get_parameters(self) -> Dict:
        """Return current configuration for reproducibility."""
        return {
            "min_similarity": self.min_similarity,
            "min_confidence": self.min_confidence,
            "date_boost": self.date_boost,
            "use_ngrams": self.use_ngrams,
            "ngram_weight": self.ngram_weight,
            "hard_checks": self.hard_checks,
            "date_tolerance_days": self.date_tolerance_days,
            "end_tolerance_days": self.end_tolerance_days,
            "manual_matches": len(self._manual_matches),
        }
