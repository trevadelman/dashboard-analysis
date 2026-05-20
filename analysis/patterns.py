"""
Chart pattern recognition for technical analysis.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class ChartPatterns:
    """Class for recognizing chart patterns."""
    
    @staticmethod
    def find_swing_highs(data, window=5):
        """
        Find swing highs in price data.
        
        Args:
            data (pandas.DataFrame): Price data
            window (int): Window size for swing high detection
        
        Returns:
            pandas.Series: Boolean series indicating swing highs
        """
        # Create a rolling window view of the high prices
        highs = data['high'].rolling(window=window*2+1, center=True).apply(
            lambda x: x[window] == max(x), raw=True
        )
        
        return highs
    
    @staticmethod
    def find_swing_lows(data, window=5):
        """
        Find swing lows in price data.
        
        Args:
            data (pandas.DataFrame): Price data
            window (int): Window size for swing low detection
        
        Returns:
            pandas.Series: Boolean series indicating swing lows
        """
        # Create a rolling window view of the low prices
        lows = data['low'].rolling(window=window*2+1, center=True).apply(
            lambda x: x[window] == min(x), raw=True
        )
        
        return lows
    
    @staticmethod
    def double_top(data, window=10, threshold=0.03):
        """
        Detect double top pattern.
        
        Args:
            data (pandas.DataFrame): Price data
            window (int): Window size for pattern detection
            threshold (float): Threshold for price similarity
        
        Returns:
            pandas.Series: Boolean series indicating double tops
        """
        # Find swing highs
        swing_highs = ChartPatterns.find_swing_highs(data, window)
        
        # Initialize result series
        double_tops = pd.Series(False, index=data.index)
        
        # Find consecutive swing highs with similar prices
        for i in range(window, len(data)-window):
            if swing_highs.iloc[i]:
                # Look for another swing high within the next window bars
                for j in range(i+1, min(i+window*2, len(data))):
                    if swing_highs.iloc[j]:
                        # Check if prices are similar
                        price_diff = abs(data['high'].iloc[i] - data['high'].iloc[j]) / data['high'].iloc[i]
                        if price_diff < threshold:
                            # Check if there's a lower low between the two highs
                            if data['low'].iloc[i:j].min() < data['low'].iloc[i-window:i].min():
                                double_tops.iloc[j] = True
                                break
        
        return double_tops
    
    @staticmethod
    def double_bottom(data, window=10, threshold=0.03):
        """
        Detect double bottom pattern.
        
        Args:
            data (pandas.DataFrame): Price data
            window (int): Window size for pattern detection
            threshold (float): Threshold for price similarity
        
        Returns:
            pandas.Series: Boolean series indicating double bottoms
        """
        # Find swing lows
        swing_lows = ChartPatterns.find_swing_lows(data, window)
        
        # Initialize result series
        double_bottoms = pd.Series(False, index=data.index)
        
        # Find consecutive swing lows with similar prices
        for i in range(window, len(data)-window):
            if swing_lows.iloc[i]:
                # Look for another swing low within the next window bars
                for j in range(i+1, min(i+window*2, len(data))):
                    if swing_lows.iloc[j]:
                        # Check if prices are similar
                        price_diff = abs(data['low'].iloc[i] - data['low'].iloc[j]) / data['low'].iloc[i]
                        if price_diff < threshold:
                            # Check if there's a higher high between the two lows
                            if data['high'].iloc[i:j].max() > data['high'].iloc[i-window:i].max():
                                double_bottoms.iloc[j] = True
                                break
        
        return double_bottoms
    
    @staticmethod
    def head_and_shoulders(data, window=10, threshold=0.03):
        """
        Detect head and shoulders pattern.
        
        Args:
            data (pandas.DataFrame): Price data
            window (int): Window size for pattern detection
            threshold (float): Threshold for price similarity
        
        Returns:
            pandas.Series: Boolean series indicating head and shoulders patterns
        """
        # Find swing highs
        swing_highs = ChartPatterns.find_swing_highs(data, window)
        
        # Initialize result series
        head_and_shoulders = pd.Series(False, index=data.index)
        
        # Find three consecutive swing highs with the middle one higher
        for i in range(window, len(data)-window*2):
            if swing_highs.iloc[i]:
                # Look for a higher swing high
                for j in range(i+1, min(i+window, len(data))):
                    if swing_highs.iloc[j] and data['high'].iloc[j] > data['high'].iloc[i]:
                        # Look for a third swing high similar to the first
                        for k in range(j+1, min(j+window, len(data))):
                            if swing_highs.iloc[k]:
                                # Check if first and third peaks are similar
                                price_diff = abs(data['high'].iloc[i] - data['high'].iloc[k]) / data['high'].iloc[i]
                                if price_diff < threshold:
                                    # Check if the middle peak is higher
                                    if data['high'].iloc[j] > data['high'].iloc[i] and data['high'].iloc[j] > data['high'].iloc[k]:
                                        head_and_shoulders.iloc[k] = True
                                        break
        
        return head_and_shoulders
    
    @staticmethod
    def inverse_head_and_shoulders(data, window=10, threshold=0.03):
        """
        Detect inverse head and shoulders pattern.
        
        Args:
            data (pandas.DataFrame): Price data
            window (int): Window size for pattern detection
            threshold (float): Threshold for price similarity
        
        Returns:
            pandas.Series: Boolean series indicating inverse head and shoulders patterns
        """
        # Find swing lows
        swing_lows = ChartPatterns.find_swing_lows(data, window)
        
        # Initialize result series
        inverse_head_and_shoulders = pd.Series(False, index=data.index)
        
        # Find three consecutive swing lows with the middle one lower
        for i in range(window, len(data)-window*2):
            if swing_lows.iloc[i]:
                # Look for a lower swing low
                for j in range(i+1, min(i+window, len(data))):
                    if swing_lows.iloc[j] and data['low'].iloc[j] < data['low'].iloc[i]:
                        # Look for a third swing low similar to the first
                        for k in range(j+1, min(j+window, len(data))):
                            if swing_lows.iloc[k]:
                                # Check if first and third troughs are similar
                                price_diff = abs(data['low'].iloc[i] - data['low'].iloc[k]) / data['low'].iloc[i]
                                if price_diff < threshold:
                                    # Check if the middle trough is lower
                                    if data['low'].iloc[j] < data['low'].iloc[i] and data['low'].iloc[j] < data['low'].iloc[k]:
                                        inverse_head_and_shoulders.iloc[k] = True
                                        break
        
        return inverse_head_and_shoulders
    
    @staticmethod
    def detect_all_patterns(data):
        """
        Detect all chart patterns.
        
        Args:
            data (pandas.DataFrame): Price data with OHLC columns
        
        Returns:
            pandas.DataFrame: Data with pattern indicators
        """
        # Make a copy to avoid modifying the original
        df = data.copy()
        
        # Detect patterns
        df['swing_high'] = ChartPatterns.find_swing_highs(df)
        df['swing_low'] = ChartPatterns.find_swing_lows(df)
        df['double_top'] = ChartPatterns.double_top(df)
        df['double_bottom'] = ChartPatterns.double_bottom(df)
        df['head_and_shoulders'] = ChartPatterns.head_and_shoulders(df)
        df['inverse_head_and_shoulders'] = ChartPatterns.inverse_head_and_shoulders(df)
        
        logger.info("Detected all chart patterns")
        return df
