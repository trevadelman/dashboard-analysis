"""
Personal Trading Bot
Automated trading with Alpaca API, technical analysis, and AI strategy generation
"""

from alpaca_trade_api.rest import REST
import yfinance as yf
import pandas as pd
import json
from datetime import datetime
import logging
import time

from analysis.indicators import TechnicalIndicators
from analysis.patterns import ChartPatterns
from ai_strategy import AIStrategyGenerator
from strategies.momentum import SignalHierarchy
from config import Config

logger = logging.getLogger(__name__)


class TradingBot:
    """Personal trading bot with risk management and AI integration."""

    def __init__(self, config):
        """Initialize the trading bot."""
        self.config = config

        # Initialize Alpaca API — may be None if no credentials are configured yet.
        # The dashboard profile system will swap this in via bot.api = REST(...)
        if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
            self.api = REST(
                key_id=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
                base_url=config.ALPACA_BASE_URL
            )
        else:
            self.api = None
            logger.warning("No Alpaca credentials — bot started without API connection")

        # Initialize AI strategy generator (OpenAI-compatible: Ollama locally, DeepSeek in prod)
        self.ai = AIStrategyGenerator(
            base_url=config.OPENAI_BASE_URL,
            api_key=config.OPENAI_API_KEY,
            model=config.OLLAMA_MODEL
        )

        # Risk management parameters
        self.max_positions = config.MAX_POSITIONS
        self.risk_percentage = config.RISK_PERCENTAGE

        # Data cache to avoid rate limits
        self._data_cache = {}
        self._cache_duration = 300  # 5 minutes

        logger.info(f"Trading bot initialized (paper={config.PAPER_TRADING})")

    # ===== MARKET DATA =====

    def _require_api(self) -> bool:
        """Return True if the API client is ready, False otherwise."""
        if self.api is None:
            logger.warning("Alpaca API not connected — add a profile in the dashboard")
            return False
        return True

    def get_market_data(self, symbol, period='1y', interval='1d'):
        """
        Get historical market data with caching.
        Uses Alpaca for all intervals. Works from any residential/local IP.

        Args:
            symbol (str): Stock symbol
            period (str): Lookback period (1mo, 3mo, 6mo, 1y, 2y)
            interval (str): Data interval (1d, 1h, 15m, 5m, 1m)

        Returns:
            pandas.DataFrame: Historical OHLCV data with indicators and patterns
        """
        if not self._require_api():
            return pd.DataFrame()

        cache_key = f"{symbol}_{period}_{interval}"
        if cache_key in self._data_cache:
            cached_data, cached_time = self._data_cache[cache_key]
            if time.time() - cached_time < self._cache_duration:
                logger.debug(f"Using cached data for {symbol}")
                return cached_data

        try:
            from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit
            from datetime import timedelta

            tf_map = {
                '1d':  TimeFrame.Day,
                '1h':  TimeFrame.Hour,
                '15m': TimeFrame(15, TimeFrameUnit.Minute),
                '5m':  TimeFrame(5, TimeFrameUnit.Minute),
                '1m':  TimeFrame.Minute,
            }
            period_days = {
                '1mo': 30, '2w': 14, '3mo': 90, '6mo': 180, '1y': 365, '2y': 730, '5d': 5,
            }
            tf = tf_map.get(interval, TimeFrame.Day)
            days = period_days.get(period, 365)

            end = datetime.now()
            start = end - timedelta(days=days)

            bars = self.api.get_bars(
                symbol, tf,
                start=start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                end=end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                limit=10000,
                feed='iex',
            ).df

            if bars.empty:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            # Standardize column names to lowercase
            bars.columns = [c.lower() for c in bars.columns]

            # Calculate technical indicators
            # Fetch SPY for relative strength if this isn't SPY itself
            spy_data = pd.DataFrame()
            if symbol.upper() != 'SPY':
                try:
                    spy_bars = self.api.get_bars(
                        'SPY', tf,
                        start=start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                        end=end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                        limit=10000,
                        feed='iex',
                    ).df
                    if not spy_bars.empty:
                        spy_bars.columns = [c.lower() for c in spy_bars.columns]
                        spy_data = spy_bars
                except Exception:
                    pass  # RS vs SPY will be NaN — non-fatal

            bars = TechnicalIndicators.calculate_all(bars, spy_data=spy_data)

            # Detect chart patterns
            bars = ChartPatterns.detect_all_patterns(bars)

            self._data_cache[cache_key] = (bars, time.time())

            logger.info(f"Retrieved {len(bars)} bars for {symbol} via Alpaca")
            return bars

        except Exception as e:
            logger.error(f"Error getting market data for {symbol}: {e}")

            if cache_key in self._data_cache:
                logger.info(f"Using stale cached data for {symbol} due to error")
                cached_data, _ = self._data_cache[cache_key]
                return cached_data

            return pd.DataFrame()

    # ===== ACCOUNT & POSITIONS =====

    def get_account(self):
        """Get account information."""
        try:
            account = self.api.get_account()
            return {
                'cash': float(account.cash),
                'portfolio_value': float(account.portfolio_value),
                'equity': float(account.equity),
                'buying_power': float(account.buying_power)
            }
        except Exception as e:
            logger.error(f"Error getting account: {e}")
            return {}

    def get_positions(self):
        """Get current positions."""
        try:
            positions = self.api.list_positions()
            return [{
                'symbol': p.symbol,
                'qty': float(p.qty),
                'avg_entry_price': float(p.avg_entry_price),
                'current_price': float(p.current_price),
                'market_value': float(p.market_value),
                'unrealized_pl': float(p.unrealized_pl),
                'unrealized_plpc': float(p.unrealized_plpc)
            } for p in positions]
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    def get_orders(self, status='all'):
        """Get orders."""
        try:
            orders = self.api.list_orders(status=status)
            return [{
                'id': o.id,
                'symbol': o.symbol,
                'side': o.side,
                'type': o.type,
                'qty': float(o.qty),
                'status': o.status,
                'created_at': o.created_at.isoformat()
            } for o in orders]
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []

    # ===== RISK MANAGEMENT =====

    def calculate_position_size(self, entry_price, stop_price):
        """
        Calculate position size based on risk parameters.

        Args:
            entry_price (float): Entry price
            stop_price (float): Stop loss price

        Returns:
            int: Position size in shares
        """
        account = self.get_account()
        equity = account.get('equity', 0)

        # Calculate risk amount (2% of equity by default)
        risk_amount = equity * (self.risk_percentage / 100)

        # Calculate risk per share
        risk_per_share = abs(entry_price - stop_price)

        if risk_per_share == 0:
            # Default to 5% of equity if no stop loss
            position_size = (equity * 0.05) / entry_price
        else:
            position_size = risk_amount / risk_per_share

        return int(position_size)

    def can_trade(self, symbol, side):
        """
        Check if we can execute a trade.

        Args:
            symbol (str): Stock symbol
            side (str): 'buy' or 'sell'

        Returns:
            tuple: (bool, str) - (can_trade, reason)
        """
        positions = self.get_positions()
        position_symbols = [p['symbol'] for p in positions]

        if side.lower() == 'buy':
            # Check if already holding
            if symbol in position_symbols:
                return False, f"Already holding {symbol}"

            # Check max positions
            if len(positions) >= self.max_positions:
                return False, f"Max positions reached ({self.max_positions})"

        elif side.lower() == 'sell':
            # Check if holding the symbol
            if symbol not in position_symbols:
                return False, f"Not holding {symbol}"

        # Check for pending orders
        orders = self.get_orders(status='open')
        for order in orders:
            if order['symbol'] == symbol:
                return False, f"Pending order exists for {symbol}"

        return True, "OK"

    # ===== TRADING =====

    def execute_trade(self, symbol, side, quantity=None, entry_price=None, stop_price=None):
        """
        Execute a trade with risk management.

        Args:
            symbol (str): Stock symbol
            side (str): 'buy' or 'sell'
            quantity (int, optional): Number of shares
            entry_price (float, optional): Entry price for position sizing
            stop_price (float, optional): Stop loss price

        Returns:
            dict: Trade result
        """
        # Check if we can trade
        can_trade, reason = self.can_trade(symbol, side)
        if not can_trade:
            logger.info(f"Trade skipped: {reason}")
            return {'status': 'skipped', 'reason': reason}

        # Calculate position size if not provided
        if quantity is None:
            if entry_price and stop_price:
                quantity = self.calculate_position_size(entry_price, stop_price)
            else:
                # Default to 10 shares if no sizing info
                quantity = 10

        # Execute the trade
        try:
            order = self.api.submit_order(
                symbol=symbol,
                qty=quantity,
                side=side,
                type='market',
                time_in_force='day'
            )

            # Log the trade
            trade_info = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'order_id': order.id,
                'status': order.status
            }

            self._log_trade(trade_info)
            logger.info(f"Executed {side} order for {quantity} shares of {symbol}")

            return {'status': 'executed', 'trade': trade_info}

        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            return {'status': 'error', 'message': str(e)}

    def _log_trade(self, trade_info):
        """Save trade to JSON file."""
        try:
            try:
                with open('data/trades.json', 'r') as f:
                    trades = json.load(f)
            except FileNotFoundError:
                trades = []

            trades.append(trade_info)

            with open('data/trades.json', 'w') as f:
                json.dump(trades, f, indent=2)

        except Exception as e:
            logger.error(f"Error logging trade: {e}")

    def get_trade_history(self, limit=100):
        """Get trade history from JSON file."""
        try:
            with open('data/trades.json', 'r') as f:
                trades = json.load(f)
            return trades[-limit:]
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Error reading trade history: {e}")
            return []

    # ===== STRATEGY =====

    def analyze_symbol(self, symbol, use_ai_confirmation=True, params=None):
        """
        Analyze a symbol using deterministic strategy with optional AI confirmation.

        Args:
            symbol (str): Stock symbol
            use_ai_confirmation (bool): Whether to use AI for signal confirmation
            params (dict, optional): Custom strategy parameters

        Returns:
            dict: Analysis with trading signals
        """
        data = self.get_market_data(symbol)

        if data.empty:
            return {
                'symbol': symbol,
                'error': 'No market data available',
                'signals': []
            }

        strategy_params = params if params else self.config.STRATEGY_PARAMS
        ai_gen = self.ai if use_ai_confirmation else None
        strategy = SignalHierarchy(ai_generator=ai_gen, params=strategy_params)

        signal = strategy.generate_signal(data, symbol)

        result = signal if signal else {'symbol': symbol, 'signals': [], 'audit': []}
        result['timestamp'] = datetime.now().isoformat()
        return result

    def analyze_symbol_stream(self, symbol, use_ai_confirmation=True, params=None):
        """
        Analyze a symbol and yield SSE-formatted events as each tier completes.

        Args:
            symbol (str): Stock symbol
            use_ai_confirmation (bool): Whether to use AI for signal confirmation
            params (dict, optional): Custom strategy parameters

        Yields:
            str: SSE-formatted event strings
        """
        import json as _json

        data = self.get_market_data(symbol)

        if data.empty:
            payload = _json.dumps({'type': 'error', 'message': 'No market data available'})
            yield f"data: {payload}\n\n"
            return

        strategy_params = params if params else self.config.STRATEGY_PARAMS
        ai_gen = self.ai if use_ai_confirmation else None
        strategy = SignalHierarchy(ai_generator=ai_gen, params=strategy_params)

        for event in strategy.stream_signal(data, symbol):
            if event['type'] == 'done':
                event['timestamp'] = datetime.now().isoformat()
            payload = _json.dumps(event, default=str)
            yield f"data: {payload}\n\n"

    def analyze_symbol_multi_timeframe_stream(self, symbol, use_ai_confirmation=True):
        """
        Run analysis across three timeframes (long/swing/short) sequentially,
        yielding SSE-formatted events tagged with their timeframe.

        Timeframe mapping:
            long  → 1D bars, 1-year lookback
            swing → 1H bars, 60-day lookback
            short → 15m bars, 14-day lookback

        After all three complete, yields a 'summary' event with the overall verdict.

        Yields:
            str: SSE-formatted event strings
        """
        import json as _json

        timeframes = [
            ('long',  '1d',  '1y'),
            ('swing', '1h',  '3mo'),
            ('short', '15m', '1mo'),
        ]

        ai_gen = self.ai if use_ai_confirmation else None
        results = {}

        for tf_name, interval, period in timeframes:
            data = self.get_market_data(symbol, period=period, interval=interval)

            if data.empty:
                payload = _json.dumps({
                    'type': 'tf_error',
                    'timeframe': tf_name,
                    'message': f'No data for {interval} interval',
                })
                yield f"data: {payload}\n\n"
                results[tf_name] = None
                continue

            strategy = SignalHierarchy(
                ai_generator=ai_gen,
                timeframe=tf_name,
            )

            tf_result = None
            for event in strategy.stream_signal(data, symbol):
                if event['type'] == 'done':
                    event['timestamp'] = datetime.now().isoformat()
                    tf_result = event
                payload = _json.dumps(event, default=str)
                yield f"data: {payload}\n\n"

            results[tf_name] = tf_result

        # Compute overall verdict from the three done events
        verdicts = []
        all_signals = []
        for tf_name in ('long', 'swing', 'short'):
            r = results.get(tf_name)
            if r is None:
                verdicts.append('ERROR')
            elif r.get('signals'):
                verdicts.append('SIGNAL')
                all_signals.extend(r['signals'])
            elif r.get('blocked_at', '').startswith('Tier 1'):
                verdicts.append('NO_TRADE')
            else:
                verdicts.append('NO_ENTRY')

        signal_count = verdicts.count('SIGNAL')
        if signal_count == 3:
            overall = 'ALIGNED'
        elif signal_count >= 2:
            overall = 'PARTIAL'
        elif 'NO_TRADE' in verdicts:
            overall = 'CAUTION'
        else:
            overall = 'MIXED'

        summary_payload = _json.dumps({
            'type': 'summary',
            'symbol': symbol,
            'overall': overall,
            'verdicts': {
                'long': verdicts[0] if len(verdicts) > 0 else 'ERROR',
                'swing': verdicts[1] if len(verdicts) > 1 else 'ERROR',
                'short': verdicts[2] if len(verdicts) > 2 else 'ERROR',
            },
            'signals': all_signals,
            'timestamp': datetime.now().isoformat(),
        }, default=str)
        yield f"data: {summary_payload}\n\n"

    # ===== BOT LOOP =====

    def run(self, symbols, interval=3600, auto_trade=False):
        """
        Run the trading bot in a loop.

        Args:
            symbols (list): List of symbols to trade
            interval (int): Check interval in seconds (default: 1 hour)
            auto_trade (bool): Automatically execute trades (default: False)
        """
        logger.info(f"Starting trading bot for {symbols}")
        logger.info(f"Check interval: {interval}s, Auto-trade: {auto_trade}")

        while True:
            try:
                for symbol in symbols:
                    logger.info(f"Analyzing {symbol}...")

                    # Analyze the symbol
                    strategy = self.analyze_symbol(symbol)

                    # Check for trading signals
                    if strategy.get('signals'):
                        for signal in strategy['signals']:
                            logger.info(f"Signal for {symbol}: {signal}")

                            if auto_trade and signal.get('side'):
                                # Execute the trade
                                result = self.execute_trade(
                                    symbol=signal['symbol'],
                                    side=signal['side'],
                                    entry_price=signal.get('entry_price'),
                                    stop_price=signal.get('stop_price')
                                )
                                logger.info(f"Trade result: {result}")
                    else:
                        logger.info(f"No signals for {symbol}")

                # Wait before next check
                logger.info(f"Waiting {interval}s before next check...")
                time.sleep(interval)

            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                time.sleep(60)  # Wait 1 minute before retrying


def main():
    """Main entry point with safety checks."""
    # Initialize logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/bot.log'),
            logging.StreamHandler()
        ]
    )

    # Initialize config
    config = Config()

    # SAFETY CHECK: Confirm live trading
    if not config.PAPER_TRADING:
        print("\n" + "="*60)
        print("⚠️  WARNING: LIVE TRADING MODE ENABLED ⚠️")
        print("="*60)
        print("You are about to trade with REAL MONEY.")
        print("This will execute actual trades on your brokerage account.")
        print("\nType 'YES' (all caps) to continue, or anything else to exit.")
        print("="*60 + "\n")

        confirm = input("Confirm live trading: ")
        if confirm != "YES":
            print("Exiting. Switch to paper trading mode in .env file.")
            exit(0)

        print("\n✅ Live trading confirmed. Starting bot...\n")

    # Create bot
    bot = TradingBot(config)

    # Check account
    account = bot.get_account()
    logger.info(f"Account equity: ${account.get('equity', 0):.2f}")

    # Run bot with watchlist from config
    symbols = config.WATCHLIST
    logger.info(f"Watchlist: {symbols}")
    bot.run(symbols, interval=3600, auto_trade=False)  # Check every hour


if __name__ == '__main__':
    main()
