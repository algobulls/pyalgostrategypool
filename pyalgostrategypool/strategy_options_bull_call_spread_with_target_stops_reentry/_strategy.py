"""
Strategy Description:
    The Options Bull Call Spread strategy establishes a bullish position by buying an at-the-money (ATM) Call option and selling a higher-strike Call option with the same expiry.
    This variant adds a target profit, a hard stop-loss, and a trailing stop-loss to manage risk and lock in gains.
    It also allows controlled re-entries when the exit conditions are met and market conditions justify a new entry.
"""


class StrategyOptionsBullCallSpreadWithTargetStopsAndReentry(StrategyOptionsBase):
    name = "Strategy Options Bull Call Spread With Target Stops And Re-entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Required parameters for this strategy
        self.no_of_otm_strikes_leg_sell = self.strategy_parameters.get("NUMBER_OF_OTM_STRIKES_SELL_LEG", 3)
        self.re_entry_limit = self.strategy_parameters.get('RE_ENTRY_LIMIT', 1)
        self.stoploss_percentage = self.strategy_parameters.get('STOPLOSS_PERCENTAGE', 15)
        self.target_percentage = self.strategy_parameters.get('TARGET_PERCENTAGE', 15)
        self.tsl_percentage = self.strategy_parameters.get("TRAILING_STOPLOSS_PERCENTAGE", 1)

        self.total_entries_allowed = self.re_entry_limit + 1  # +1 to include the initial entry in total entry allowance

        # Internal variables and placeholders
        self.child_instrument_main_orders = None  # Tracks Call orders
        self.highest = None
        self.re_entry_count = None
        self.spread_current = None
        self.spread_entry = None
        self.stoploss_premium = None
        self.target_premium = None
        self.trailing_stop = None

        # State flags
        self.reentry_complete = None
        self.execution_complete = None

        self._validate_parameters()

    def _validate_parameters(self):
        """ Validates required strategy parameters. """
        check_argument(
            self.strategy_parameters, "extern_function", lambda x: len(x) >= 5,
            err_message=(
                "Need 5 parameters for this strategy: \n"
                "(1) NUMBER_OF_OTM_STRIKES_SELL_LEG \n"
                "(2) TARGET_PERCENTAGE \n"
                "(3) STOPLOSS_PERCENTAGE \n"
                "(4) TRAILING_STOPLOSS_PERCENTAGE \n"
                "(5) RE_ENTRY_LIMIT"
            )
        )

        # Validate numeric strategy parameters
        for param in (self.re_entry_limit, self.no_of_otm_strikes_leg_sell):
            check_argument(param, "extern_function", is_positive_int, "Value should be a positive integer")

        # Validate percentage strategy parameters
        for param in (self.target_percentage, self.stoploss_percentage, self.tsl_percentage,):
            check_argument(param, "extern_function", is_nonnegative_int_or_float, "Value should be >0")

    def initialize(self):
        super().initialize()

        # Reset all order states, spread metrics, and re-entry trackers for a fresh cycle
        self.child_instrument_main_orders = {}
        self.reentry_complete = {}
        self.execution_complete = False
        self.highest = {}
        self.re_entry_count = {}
        self.spread_current = None
        self.spread_entry = {}
        self.stoploss_premium = {}
        self.target_premium = {}
        self.trailing_stop = {}

    def _check_exit_conditions(self, base_instrument, child_leg_orders_dict):
        """
        Evaluate all exit rules for the Bull Call Spread.

        Checks:
        • Target profit – exit if spread rises to the profit target.
        • Hard stop-loss – exit if spread falls below the stop-loss threshold.
        • Trailing stop-loss – once the spread makes new highs, trail a stop to lock in profits.
        """

        # Retrieve current orders and latest traded prices (LTP) for both legs
        ltp_leg_buy = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.BUY].instrument)
        ltp_leg_sell = self.broker.get_ltp(child_leg_orders_dict[BrokerOrderTransactionTypeConstants.SELL].instrument)

        # Initialize key levels at entry:
        if self.spread_entry.get(base_instrument) is None:
            entry_price_leg_buy = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.BUY].entry_price
            entry_price_leg_sell = self.child_instrument_main_orders.get(base_instrument)[BrokerOrderTransactionTypeConstants.SELL].entry_price
            self.spread_entry[base_instrument] = entry_price_leg_buy - entry_price_leg_sell  # spread at entry
            self.stoploss_premium[base_instrument] = self.spread_entry[base_instrument] * (1 - self.stoploss_percentage / 100)
            self.target_premium[base_instrument] = self.spread_entry[base_instrument] * (1 + self.target_percentage / 100)

        # Current spread price
        # Bull Call Spread value = Long(ATM CE) − Short(OTM CE)
        self.spread_current = ltp_leg_buy - ltp_leg_sell

        self.logger.info(f"Target and Hard Stoploss Check for {base_instrument}: Entry Spread price: {self.spread_entry[base_instrument]:.2f} | "
                         f"Current Spread price: {self.spread_current:.2f} | "
                         f"Target Threshold: {self.target_premium[base_instrument]:.2f} | "
                         f"Stop-loss Threshold : {self.stoploss_premium[base_instrument]:.2f}")

        # Target Profit Check
        if self.spread_current > self.target_premium[base_instrument]:
            self.logger.debug(f"Target profit hit for {base_instrument}: Current Net Premium ({self.spread_current:.2f}) exceeded Target Threshold ({self.target_premium[base_instrument]:.2f}). Exiting positions...")
            return True

        # Hard Stop-loss Check
        if self.spread_current < self.stoploss_premium[base_instrument]:
            self.logger.debug(f"Stop-loss hit for {base_instrument}: Current Net Premium ({self.spread_current:.2f}) dropped below Stop-loss Threshold ({self.stoploss_premium[base_instrument]:.2f}). Exiting positions...")
            return True

        # Activate trailing stop only after the spread rises to at least entry / (1 − TSL%).
        # This ensures the trailing stop is positioned at or above the entry spread, securing a no-loss condition.
        # We precompute entry / (1 − TSL) for efficiency instead of recalculating each tick.
        one_minus_tsl_fraction = (1 - self.tsl_percentage / 100)
        if self.highest.get(base_instrument) is None and self.spread_current >= self.spread_entry[base_instrument] / one_minus_tsl_fraction:
            self.logger.info(f"Trailing stoploss activated for {base_instrument}")
            self.highest[base_instrument] = self.spread_current  # first highest spread
            self.trailing_stop[base_instrument] = self.highest[base_instrument] * one_minus_tsl_fraction  # initial trailing stop

        # Trailing Stop-loss (TSL) check
        if self.highest.get(base_instrument) is not None:
            self.logger.info(f"Trailing Stop-loss Check for {base_instrument}: Entry Spread price: {self.spread_entry[base_instrument]:.2f} | "
                             f"Current Spread price: {self.spread_current:.2f} | "
                             f"New Highest: {self.highest[base_instrument]:.2f} | "
                             f"Trailing Stop: {self.trailing_stop[base_instrument]:.2f} | "
                             f"(Trail %: {self.tsl_percentage}) ")

            # Update trailing stop whenever current spread exceeds previous high
            if self.spread_current > self.highest[base_instrument]:
                self.highest[base_instrument] = self.spread_current
                self.trailing_stop[base_instrument] = self.highest[base_instrument] * one_minus_tsl_fraction

            # Trigger TSL exit if current spread falls below trailing stop
            if self.spread_current < self.trailing_stop[base_instrument]:
                self.logger.info(f"Trailing Stop-loss triggered for {base_instrument}: Current Net Premium ({self.spread_current:.2f}) dropped below Trailing Stop ({self.trailing_stop[base_instrument]:.2f}). Exiting positions...")
                return True

        return False

    def _exit_all_positions_for_base_instrument(self, base_instrument):
        for order in filter(None, self.child_instrument_main_orders.get(base_instrument, {}).values()):  # Exit all active positions for the base instrument.
            order.exit_position()

        # Reset all state variables so the next entry can be reinitialized
        for dict_state in (self.spread_entry, self.highest, self.trailing_stop, self.stoploss_premium, self.target_premium):
            dict_state.pop(base_instrument, None)

        self.child_instrument_main_orders.pop(base_instrument, None)  # Remove references to the base instrument after exiting CE orders.
        self.re_entry_count[base_instrument]['reentry_recorded'] = False  # Reset re-entry flag so new entries are allowed after full exit.

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        selected_instruments, meta = [], []

        for instrument in instruments_bucket:
            if not self.reentry_complete.setdefault(instrument, False):
                self.logger.info(
                    f"Checking entry conditions for base instrument: {instrument} | "
                    f"Determining ATM/OTM option instruments and verifying if CE orders are already placed."
                )

                # Skip the instrument if active order already exists
                if self.child_instrument_main_orders.get(instrument):
                    continue

                # Initialize re-entry tracking for this base instrument if not already done
                self.re_entry_count.setdefault(instrument, {'reentry_recorded': False})

                # Retrieve total entries so far; skip if re-entry limit reached
                re_entry_count = self.re_entry_count[instrument].get('reentry_count', 0)
                if re_entry_count >= self.total_entries_allowed:
                    self.logger.info(f"Reentry limit reached for {instrument}. Continue checking for other base instruments..")
                    self.reentry_complete[instrument] = True
                    # End strategy execution if all instruments reached reentry limit
                    if all((self.reentry_complete.get(instrument, False) for instrument in instruments_bucket)):
                        self.logger.info(f"Reentry limit reached for all base instruments. Exiting strategy...")
                        self.execution_complete = True
                    continue

                leg_wise_list = [
                    (BrokerOrderTransactionTypeConstants.BUY, OptionsStrikeDirection.ATM.value, 0),
                    (BrokerOrderTransactionTypeConstants.SELL, OptionsStrikeDirection.OTM.value, self.no_of_otm_strikes_leg_sell)
                ]

                # Retrieve LTP of the base instrument to set up child instruments
                base_instrument_ltp = self.broker.get_ltp(instrument)

                for action, strike_direction, no_of_strikes in leg_wise_list:

                    expiry_date = self.get_allowed_expiry_dates(instrument)[0]
                    self.options_instruments_set_up(instrument, OptionsInstrumentDirection.EXACT, expiry_date, OptionsTradingsymbolSuffix.CE.value, base_instrument_ltp)

                    # Retrieve ATM/OTM child instrument for the given instrument
                    child_instrument = self.get_options_instrument_with_strike_direction(instrument, expiry_date, OptionsTradingsymbolSuffix.CE.value, strike_direction, no_of_strikes)

                    # Clean up any half-built selection if a leg is missing (avoids partial exposure).
                    # Only one child instrument exists in case of partial fill, so [0] indexing is used.
                    if not child_instrument:
                        self.logger.info("One leg is missing - removing previously added legs (if any) to avoid partial exposure; otherwise skipping this candle.")
                        if self.instruments_mapper.is_base_instrument(instrument):
                            other_child_instrument = self.instruments_mapper.get_child_instruments_list(instrument)[0]
                            meta.pop(selected_instruments.index(other_child_instrument))
                            selected_instruments.remove(other_child_instrument)
                        break

                    # Map the base instrument to its corresponding child instrument in the instruments' mapper.
                    # This allows tracking of relationships between base and child instruments for further processing.
                    self.instruments_mapper.add_mappings(instrument, child_instrument)

                    selected_instruments.append(child_instrument)
                    meta.append({"action": action, "base_instrument": instrument})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, meta):

        child_instrument = instrument
        base_instrument = meta["base_instrument"]
        _order = self.broker.OrderRegular(instrument=child_instrument, order_transaction_type=meta['action'], order_code=self.order_code, order_variety=BrokerOrderVarietyConstants.MARKET, quantity=self.number_of_lots * child_instrument.lot_size)

        # Store details of successful orders
        if check_order_placed_successfully(_order):
            self.child_instrument_main_orders.setdefault(base_instrument, {})[meta['action']] = _order

            # Increment re-entry count only once per complete entry.
            # Mark re-entry as recorded to prevent multiple increments before exit.
            if not self.re_entry_count[base_instrument].get('reentry_recorded'):
                self.re_entry_count[base_instrument]['reentry_count'] = self.re_entry_count[base_instrument].get('reentry_count', 0) + 1
                self.re_entry_count[base_instrument]['reentry_recorded'] = True
        else:
            # Protection logic incase any of the legs fail to get placed - this will help avoid having naked positions
            self.logger.critical('Order placement failed for one of the legs. Exiting position for other leg, if possible and stopping strategy...')
            self._exit_all_positions_for_base_instrument(base_instrument)
            raise ABSystemExit

        return _order

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        selected_instruments_bucket, meta, _base_instruments_processed_list = [], [], []

        for instrument in instruments_bucket:
            if self.instruments_mapper.is_child_instrument(instrument):
                base_instrument = self.instruments_mapper.get_base_instrument(instrument)

                # Evaluate exit only once per base
                if base_instrument in _base_instruments_processed_list:
                    continue  # Skip if already processed

                _base_instruments_processed_list.append(base_instrument)

                # Check if CE orders are complete and if trailing stop-loss condition is met.
                child_leg_orders_dict = self.child_instrument_main_orders.get(base_instrument)
                if child_leg_orders_dict:
                    if all(check_order_complete_status(order) for order in child_leg_orders_dict.values()) and (self._check_exit_conditions(base_instrument, child_leg_orders_dict)):
                        # Collect all child instruments of the base instrument for exit
                        selected_instruments_bucket.extend(order.instrument for order in child_leg_orders_dict.values())
                        meta.extend([{"action": "EXIT", "base_instrument": base_instrument}] * len(child_leg_orders_dict))

        return selected_instruments_bucket, meta

    def strategy_exit_position(self, candle, instrument, meta):
        # Close both legs for the base instrument referenced in meta['base_instrument']
        self._exit_all_positions_for_base_instrument(meta['base_instrument'])

        return True
