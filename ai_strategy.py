"""
AI Strategy Generator
Uses any OpenAI-compatible API (Ollama locally, DeepSeek in production)
to generate trading strategies with structured JSON output.
"""

import json
import logging
from datetime import datetime, timedelta
from openai import OpenAI

logger = logging.getLogger(__name__)


class AIStrategyGenerator:
    """Generate trading strategies using AI with rate limiting and caching."""

    def __init__(self, base_url="http://localhost:11434/v1", api_key="ollama", model="gemma3:4b-it-qat"):
        """Initialize with caching."""
        self.model = model
        self.cache = {}  # symbol -> (strategy, timestamp)
        self.cache_duration = timedelta(hours=1)
        self.min_price_change = 0.02  # 2% price change to invalidate cache
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        logger.info(f"Initialized AI strategy generator with model {model}")

    def is_available(self):
        """Check if the AI provider is reachable."""
        try:
            self.client.models.list()
            return True
        except Exception:
            return False

    def generate_strategy(self, data, symbol, additional_context=None, force_refresh=False):
        """
        Generate strategy with caching and rate limiting.

        Args:
            data: Market data DataFrame
            symbol: Stock symbol
            additional_context: Additional context string
            force_refresh: Force new analysis (ignore cache)

        Returns:
            Trading strategy dict
        """
        if not force_refresh and symbol in self.cache:
            cached_strategy, cached_time = self.cache[symbol]
            if datetime.now() - cached_time < self.cache_duration:
                current_price = data.iloc[-1]['close']
                cached_price = cached_strategy.get('current_price', 0)
                if cached_price > 0:
                    price_change = abs(current_price - cached_price) / cached_price
                    if price_change < self.min_price_change:
                        logger.info(f"Using cached strategy for {symbol}")
                        return cached_strategy

        try:
            latest = data.iloc[-1].to_dict()
            prompt = self._build_strategy_prompt(data, latest, symbol, additional_context)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert algorithmic trader. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )

            strategy_json = json.loads(response.choices[0].message.content)

            strategy_json['symbol'] = symbol
            strategy_json['timestamp'] = datetime.now().isoformat()
            strategy_json['current_price'] = data.iloc[-1]['close']

            self.cache[symbol] = (strategy_json, datetime.now())

            logger.info(f"Generated strategy for {symbol}: {strategy_json.get('sentiment', 'unknown')}")
            return strategy_json

        except Exception as e:
            logger.error(f"Error generating strategy: {e}")
            return {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'signals': []
            }

    def stream_analysis(self, prompt: str):
        """
        Stream a plain-text AI response for an arbitrary prompt.
        Yields text chunks as they arrive from the model.

        Used by the Market Pulse endpoint to stream market commentary.
        """
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a quantitative market analyst. Be direct, concise, and actionable."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"AI stream error: {e}")
            yield f"[AI unavailable: {e}]"

    def get_ai_commentary(self, data, symbol, audit):
        """
        Generate plain-English commentary on the full analysis audit trail.
        Always runs regardless of whether a signal was generated.

        Args:
            data: Market data DataFrame
            symbol: Stock symbol
            audit: List of tier result dicts from stream_signal()

        Returns:
            str: Plain-English explanation of the analysis
        """
        try:
            latest = data.iloc[-1].to_dict()
            prompt = self._build_commentary_prompt(data, latest, symbol, audit)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert algorithmic trader providing clear, concise market commentary. Respond in plain English, not JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )

            commentary = response.choices[0].message.content.strip()
            logger.info(f"Generated AI commentary for {symbol}")
            return commentary

        except Exception as e:
            logger.error(f"Error generating AI commentary: {e}")
            return f"AI commentary unavailable: {e}"

    def _build_strategy_prompt(self, data, latest, symbol, additional_context):
        """Build the prompt for strategy generation."""
        recent_data = data.tail(5)[['open', 'high', 'low', 'close', 'volume']].to_dict('records')

        prompt = f"""Analyze {symbol} and provide a trading strategy.

MARKET DATA (Last 5 periods):
{json.dumps(recent_data, indent=2)}

CURRENT TECHNICAL INDICATORS:
- RSI(14): {latest.get('rsi_14', 'N/A')}
- MACD: {latest.get('macd_line', 'N/A')} (Signal: {latest.get('macd_signal', 'N/A')})
- SMA(20): {latest.get('sma_20', 'N/A')}
- SMA(50): {latest.get('sma_50', 'N/A')}
- Bollinger Bands: Upper={latest.get('bb_upper', 'N/A')}, Lower={latest.get('bb_lower', 'N/A')}

CHART PATTERNS:
- Double Top: {latest.get('double_top', False)}
- Double Bottom: {latest.get('double_bottom', False)}
- Head & Shoulders: {latest.get('head_and_shoulders', False)}
"""

        if additional_context:
            prompt += f"\nADDITIONAL CONTEXT:\n{additional_context}\n"

        prompt += """
Return your analysis as JSON with this EXACT structure:
{
  "sentiment": "bullish|bearish|neutral",
  "confidence": 0-100,
  "reasoning": "brief explanation of your analysis",
  "signals": [
    {
      "symbol": "SYMBOL",
      "side": "buy|sell|hold",
      "entry_price": 0.00,
      "stop_price": 0.00,
      "target_price": 0.00,
      "risk_reward_ratio": 0.0
    }
  ]
}

Only include a signal if you have high confidence (>70). Otherwise, return empty signals array.
Focus on risk management and favorable risk-reward ratios (minimum 2:1).
"""
        return prompt

    def _build_commentary_prompt(self, data, latest, symbol, audit):
        """Build the prompt for plain-English commentary on the full audit trail."""
        tier_summary = []
        for tier in audit:
            result = tier.get('result', 'UNKNOWN')
            name = tier.get('name', '')
            details = tier.get('details', '')
            if isinstance(details, list):
                details_str = ' | '.join(details[-3:])  # last 3 details to keep prompt concise
            else:
                details_str = str(details)
            tier_summary.append(f"Tier {tier.get('tier', '?')} ({name}): {result} — {details_str}")

        def fmt(val, decimals=2):
            return f"{val:.{decimals}f}" if isinstance(val, (int, float)) else 'N/A'

        prompt = f"""You are reviewing a multi-tier trading signal analysis for {symbol}.

CURRENT MARKET DATA:
- Price: {fmt(latest.get('close'))}
- RSI(14): {fmt(latest.get('rsi_14'))}
- MACD Histogram: {fmt(latest.get('macd_histogram'), 4)}
- SMA(20): {fmt(latest.get('sma_20'))}
- SMA(50): {fmt(latest.get('sma_50'))}
- SMA(200): {fmt(latest.get('sma_200'))}
- ATR(14): {fmt(latest.get('atr_14'))}

ANALYSIS RESULTS:
{chr(10).join(tier_summary)}

In 3-5 sentences, explain:
1. What the market is doing right now for {symbol}
2. Why the analysis produced the result it did (signal or no signal)
3. What specific conditions would need to change for a trade signal to be generated

Be direct and specific. Use actual numbers from the data. Do not use bullet points — write in flowing prose."""

        return prompt
