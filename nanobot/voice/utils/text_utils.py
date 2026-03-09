"""
Text processing utilities for TTS.

Provides text cleaning, normalization, and segmentation for speech synthesis.
"""

import re
import unicodedata
from typing import List, Tuple


class MarkdownCleaner:
    """
    Clean markdown formatting from text for TTS synthesis.
    """
    
    @staticmethod
    def clean_markdown(text: str) -> str:
        """
        Remove markdown formatting from text.
        
        Args:
            text: Input text with potential markdown formatting
            
        Returns:
            Clean text suitable for TTS
        """
        if not text:
            return ""
        
        # Remove code blocks first (they may contain patterns that match other rules)
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Remove images ![alt](url) -> alt or empty
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
        
        # Remove links [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # Remove bold/italic
        text = re.sub(r'\*{3}([^*]+)\*{3}', r'\1', text)  # ***bold italic***
        text = re.sub(r'\*{2}([^*]+)\*{2}', r'\1', text)  # **bold**
        text = re.sub(r'\*([^*]+)\*', r'\1', text)        # *italic*
        text = re.sub(r'_{3}([^_]+)_{3}', r'\1', text)    # ___bold italic___
        text = re.sub(r'_{2}([^_]+)_{2}', r'\1', text)    # __bold__
        text = re.sub(r'_([^_]+)_', r'\1', text)          # _italic_
        
        # Remove strikethrough
        text = re.sub(r'~~([^~]+)~~', r'\1', text)
        
        # Remove headers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # Remove list markers
        text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Remove blockquotes
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        
        # Remove horizontal rules
        text = re.sub(r'^[-*_]{3,}$', '', text, flags=re.MULTILINE)
        
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        text = text.strip()
        
        return text


class TextProcessor:
    """
    Text processing utilities for TTS.
    """
    
    # Punctuation sets for sentence segmentation
    PUNCTUATIONS = ("。", "？", "?", "！", "!", "；", ";", "：")
    FIRST_SENTENCE_PUNCTUATIONS = (
        "，", "~", "、", ",", "。", "？", "?", "！", "!", "；", ";", "："
    )
    
    @staticmethod
    def remove_punctuation(text: str) -> str:
        """
        Remove punctuation from text.
        
        Args:
            text: Input text
            
        Returns:
            Text without punctuation
        """
        result = []
        for char in text:
            category = unicodedata.category(char)
            if not category.startswith('P'):
                result.append(char)
        return ''.join(result).strip()
    
    @staticmethod
    def remove_punctuation_and_emoji(text: str) -> str:
        """
        Remove punctuation and emojis from text.
        
        Args:
            text: Input text
            
        Returns:
            Text without punctuation and emojis
        """
        result = []
        for char in text:
            category = unicodedata.category(char)
            # Skip punctuation (P*), symbols (S*), and other (So includes emoji)
            if not category.startswith('P') and not category.startswith('S'):
                result.append(char)
        return ''.join(result).strip()
    
    @staticmethod
    def get_text_length_without_punctuation(text: str) -> Tuple[int, str]:
        """
        Get text length excluding punctuation.
        
        Args:
            text: Input text
            
        Returns:
            Tuple of (length, cleaned_text)
        """
        cleaned = TextProcessor.remove_punctuation(text)
        return len(cleaned), cleaned
    
    @staticmethod
    def segment_text(
        text: str,
        is_first_sentence: bool = True,
        max_length: int = 100,
    ) -> List[str]:
        """
        Segment text into TTS-friendly chunks.
        
        Args:
            text: Input text
            is_first_sentence: Whether this is the first segment
            max_length: Maximum segment length
            
        Returns:
            List of text segments
        """
        if not text:
            return []
        
        # Choose punctuation set
        puncts = (
            TextProcessor.FIRST_SENTENCE_PUNCTUATIONS 
            if is_first_sentence 
            else TextProcessor.PUNCTUATIONS
        )
        
        segments = []
        current = ""
        
        for char in text:
            current += char
            
            # Check for segmentation points
            if char in puncts:
                if current.strip():
                    segments.append(current.strip())
                current = ""
            elif len(current) >= max_length:
                # Force segment at max length
                if current.strip():
                    segments.append(current.strip())
                current = ""
        
        # Add remaining text
        if current.strip():
            segments.append(current.strip())
        
        return segments
    
    @staticmethod
    def normalize_whitespace(text: str) -> str:
        """
        Normalize whitespace in text.
        
        Args:
            text: Input text
            
        Returns:
            Text with normalized whitespace
        """
        # Replace various whitespace with single space
        text = re.sub(r'[\t\r\f\v]+', ' ', text)
        # Normalize multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Normalize multiple spaces
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()
    
    @staticmethod
    def contains_chinese(text: str) -> bool:
        """
        Check if text contains Chinese characters.
        
        Args:
            text: Input text
            
        Returns:
            True if text contains Chinese characters
        """
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False
    
    @staticmethod
    def estimate_speech_duration(text: str, chars_per_second: float = 4.0) -> float:
        """
        Estimate speech duration for text.
        
        Args:
            text: Input text
            chars_per_second: Average speaking rate
            
        Returns:
            Estimated duration in seconds
        """
        # Clean text
        cleaned = TextProcessor.remove_punctuation(text)
        return len(cleaned) / chars_per_second
