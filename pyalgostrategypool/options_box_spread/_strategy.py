"""
    checkout:
        - Strategy docs: (Placeholder link)
        - Generalized options strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
        - Box Spread Strategy details: see Section 2: Box Spread Strategy (Options)
"""

from pyalgotrading.strategy import StrategyOptionsBaseV2, OptionsInstrumentDirection
import datetime

class OptionsBoxSpread(StrategyOptionsBaseV2):
    name = 'Options Box Spread'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Strategy Parameters from the input 'parameters' dictionary
        self.number_of_lots = self.strategy_parameters.get('number_of_lots', 1)
        self.lot_size = self.strategy_parameters.get('lot_size', 1)

        # Strike offset values from the input 'parameters' dictionary
        self.call_strike_offset = self.strategy_parameters.get('CALL_STRIKE_OFFSET', 50)  # OTM by 50
        self.put_strike_offset = self.strategy_parameters.get('PUT_STRIKE_OFFSET', 50)    # OTM by 50

        # Leg Parameters from the input 'parameters' dictionary
        self.leg_one_transaction_type = self.strategy_parameters.get('LEG_ONE_TRANSACTION_TYPE', 1)
        self.leg_one_tradingsymbol_suffix = self.strategy_parameters.get('LEG_ONE_TRADING_SYMBOL_SUFFIX', 1)
        self.leg_one_strike_direction = self.strategy_parameters.get('LEG_ONE_STRIKE_DIRECTION', 0)
        self.leg_one_number_of_strikes = self.strategy_parameters.get('LEG_ONE_NUMBER_OF_STRIKES', 0)

        self.leg_two_transaction_type = self.strategy_parameters.get('LEG_TWO_TRANSACTION_TYPE', 2)
        self.leg_two_tradingsymbol_suffix = self.strategy_parameters.get('LEG_TWO_TRADING_SYMBOL_SUFFIX', 1)
        self.leg_two_strike_direction = self.strategy_parameters.get('LEG_TWO_STRIKE_DIRECTION', 2)
        self.leg_two_number_of_strikes = self.strategy_parameters.get('LEG_TWO_NUMBER_OF_STRIKES', 2)

        self.leg_three_transaction_type = self.strategy_parameters.get('LEG_THREE_TRANSACTION_TYPE', 1)
        self.leg_three_tradingsymbol_suffix = self.strategy_parameters.get('LEG_THREE_TRADING_SYMBOL_SUFFIX', 2)
        self.leg_three_strike_direction = self.strategy_parameters.get('LEG_THREE_STRIKE_DIRECTION', 2)
        self.leg_three_number_of_strikes = self.strategy_parameters.get('LEG_THREE_NUMBER_OF_STRIKES', 2)

        self.leg_four_transaction_type = self.strategy_parameters.get('LEG_FOUR_TRANSACTION_TYPE', 2)
        self.leg_four_tradingsymbol_suffix = self.strategy_parameters.get('LEG_FOUR_TRADING_SYMBOL_SUFFIX', 2)
        self.leg_four_strike_direction = self.strategy_parameters.get('LEG_FOUR_STRIKE_DIRECTION', 0)
        self.leg_four_number_of_strikes = self.strategy_parameters.get('LEG_FOUR_NUMBER_OF_STRIKES', 0)

        # Arbitrage parameters from the input 'parameters' dictionary
        self.risk_free_rate = self.strategy_parameters.get('RISK_FREE_RATE', 0.01)
        self.exit_threshold = self.strategy_parameters.get('EXIT_THRESHOLD', 0.03)
        self.transaction_cost_percent = self.strategy_parameters.get('TRANSACTION_COST_PERCENT', 0.001)

        # Tracking state
        self.instruments_done_for_the_day = []
        self.open_positions = {}

    def initialize(self):
        super().initialize()
        self.instruments_done_for_the_day = []

    def get_allowed_expiry_dates(self, instrument):
        """
        Fetch expiry dates from historical data of an instrument.
        """
        historical_data = self.get_historical_data(instrument)  # Ensure get_historical_data is used here
        if 'expiry' in historical_data.columns:
            return sorted(historical_data['expiry'].unique())
        return []

    def register_option_instrument(self, option_instrument):
        """
        Registers the option instrument with the broker or framework.
        """
        pass  # Placeholder for broker-specific instrument registration

    def options_instruments_set_up(self, base_instrument, instrument_direction, expiry_date, tradingsymbol_suffix, ltp=None):
        """
        Sets up a single option instrument based on the provided parameters.
        """
        option_symbol = f"{base_instrument['symbol']}{tradingsymbol_suffix}{ltp}"
        option_instrument = {
            'symbol': option_symbol,
            'expiry': expiry_date,
            'strike': ltp,
            'type': tradingsymbol_suffix,
            'direction': instrument_direction
        }
        self.register_option_instrument(option_instrument)
        return option_instrument

    def compute_theoretical_cost(self, strike_a, strike_b, expiry_date):
        """
        Compute the theoretical cost of the box spread using a simplified model.
        Theoretical cost is calculated as the difference between the strikes, discounted by the time to expiration.
        
        Formula: (Strike B - Strike A) / (1 + risk-free rate)^T
        where T is the time to expiration in years.
        """
        current_time = datetime.datetime.now().date()  # Get the current date
        T = self.time_to_expiration_in_years(expiry_date, current_time)  # Time to expiration in years

        # Simplified theoretical cost computation (ignores volatility, etc.)
        theoretical_cost = (strike_b - strike_a) * self.discount_factor(T)

        return theoretical_cost

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        Attempt to enter a Box Spread (Buy or Sell) for each valid instrument in instruments_bucket.
        The decision is based on the comparison of market cost and theoretical cost.
        """
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            if instrument in self.instruments_done_for_the_day:
                continue

            self.instruments_done_for_the_day.append(instrument)

            try:
                ltp = self.broker.get_ltp(instrument)
                if ltp is None:
                    continue
            except IndexError:
                continue

            # Retrieve expiry dates using get_historical_data
            expiry_dates = self.get_allowed_expiry_dates(instrument)
            if not expiry_dates:
                continue

            expiry_date = expiry_dates[0]  # Use the nearest expiry

            strike_a = ltp  # ATM strike
            strike_b = ltp + self.call_strike_offset  # OTM strike by predefined offset

            try:
                leg1 = self.options_instruments_set_up(
                    base_instrument=instrument,
                    instrument_direction=OptionsInstrumentDirection.LONG,  # Buy Call
                    expiry_date=expiry_date,
                    tradingsymbol_suffix="CE",
                    ltp=strike_a
                )
                leg2 = self.options_instruments_set_up(
                    base_instrument=instrument,
                    instrument_direction=OptionsInstrumentDirection.SHORT,  # Sell Call
                    expiry_date=expiry_date,
                    tradingsymbol_suffix="CE",
                    ltp=strike_b
                )
                leg3 = self.options_instruments_set_up(
                    base_instrument=instrument,
                    instrument_direction=OptionsInstrumentDirection.LONG,  # Buy Put
                    expiry_date=expiry_date,
                    tradingsymbol_suffix="PE",
                    ltp=strike_b
                )
                leg4 = self.options_instruments_set_up(
                    base_instrument=instrument,
                    instrument_direction=OptionsInstrumentDirection.SHORT,  # Sell Put
                    expiry_date=expiry_date,
                    tradingsymbol_suffix="PE",
                    ltp=strike_a
                )
            except Exception:
                continue

            # Compute market cost and theoretical cost
            try:
                market_cost = (
                    self.broker.get_ltp(leg1) - self.broker.get_ltp(leg2) +
                    self.broker.get_ltp(leg3) - self.broker.get_ltp(leg4)
                )
                theoretical_cost = self.compute_theoretical_cost(strike_a, strike_b, expiry_date)

                if market_cost < theoretical_cost:
                    box_action = 'BUY_BOX'
                elif market_cost > theoretical_cost:
                    box_action = 'SELL_BOX'
                else:
                    continue  # Skip if costs are equal or undefined
            except Exception:
                continue

            # Append the 4 legs with the determined action
            legs = [
                (leg1, 'BUY' if box_action == 'BUY_BOX' else 'SELL'),
                (leg2, 'SELL' if box_action == 'BUY_BOX' else 'BUY'),
                (leg3, 'BUY' if box_action == 'BUY_BOX' else 'SELL'),
                (leg4, 'SELL' if box_action == 'BUY_BOX' else 'BUY')
            ]

            for leg, action in legs:
                selected_instruments.append(leg)
                meta.append({
                    'base_instrument': instrument,
                    'box_action': box_action,
                    'child_action': action
                })

            self.open_positions[instrument] = {
                'legs': legs,
                'strike_a': strike_a,
                'strike_b': strike_b,
                'market_cost': market_cost,
                'theoretical_cost': theoretical_cost,
                'is_long_box': box_action == 'BUY_BOX'
            }

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        Place an order for each child instrument.
        The sideband_info contains metadata like 'child_action'.
        """
        child_action = sideband_info.get('child_action')
        if not child_action:
            return None

        quantity = self.number_of_lots * self.lot_size

        # Place the order using the broker's API
        try:
            order_response = self.broker.OrderRegular(
                instrument['symbol'],
                child_action,
                quantity=quantity
            )
            return order_response
        except Exception:
            return None

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        Always select instruments to exit any open Buy Box Spreads.
        """
        selected_instruments, meta = [], []

        for base_instrument, pos_data in list(self.open_positions.items()):
            legs = pos_data['legs']
            action_label = 'EXIT_BOX'  # Force exit condition

            for (leg_instrument, _) in legs:
                selected_instruments.append(leg_instrument)
                meta.append({
                    'base_instrument': base_instrument,
                    'action': action_label
                })

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, sideband_info):
        """
        Select instruments for exit based on the market cost convergence with the theoretical cost
        for open positions (both Buy and Sell Box Spreads).
        Includes transaction cost adjustment for more accurate exit conditions.
        """
        if sideband_info.get('action') == 'EXIT_BOX':
            try:
                self.broker.close_position(instrument['symbol'])
                base_instrument = sideband_info.get('base_instrument')
                if base_instrument and base_instrument in self.open_positions:
                    del self.open_positions[base_instrument]
                return True
            except Exception:
                return False
        return False