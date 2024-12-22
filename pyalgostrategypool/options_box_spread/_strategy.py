
# Box Spread Python Strategy Code
"""
    checkout:
        - Strategy docs: (Placeholder link)
        - Generalized options strategy guide: https://algobulls.github.io/pyalgotrading/strategies/strategy_guides/common_strategy_guide/
        - Box Spread Strategy details: see Section 2: Box Spread Strategy (Options)
"""

from pyalgotrading.strategy import StrategyOptionsBase, OptionsStrikeDirection, OptionsInstrumentDirection
import datetime

class OptionsBoxSpread(StrategyOptionsBase):
    name = 'Options Box Spread'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        """
        A box spread has 4 legs (Bull Call + Bear Put).
        For simplicity, let's define them as:
          Leg1 = Buy Call @ strike A (near ATM)
          Leg2 = Sell Call @ strike B (slightly OTM)
          Leg3 = Buy Put @ strike B
          Leg4 = Sell Put @ strike A

        We will incorporate discounting logic & checks for arbitrage opportunity:
          - Enter Long Box if Market Cost < Theoretical Cost
          - Enter Short Box if Market Cost > Theoretical Cost
        """

        # ---------------------------
        #  Leg Parameter Defaults
        # ---------------------------

        # Leg 1: Buy call (strike A, near-ATM => 0 strikes offset or ITM)
        self._leg_one_transaction_type = self.strategy_parameters.get('LEG_ONE_TRANSACTION_TYPE', 1)  # 1=BUY
        self._leg_one_tradingsymbol_suffix = self.strategy_parameters.get('LEG_ONE_TRADING_SYMBOL_SUFFIX', 1)  # 1=CE
        self._leg_one_strike_direction = self.strategy_parameters.get('LEG_ONE_STRIKE_DIRECTION', 0)  # 0=ITM, 1=ATM, 2=OTM
        self._leg_one_number_of_strikes = self.strategy_parameters.get('LEG_ONE_NUMBER_OF_STRIKES', 0)

        # Leg 2: Sell call (strike B, OTM => 2 strikes offset by default)
        self._leg_two_transaction_type = self.strategy_parameters.get('LEG_TWO_TRANSACTION_TYPE', 2)  # 2=SELL
        self._leg_two_tradingsymbol_suffix = self.strategy_parameters.get('LEG_TWO_TRADING_SYMBOL_SUFFIX', 1)  # CE
        self._leg_two_strike_direction = self.strategy_parameters.get('LEG_TWO_STRIKE_DIRECTION', 2)  # OTM
        self._leg_two_number_of_strikes = self.strategy_parameters.get('LEG_TWO_NUMBER_OF_STRIKES', 2)

        # Leg 3: Buy put (strike B, OTM => same offset as Leg 2)
        self._leg_three_transaction_type = self.strategy_parameters.get('LEG_THREE_TRANSACTION_TYPE', 1)  # BUY
        self._leg_three_tradingsymbol_suffix = self.strategy_parameters.get('LEG_THREE_TRADING_SYMBOL_SUFFIX', 2)  # PE
        self._leg_three_strike_direction = self.strategy_parameters.get('LEG_THREE_STRIKE_DIRECTION', 2)  # OTM
        self._leg_three_number_of_strikes = self.strategy_parameters.get('LEG_THREE_NUMBER_OF_STRIKES', 2)

        # Leg 4: Sell put (strike A, near-ATM => 0 strikes offset or ITM)
        self._leg_four_transaction_type = self.strategy_parameters.get('LEG_FOUR_TRANSACTION_TYPE', 2)  # SELL
        self._leg_four_tradingsymbol_suffix = self.strategy_parameters.get('LEG_FOUR_TRADING_SYMBOL_SUFFIX', 2)  # PE
        self._leg_four_strike_direction = self.strategy_parameters.get('LEG_FOUR_STRIKE_DIRECTION', 0)  # ITM
        self._leg_four_number_of_strikes = self.strategy_parameters.get('LEG_FOUR_NUMBER_OF_STRIKES', 0)

        # ---------------------------
        #  Arbitrage Parameters
        # ---------------------------
        self._risk_free_rate = self.strategy_parameters.get('RISK_FREE_RATE', 0.01)   # e.g., 1%
        self._exit_threshold = self.strategy_parameters.get('EXIT_THRESHOLD', 0.01)   # absolute difference in cost
        self._transaction_cost_percent = self.strategy_parameters.get('TRANSACTION_COST_PERCENT', 0.0)  # optional

        # Maps 
        self.transaction_type_map = {1: "BUY", 2: "SELL"}
        self.tradingsymbol_suffix_map = {1: "CE", 2: "PE"}
        self.strike_direction_map = {
            0: OptionsStrikeDirection.ITM,
            1: OptionsStrikeDirection.ATM,
            2: OptionsStrikeDirection.OTM
        }

        self.number_of_allowed_expiry_dates = 1
        self.instruments_done_for_the_day = None

        # Track open box positions (so we can monitor cost for early exit)
        # Format: self.open_positions[base_instrument] = {
        #    'legs': [ (instrument_leg1, action), (instrument_leg2, action), ... ],
        #    'strike_a': float,
        #    'strike_b': float,
        #    'is_long_box': bool  # or is_short_box
        # }
        self.open_positions = {}

    def initialize(self):
        super().initialize()
        self.instruments_done_for_the_day = []

    # ---------------
    # Helper Methods
    # ---------------
    def time_to_expiration_in_years(self, expiry_dt: datetime.date, current_time: datetime.datetime):
        """
        Compute time to expiration (T) in *years*.
        Example logic: days_to_expiry / 365.0
        """
        if isinstance(expiry_dt, datetime.datetime):
            days_to_expiry = (expiry_dt.date() - current_time.date()).days
        elif isinstance(expiry_dt, datetime.date):
            days_to_expiry = (expiry_dt - current_time.date()).days
        else:
            days_to_expiry = 0

        return max(days_to_expiry, 0) / 365.0

    def discount_factor(self, T):
        """
        1 / (1 + r)^T
        """
        return 1.0 / ((1.0 + self._risk_free_rate) ** T)

    def compute_theoretical_cost(self, strike_a, strike_b, T):
        """
        (B - A) / (1 + r)^T
        """
        df = self.discount_factor(T)
        return (strike_b - strike_a) * df

    def compute_box_market_cost(self, leg_instruments):
        """
        Sum of premiums for the 4 legs:
         Bull Call = Buy call @ A, Sell call @ B  => cost = callA_price - callB_price
         Bear Put  = Buy put  @ B, Sell put  @ A  => cost = putB_price  - putA_price
         net cost  = callA_price - callB_price + putB_price - putA_price
        We'll just sum up the cost if action=BUY, subtract if action=SELL.
        """
        total_cost = 0.0
        for (instr, action) in leg_instruments:
            ltp = self.broker.get_ltp(instr)
            if action == 'BUY':
                total_cost += ltp
            else:  # SELL
                total_cost -= ltp
        return total_cost

    # ---------------
    # Overridden Methods
    # ---------------
    def get_child_instrument_details(self, base_instrument, tradingsymbol_suffix, strike_direction, no_of_strikes):
        expiry_date = self.get_allowed_expiry_dates()[0]
        child_instrument = self.get_options_instrument_with_strike_direction(
            base_instrument,
            expiry_date,
            tradingsymbol_suffix,
            strike_direction,
            no_of_strikes
        )
        return child_instrument

    def options_instruments_set_up_local(self, base_instrument, tradingsymbol_suffix, current_close, direction=OptionsInstrumentDirection.EXACT):
        expiry_dates = self.get_allowed_expiry_dates()
        for expiry_date in expiry_dates:
            self.options_instruments_set_up(
                base_instrument,
                direction,
                expiry_date,
                tradingsymbol_suffix,
                current_close
            )

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        Logic:
         1) For each instrument in instruments_bucket, if not processed:
            - set up the child instruments
            - build the 4 legs
            - compute strike A, strike B from the child instruments (assuming Leg1/Leg4 => A, Leg2/Leg3 => B)
            - compute the market cost of these 4 legs
            - compute the theoretical cost
            - if market_cost < theoretical_cost => 'BUY_BOX'
            - if market_cost > theoretical_cost => 'SELL_BOX'
         2) Return the actual legs as (selected_instruments, meta)
        """
        selected_instruments, meta = [], []
        current_time = candle.timestamp  # or candle.datetime

        for instrument in instruments_bucket:
            if instrument in self.instruments_done_for_the_day:
                continue

            self.instruments_done_for_the_day.append(instrument)
            ltp = self.broker.get_ltp(instrument)

            # Step 1: ensure child instruments exist
            self.options_instruments_set_up_local(instrument, "CE", ltp)
            self.options_instruments_set_up_local(instrument, "PE", ltp)

            # Step 2: define the 4 legs
            # Previous legs data is stored to compute cost.
            leg_specs = [
                (self._leg_one_transaction_type, self._leg_one_tradingsymbol_suffix,
                 self._leg_one_strike_direction, self._leg_one_number_of_strikes),
                (self._leg_two_transaction_type, self._leg_two_tradingsymbol_suffix,
                 self._leg_two_strike_direction, self._leg_two_number_of_strikes),
                (self._leg_three_transaction_type, self._leg_three_tradingsymbol_suffix,
                 self._leg_three_strike_direction, self._leg_three_number_of_strikes),
                (self._leg_four_transaction_type, self._leg_four_tradingsymbol_suffix,
                 self._leg_four_strike_direction, self._leg_four_number_of_strikes),
            ]
            leg_instruments = []

            # For convenience in identifying strike A vs. strike B after we pick them
            strike_prices = []

            for (tx_type, ts_suffix, str_dir, num_strikes) in leg_specs:
                action = self.transaction_type_map[tx_type]
                suffix = self.tradingsymbol_suffix_map[ts_suffix]
                direction = self.strike_direction_map[str_dir]

                child_instrument = self.get_child_instrument_details(
                    instrument,
                    suffix,
                    direction,
                    num_strikes
                )

                # Keep track so we can compute market cost
                leg_instruments.append((child_instrument, action))
                strike_prices.append(child_instrument.strike)  # store the actual strike used

            # Example assumption:
            #   Leg1 & Leg4 => strike A  (since both are ITM or near)
            #   Leg2 & Leg3 => strike B  (since both are OTM)
            # The code here is simplistic: we can guess the min strike ~ A, max strike ~ B
            strike_a = min(strike_prices)
            strike_b = max(strike_prices)

            # Step 3: compute T => from one child instrument's expiry
            expiry_dt = leg_instruments[0][0].expiry
            T = self.time_to_expiration_in_years(expiry_dt, current_time)

            # Step 4: compute theoretical cost
            theoretical_cost = self.compute_theoretical_cost(strike_a, strike_b, T)

            # Step 5: compute market cost
            market_cost = self.compute_box_market_cost(leg_instruments)

            # Step 6: optional transaction cost offset
            # e.g. we apply a % on (B - A) or something. For simplicity, we reduce the theoretical cost 
            # for a long box or bump for a short box:
            notional = (strike_b - strike_a)
            tc_amount = self._transaction_cost_percent * notional

            # Step 7: decide if we have an arbitrage opportunity
            # (Simplified logic: if market_cost < theoretical_cost - tc => BUY_BOX
            #                   if market_cost > theoretical_cost + tc => SELL_BOX)
            # If the difference is not big enough, we do nothing (no arbitrage).
            action_label = None
            if market_cost < (theoretical_cost - tc_amount):
                action_label = 'BUY_BOX'
            elif market_cost > (theoretical_cost + tc_amount):
                action_label = 'SELL_BOX'

            if action_label:
                # We'll add each child's entry to selected_instruments & meta
                for (child_instrument, child_action) in leg_instruments:
                    selected_instruments.append(child_instrument)
                    # For actual order placement, we store the child's action the same as the 4-leg definition
                    meta.append({'base_instrument': instrument, 'box_action': action_label, 'child_action': child_action})

                # Keep track of position details for exit
                self.open_positions[instrument] = {
                    'legs': leg_instruments,
                    'strike_a': strike_a,
                    'strike_b': strike_b,
                    'is_long_box': (action_label == 'BUY_BOX')
                }

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        Place an order for each child instrument. 
        The sideband_info has: {'base_instrument': ..., 'box_action': 'BUY_BOX'/'SELL_BOX', 'child_action': 'BUY'/'SELL'}
        """
        child_action = sideband_info['child_action']
        _ = self.broker.OrderRegular(
            instrument,
            child_action,
            quantity=self.number_of_lots * instrument.lot_size
        )
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        We check if the box cost converges to theoretical cost for open positions.
        If so, we exit.
        """
        selected_instruments, meta = [], []
        current_time = candle.timestamp

        for base_instrument, pos_data in list(self.open_positions.items()):
            # We only exit instruments if they are in instruments_bucket 
            # (some environments restrict actions to that subset).
            if base_instrument not in instruments_bucket:
                continue

            legs = pos_data['legs']
            strike_a = pos_data['strike_a']
            strike_b = pos_data['strike_b']

            # Recompute time to expiration & theoretical cost
            expiry_dt = legs[0][0].expiry
            T = self.time_to_expiration_in_years(expiry_dt, current_time)
            theoretical_cost = self.compute_theoretical_cost(strike_a, strike_b, T)

            # Current market cost
            market_cost = self.compute_box_market_cost(legs)

            # If cost is near theoretical (within self._exit_threshold), close position
            # e.g. abs(market_cost - theoretical_cost) < _exit_threshold
            if abs(market_cost - theoretical_cost) < self._exit_threshold:
                # We want to exit each child instrument
                for (child_instrument, _) in legs:
                    selected_instruments.append(child_instrument)
                    meta.append({'base_instrument': base_instrument, 'action': 'EXIT_BOX'})

        return selected_instruments, meta

    def strategy_exit_position(self, candle, instrument, sideband_info):
        """
        Called for each child_instrument we want to exit. 
        We'll close the position by reversing the child's direction.
        """
        if sideband_info['action'] == 'EXIT_BOX':
            # We can do broker.close_position(instrument) 
            # or issue the opposite transaction type 
            # (if we have a net position in that instrument).
            self.broker.close_position(instrument)

            return True
        return False

