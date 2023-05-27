from strategy.core.strategy_options_base_v2 import StrategyOptionsBaseV2, OptionsInstrumentDirection


class StrategyOptionsBullCallLadder(StrategyOptionsBaseV2):
    name = 'Options Bull Call Ladder Template v2'

    def __init__(self, *args, **kwargs):
        """
        Accept and sanitize all your parameters here.
        Setup the variables you will need here.
        If you are running the strategy for multiple days, then this method will be called only once at the start of the strategy.
        """

        super().__init__(*args, **kwargs)

        # Parameters (currently set with default values, can be overridden from the Parameters Configuration Pane)
        self._leg_one_transaction_type = self.strategy_parameters.get('LEG_ONE_TRANSACTION_TYPE', 1)  # BUY: 1 | SELL: 2
        self._leg_one_tradingsymbol_suffix = self.strategy_parameters.get('LEG_ONE_TRADING_SYMBOL_SUFFIX', 1)  # CE: 1 | PE: 2
        self._leg_one_strike_direction = self.strategy_parameters.get('LEG_ONE_STRIKE_DIRECTION', 1)  # ITM: 0| ATM: 1| OTM: 2
        self._leg_one_number_of_strikes = self.strategy_parameters.get('LEG_ONE_NUMBER_OF_STRIKES', 0)

        self._leg_two_transaction_type = self.strategy_parameters.get('LEG_TWO_TRANSACTION_TYPE', 1)  # BUY: 1 | SELL: 2
        self._leg_two_tradingsymbol_suffix = self.strategy_parameters.get('LEG_TWO_TRADING_SYMBOL_SUFFIX', 1)  # CE: 1 | PE: 2
        self._leg_two_strike_direction = self.strategy_parameters.get('LEG_TWO_STRIKE_DIRECTION', 2)  # ITM: 0| ATM: 1| OTM: 2
        self._leg_two_number_of_strikes = self.strategy_parameters.get('LEG_TWO_NUMBER_OF_STRIKES', 2)

        self._leg_three_transaction_type = self.strategy_parameters.get('LEG_THREE_TRANSACTION_TYPE', 2)  # BUY: 1 | SELL: 2
        self._leg_three_tradingsymbol_suffix = self.strategy_parameters.get('LEG_THREE_TRADING_SYMBOL_SUFFIX', 1)  # CE: 1 | PE: 2
        self._leg_three_strike_direction = self.strategy_parameters.get('LEG_THREE_STRIKE_DIRECTION', 2)  # ITM: 0| ATM: 1| OTM: 2
        self._leg_three_number_of_strikes = self.strategy_parameters.get('LEG_THREE_NUMBER_OF_STRIKES', 4)

        # Maps
        self.transaction_type_map = {1: "BUY", 2: "SELL"}
        self.tradingsymbol_suffix_map = {1: "CE", 2: "PE"}
        self.strike_direction_map = {0: "ITM", 1: "ATM", 2: "OTM"}

        # Variables
        self.number_of_allowed_expiry_dates = 1
        self.instruments_done_for_the_day = None

    def initialize(self):
        super().initialize()
        self.instruments_done_for_the_day = []

    def get_child_instrument_details(self, base_instrument, tradingsymbol_suffix, strike_direction, no_of_strikes):
        """
        Fetches a child instrument based on the criteria given.
        You can keep this method as-is, if the number of allowed expiry dates is 1. If more than 1, you can modify accordingly.
        """

        expiry_date = self.get_allowed_expiry_dates()[0]
        child_instrument = self.get_options_instrument_with_strike_direction(base_instrument, expiry_date, tradingsymbol_suffix, strike_direction, no_of_strikes)
        return child_instrument

    def options_instruments_set_up_local(self, base_instrument, tradingsymbol_suffix, current_close, direction=OptionsInstrumentDirection.EXACT):
        """
        Fetches and stores all Call and Put Options instruments (a.k.a CE/PE child instruments) and keeps them ready.
        You can keep this method as-is.
        """

        expiry_dates = self.get_allowed_expiry_dates()
        for expiry_date in expiry_dates:
            self.options_instruments_set_up(base_instrument, direction, expiry_date, tradingsymbol_suffix, current_close)

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):
        """
        This method is called once every candle time.
        If you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on).
        In a candle, the exit method is called first, then the entry method is called.
        So once a candle starts, strategy_select_instruments_for_exit gets called first
        and then this method strategy_select_instruments_for_entry gets called.
        """

        selected_instruments = []
        meta = []

        for instrument in instruments_bucket:

            # Process entry signal(s) only if instrument is not processed already
            if instrument not in self.instruments_done_for_the_day:
                self.instruments_done_for_the_day.append(instrument)
                # get ltp of base instrument
                ltp = self.broker.get_ltp(instrument)

                # setup child instruments
                self.options_instruments_set_up_local(instrument, "CE", ltp)
                self.options_instruments_set_up_local(instrument, "PE", ltp)

                leg_wise_list = [('LEG_ONE', self._leg_one_tradingsymbol_suffix, self._leg_one_strike_direction, self._leg_one_number_of_strikes, self._leg_one_transaction_type),
                                 ('LEG_TWO', self._leg_two_tradingsymbol_suffix, self._leg_two_strike_direction, self._leg_two_number_of_strikes, self._leg_two_transaction_type),
                                 ('LEG_THREE', self._leg_three_tradingsymbol_suffix, self._leg_three_strike_direction, self._leg_three_number_of_strikes, self._leg_three_transaction_type)]

                for leg_number, tradingingsymbol_suffix, strike_direction, number_of_strikes, transaction_type in leg_wise_list:
                    self.logger.info(f'Processing {leg_number}...')
                    child_instrument = self.get_child_instrument_details(instrument, self.tradingsymbol_suffix_map[tradingingsymbol_suffix], self.strike_direction_map[strike_direction], number_of_strikes)
                    selected_instruments.append(child_instrument)
                    meta.append({'base_instrument': instrument, 'action': self.transaction_type_map[transaction_type]})

        return selected_instruments, meta

    def strategy_enter_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Place an order here and return it to the core.
        """
        # Place buy order
        _ = self.broker.OrderRegular(instrument, sideband_info['action'], quantity=self.number_of_lots * instrument.lot_size)
        return _

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):
        """
        This method is called once every candle time.
        If you set the candle at 5 minutes, then this method will be called every 5 minutes (09:15, 09:20, 09:25 and so on).
        In a candle, the exit method is called first, then the entry method is called.
        So once a candle starts, this method strategy_select_instruments_for_exit gets called first
        and then strategy_select_instruments_for_entry gets called.
        """

        return [], []

    def strategy_exit_position(self, candle, instrument, sideband_info):
        """
        This method is called once for each instrument from the bucket in this candle.
        Exit an order here and return the instrument status to the core.
        """

        return False
