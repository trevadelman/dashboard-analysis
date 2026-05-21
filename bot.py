"""
Personal Trading Bot
Automated trading with Alpaca API, technical analysis, and AI strategy generation
"""

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
from alpaca.data.enums import DataFeed
import pandas as pd
import json
from datetime import datetime, timedelta
import logging
import time

from analysis.indicators import TechnicalIndicators
from ai_strategy import AIStrategyGenerator
from strategies.momentum import SignalHierarchy, TIMEFRAME_CONFIG
from config import Config

logger = logging.getLogger(__name__)


class TradingBot:
    """Personal trading bot with risk management and AI integration."""

    def __init__(self, config):
        """Initialize the trading bot."""
        self.config = config

        # Initialize Alpaca clients — may be None if no credentials are configured yet.
        # The dashboard profile system will swap these in via bot.trading_client / bot.data_client
        if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
            paper = config.PAPER_TRADING
            self.trading_client = TradingClient(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
                paper=paper,
            )
            self.data_client = StockHistoricalDataClient(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
            )
        else:
            self.trading_client = None
            self.data_client = None
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
        """Return True if the API clients are ready, False otherwise."""
        if self.trading_client is None or self.data_client is None:
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
            tf   = tf_map.get(interval, TimeFrame.Day)
            days = period_days.get(period, 365)

            end   = datetime.now()
            start = end - timedelta(days=days)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                limit=10000,
                feed=DataFeed.IEX,
            )
            bars = self.data_client.get_stock_bars(request).df

            if bars.empty:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            # alpaca-py returns a MultiIndex (symbol, timestamp) — drop the symbol level
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.droplevel(0)

            # Standardize column names to lowercase
            bars.columns = [c.lower() for c in bars.columns]

            # Fetch SPY for relative strength if this isn't SPY itself
            spy_data = pd.DataFrame()
            if symbol.upper() != 'SPY':
                try:
                    spy_request = StockBarsRequest(
                        symbol_or_symbols='SPY',
                        timeframe=tf,
                        start=start,
                        end=end,
                        limit=10000,
                        feed=DataFeed.IEX,
                    )
                    spy_bars = self.data_client.get_stock_bars(spy_request).df
                    if not spy_bars.empty:
                        if isinstance(spy_bars.index, pd.MultiIndex):
                            spy_bars = spy_bars.droplevel(0)
                        spy_bars.columns = [c.lower() for c in spy_bars.columns]
                        spy_data = spy_bars
                except Exception:
                    pass  # RS vs SPY will be NaN — non-fatal

            bars = TechnicalIndicators.calculate_all(bars, spy_data=spy_data)

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
            account = self.trading_client.get_account()
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
            positions = self.trading_client.get_all_positions()
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
            status_map = {
                'all':    QueryOrderStatus.ALL,
                'open':   QueryOrderStatus.OPEN,
                'closed': QueryOrderStatus.CLOSED,
            }
            req = GetOrdersRequest(status=status_map.get(status, QueryOrderStatus.ALL))
            orders = self.trading_client.get_orders(req)
            result = []
            for o in orders:
                legs = []
                if o.legs:
                    for leg in o.legs:
                        legs.append({
                            'id':         str(leg.id),
                            'type':       leg.type.value,
                            'side':       leg.side.value,
                            'qty':        float(leg.qty) if leg.qty else None,
                            'status':     leg.status.value,
                            'limit_price': float(leg.limit_price) if leg.limit_price else None,
                            'stop_price':  float(leg.stop_price)  if leg.stop_price  else None,
                        })
                result.append({
                    'id':          str(o.id),
                    'symbol':      o.symbol,
                    'side':        o.side.value,
                    'type':        o.type.value,
                    'order_class': o.order_class.value if o.order_class else None,
                    'qty':         float(o.qty),
                    'filled_qty':  float(o.filled_qty) if o.filled_qty else 0,
                    'filled_avg_price': float(o.filled_avg_price) if o.filled_avg_price else None,
                    'limit_price': float(o.limit_price) if o.limit_price else None,
                    'stop_price':  float(o.stop_price)  if o.stop_price  else None,
                    'status':      o.status.value,
                    'created_at':  o.created_at.isoformat(),
                    'updated_at':  o.updated_at.isoformat() if o.updated_at else None,
                    'legs':        legs,
                })
            return result
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
            if symbol in position_symbols:
                return False, f"Already holding {symbol}"
            if len(positions) >= self.max_positions:
                return False, f"Max positions reached ({self.max_positions})"

        elif side.lower() == 'sell':
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
        can_trade, reason = self.can_trade(symbol, side)
        if not can_trade:
            logger.info(f"Trade skipped: {reason}")
            return {'status': 'skipped', 'reason': reason}

        if quantity is None:
            if entry_price and stop_price:
                quantity = self.calculate_position_size(entry_price, stop_price)
            else:
                quantity = 10

        try:
            order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
            req = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order = self.trading_client.submit_order(req)

            trade_info = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'order_id': str(order.id),
                'status': order.status.value,
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

    def adjust_orders(self, symbol: str, new_stop: float = None, new_target: float = None) -> dict:
        """
        Modify the stop-loss and/or take-profit legs of an open bracket order.

        Alpaca bracket legs are child orders of the parent bracket. We cancel
        the existing leg and replace it with a new order at the updated price.

        Args:
            symbol:     Stock symbol
            new_stop:   New stop price (None = leave unchanged)
            new_target: New take-profit limit price (None = leave unchanged)

        Returns:
            dict with status and details of what was changed
        """
        if not self._require_api():
            return {'status': 'error', 'message': 'No Alpaca API connection'}

        from alpaca.trading.requests import ReplaceOrderRequest

        all_orders = self.get_orders(status='all')
        active_statuses = {'new', 'held', 'accepted', 'pending_new', 'partially_filled'}
        symbol_orders = [
            o for o in all_orders
            if o['symbol'].upper() == symbol.upper()
            and o['status'] in active_statuses
        ]

        changed = []
        errors  = []

        for order in symbol_orders:
            order_id = order['id']

            # Stop-loss leg
            if new_stop is not None and order['type'] in ('stop', 'stop_limit'):
                try:
                    req = ReplaceOrderRequest(stop_price=round(new_stop, 2))
                    self.trading_client.replace_order_by_id(order_id, req)
                    changed.append(f"Stop updated to ${new_stop:.2f} (order {order_id[:8]})")
                    logger.info(f"Stop order {order_id[:8]} for {symbol} updated to ${new_stop:.2f}")
                except Exception as e:
                    errors.append(f"Stop update failed: {e}")
                    logger.error(f"Failed to update stop for {symbol}: {e}")

            # Take-profit leg (limit sell)
            if new_target is not None and order['type'] == 'limit' and order['side'] == 'sell':
                try:
                    req = ReplaceOrderRequest(limit_price=round(new_target, 2))
                    self.trading_client.replace_order_by_id(order_id, req)
                    changed.append(f"Target updated to ${new_target:.2f} (order {order_id[:8]})")
                    logger.info(f"Target order {order_id[:8]} for {symbol} updated to ${new_target:.2f}")
                except Exception as e:
                    errors.append(f"Target update failed: {e}")
                    logger.error(f"Failed to update target for {symbol}: {e}")

            # Also check nested legs
            for leg in (order.get('legs') or []):
                leg_id = leg['id']
                if new_stop is not None and leg['type'] in ('stop', 'stop_limit'):
                    try:
                        req = ReplaceOrderRequest(stop_price=round(new_stop, 2))
                        self.trading_client.replace_order_by_id(leg_id, req)
                        changed.append(f"Stop leg updated to ${new_stop:.2f} (leg {leg_id[:8]})")
                    except Exception as e:
                        errors.append(f"Stop leg update failed: {e}")

                if new_target is not None and leg['type'] == 'limit':
                    try:
                        req = ReplaceOrderRequest(limit_price=round(new_target, 2))
                        self.trading_client.replace_order_by_id(leg_id, req)
                        changed.append(f"Target leg updated to ${new_target:.2f} (leg {leg_id[:8]})")
                    except Exception as e:
                        errors.append(f"Target leg update failed: {e}")

        if not changed and not errors:
            return {'status': 'no_orders', 'message': f'No active stop/target orders found for {symbol}'}

        return {
            'status':  'error' if errors and not changed else 'ok',
            'changed': changed,
            'errors':  errors,
        }

    def close_position(self, symbol: str) -> dict:
        """
        Close an open position at market price.

        Cancels all open orders for the symbol first, then submits a
        market order to flatten the position.

        Args:
            symbol: Stock symbol

        Returns:
            dict with status and order details
        """
        if not self._require_api():
            return {'status': 'error', 'message': 'No Alpaca API connection'}

        try:
            # Cancel all open orders for this symbol first to avoid conflicts
            all_orders = self.get_orders(status='all')
            active_statuses = {'new', 'held', 'accepted', 'pending_new'}
            for order in all_orders:
                if (order['symbol'].upper() == symbol.upper()
                        and order['status'] in active_statuses):
                    try:
                        self.trading_client.cancel_order_by_id(order['id'])
                        logger.info(f"Cancelled order {order['id'][:8]} for {symbol} before close")
                    except Exception as e:
                        logger.warning(f"Could not cancel order {order['id'][:8]}: {e}")

            # Close the position via Alpaca's close_position endpoint
            result = self.trading_client.close_position(symbol)
            logger.info(f"Closed position for {symbol}: order {result.id}")
            return {
                'status':   'ok',
                'order_id': str(result.id),
                'symbol':   symbol,
                'message':  f'Position closed at market — order {str(result.id)[:8]}',
            }

        except Exception as e:
            logger.error(f"Error closing position for {symbol}: {e}")
            return {'status': 'error', 'message': str(e)}

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
        Run analysis across three timeframes (long/swing/short), yielding SSE-formatted
        events tagged with their timeframe.

        Bar data for all three timeframes (plus SPY for each) is fetched in parallel
        using a thread pool before any strategy logic runs, cutting wall-clock fetch
        time from 3× single-timeframe to roughly 1× (the slowest fetch).

        After all three complete, yields a 'summary' event with the overall verdict.

        Yields:
            str: SSE-formatted event strings
        """
        import json as _json
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Period overrides per timeframe — keep data volumes reasonable for live analysis.
        period_override = {'long': '1y', 'swing': '3mo', 'short': '1mo'}
        timeframe_specs = [
            (tf_name, TIMEFRAME_CONFIG[tf_name]['interval'], period_override[tf_name])
            for tf_name in ('long', 'swing', 'short')
        ]

        # ── Parallel data fetch ───────────────────────────────────────────────
        # Fetch symbol bars and SPY bars for each timeframe concurrently.
        # Keys: (tf_name, 'symbol') and (tf_name, 'spy')
        fetch_tasks = {}
        for tf_name, interval, period in timeframe_specs:
            fetch_tasks[(tf_name, 'symbol')] = (symbol, period, interval)
            fetch_tasks[(tf_name, 'spy')]    = ('SPY',  period, interval)

        fetched = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_to_key = {
                pool.submit(self.get_market_data, sym, period, interval): key
                for key, (sym, period, interval) in fetch_tasks.items()
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    fetched[key] = future.result()
                except Exception as e:
                    logger.warning(f"Parallel fetch failed for {key}: {e}")
                    fetched[key] = pd.DataFrame()

        # ── Sequential strategy evaluation ────────────────────────────────────
        ai_gen = self.ai if use_ai_confirmation else None
        results = {}

        for tf_name, interval, period in timeframe_specs:
            data     = fetched.get((tf_name, 'symbol'), pd.DataFrame())
            spy_data = fetched.get((tf_name, 'spy'),    pd.DataFrame())

            if data.empty:
                payload = _json.dumps({
                    'type': 'tf_error',
                    'timeframe': tf_name,
                    'message': f'No data for {interval} interval',
                })
                yield f"data: {payload}\n\n"
                results[tf_name] = None
                continue

            # Inject SPY data into the bars so RS vs SPY is computed correctly.
            # TechnicalIndicators.calculate_all accepts spy_data as a kwarg.
            from analysis.indicators import TechnicalIndicators
            try:
                data = TechnicalIndicators.calculate_all(data, spy_data=spy_data)
            except Exception as e:
                logger.warning(f"Indicator recalc failed for {symbol} [{tf_name}]: {e}")

            strategy = SignalHierarchy(ai_generator=ai_gen, timeframe=tf_name)

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
        all_audits  = {}
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
            if r:
                all_audits[tf_name] = r.get('audit', [])

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

        # Single consolidated AI commentary across all three timeframes
        if use_ai_confirmation and ai_gen:
            try:
                # Build a combined audit summary for the AI
                combined_audit = []
                for tf_name in ('long', 'swing', 'short'):
                    for tier in all_audits.get(tf_name, []):
                        combined_audit.append({**tier, 'timeframe': tf_name})
                # Use the long-timeframe data as the primary data source for the prompt
                primary_data = fetched.get(('long', 'symbol'), pd.DataFrame())
                if primary_data.empty:
                    primary_data = fetched.get(('swing', 'symbol'), pd.DataFrame())
                if not primary_data.empty:
                    commentary = ai_gen.get_ai_commentary(primary_data, symbol, combined_audit)
                    commentary_payload = _json.dumps({
                        'type': 'ai_commentary',
                        'timeframe': 'all',
                        'text': commentary,
                    }, default=str)
                    yield f"data: {commentary_payload}\n\n"
            except Exception as e:
                logger.warning(f"AI commentary failed for {symbol}: {e}")

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

                    strategy = self.analyze_symbol(symbol)

                    if strategy.get('signals'):
                        for signal in strategy['signals']:
                            logger.info(f"Signal for {symbol}: {signal}")

                            if auto_trade and signal.get('side'):
                                result = self.execute_trade(
                                    symbol=signal['symbol'],
                                    side=signal['side'],
                                    entry_price=signal.get('entry_price'),
                                    stop_price=signal.get('stop_price')
                                )
                                logger.info(f"Trade result: {result}")
                    else:
                        logger.info(f"No signals for {symbol}")

                logger.info(f"Waiting {interval}s before next check...")
                time.sleep(interval)

            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                time.sleep(60)


def main():
    """Main entry point with safety checks."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/bot.log'),
            logging.StreamHandler()
        ]
    )

    config = Config()

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

    bot = TradingBot(config)

    account = bot.get_account()
    logger.info(f"Account equity: ${account.get('equity', 0):.2f}")

    symbols = config.WATCHLIST
    logger.info(f"Watchlist: {symbols}")
    bot.run(symbols, interval=3600, auto_trade=False)


if __name__ == '__main__':
    main()
