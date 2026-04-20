"""Skill trigger engine - handles auto-matching and manual invocation."""

import logging
import re
import unicodedata
from typing import Optional

from .registry import SkillsRegistry, get_registry

logger = logging.getLogger(__name__)

# Threshold for auto-trigger matching (0.0-1.0)
DEFAULT_THRESHOLD = 0.3


def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, remove accents, collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_keywords(text: str, threshold: float = 0.5) -> set[str]:
    """Extract meaningful keywords from text for matching."""
    normalized = _normalize(text)
    tokens = normalized.split()
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "shall",
        "should", "may", "might", "can", "could", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more", "most",
        "other", "some", "such", "no", "only", "same", "than", "too", "very",
        "just", "about", "above", "after", "again", "below", "between", "it",
        "its", "if", "then", "that", "this", "these", "those", "what", "which",
        "who", "whom", "when", "where", "why", "how", "my", "your", "his",
        "her", "our", "their", "me", "you", "him", "we", "they", "am",
    }
    # Keep tokens that are >= 3 chars and not in stop words
    kept = [t for t in tokens if len(t) >= 3 and t not in stop_words]
    return set(kept)


def _fuzzy_match(text: str, keywords: set[str]) -> float:
    """
    Calculate a match score (0.0-1.0) based on keyword overlap.
    Score = number of matched keywords / total keywords.
    Returns 0.0 if no keywords provided.
    """
    if not keywords:
        return 0.0
    normalized = _normalize(text)
    words = set(normalized.split())
    matched = 0
    for kw in keywords:
        # Direct word match
        if kw in words:
            matched += 1
        # Partial token match
        elif any(kw in token or token in kw for token in words):
            matched += 0.5
    return matched / len(keywords)


def match_skill(message: str, registry: SkillsRegistry, threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """
    Auto-trigger: match message against skill descriptions.

    Returns list of ({skill_name, confidence}) tuples, sorted by confidence descending.
    Only includes skills above the threshold.
    """
    if not message:
        return []

    query_keywords = extract_keywords(message, threshold=0.5)
    if not query_keywords:
        return []

    results = []
    for name, description in registry.get_all_descriptions().items():
        confidence = _fuzzy_match(description, query_keywords)
        if confidence >= threshold:
            results.append({"skill_name": name, "confidence": confidence})

    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results


def match_slash_command(command: str, registry: SkillsRegistry) -> Optional[str]:
    """Match a slash command to a skill name."""
    skill_name = command.strip().lower()
    if skill_name in registry.get_all_names():
        return skill_name
    return None
