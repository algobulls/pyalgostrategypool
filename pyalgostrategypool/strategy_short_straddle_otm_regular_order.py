from datetime import date as dt
from datetime import datetime

import pandas as pd

import clock
from broker.broker_connection_base import BrokerConnectionBase
from constants import *
from strategy.core.strategy_base import StrategyBase
from utils.func import check_argument, is_positive_int, is_positive_int_or_float


class StrategyShortStraddleOTMRegularOrder(StrategyBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        check_argument(self.strategy_parameters, 'extern_function', lambda x: len(x) >= 6, err_message='Need 6 parameters for this strategy: the instrument expiry date and the stoploss percentage')
        self.instrument_expiry_day = self.strategy_parameters['INSTRUMENT_EXPIRY_DAY']  # 30
        self.instrument_expiry_month = self.strategy_parameters['INSTRUMENT_EXPIRY_MONTH']  # 9
        self.instrument_expiry_year = self.strategy_parameters['INSTRUMENT_EXPIRY_YEAR']  # 2020

        self.stoploss_points = self.strategy_parameters['STOPLOSS_POINTS']  # 150
        self.profit_desired = self.strategy_parameters['PROFIT_DESIRED']  # 1250
        self.user_defined_candle_start_time_hours = self.strategy_parameters['USER_DEFINED_CANDLE_START_TIME_HOURS']  # 9
        self.user_defined_candle_start_time_minutes = self.strategy_parameters['USER_DEFINED_CANDLE_START_TIME_MINUTES']  # 20

        check_argument(self.instrument_expiry_day, 'extern_function', is_positive_int, err_message='Please enter a valid day.')
        check_argument(self.instrument_expiry_month, 'extern_function', is_positive_int, err_message='Please enter a valid month.')
        check_argument(self.instrument_expiry_year, 'extern_function', is_positive_int, err_message='Please enter a valid year.')
        check_argument(self.stoploss_points, 'extern_function', is_positive_int_or_float, err_message='stoploss points amount should be > 0.')
        check_argument(self.user_defined_candle_start_time_hours, 'extern_function', is_positive_int, err_message='user_defined_candle_start_time_hours should be > 0.')
        check_argument(self.user_defined_candle_start_time_minutes, 'extern_function', is_positive_int, err_message='user_defined_candle_start_time_minutes should be > 0.')

        try:
            self.expiry_date = dt(year=int(self.instrument_expiry_year), month=int(self.instrument_expiry_month), day=int(self.instrument_expiry_day))
        except:
            self.logger.fatal('INVALID DAY / MONTH / YEAR... EXITING')
            raise SystemExit

        str_time = str(self.user_defined_candle_start_time_hours) + ':' + str(self.user_defined_candle_start_time_minutes)
        try:
            self.candle_end_time = (datetime.strptime(str_time, '%H:%M'))
            self.candle_end_time = self.candle_end_time.time()
        except:
            self.logger.fatal('Error converting hours and minutes given in yaml... EXITING')
            raise SystemExit

        self.main_order = None
        self.main_order_ce = None
        self.main_order_entry_price_ce = None
        self.main_order_pe = None
        self.main_order_entry_price_pe = None
        self.stoploss_order = None
        self.stoploss_order_ce = None
        self.stoploss_order_pe = None
        self.instruments = None
        self.day_start = None
        self.first_order_is_done = None
        self.master_df = None
        self.broker_df = None

    @staticmethod
    def name():
        return 'Short Straddle OTM Regular Order Strategy'

    @staticmethod
    def versions_supported():
        return AlgoBullsEngineVersion.VERSION_3_2_0

    def initialize(self):
        self.main_order = {}
        self.stoploss_order = {}
        self.main_order_ce = {}
        self.main_order_entry_price_ce = {}
        self.main_order_pe = {}
        self.main_order_entry_price_pe = {}
        self.stoploss_order_ce = {}
        self.stoploss_order_pe = {}
        self.day_start = {}
        self.first_order_is_done = False
        self.instruments = {}
        self.master_df = None
        self.broker_df = None

    def select_columns(self, data_frame, column_names):
        if data_frame is None or data_frame.empty:
            self.logger.fatal('NO DATA RECEIVED FROM BROKER... EXITING')
            raise SystemExit
        # new_frame = data_frame.loc[:, column_names]
        new_frame = data_frame.reindex(columns=column_names)
        return new_frame

    def set_and_subscribe_trading_symbols(self, instrument):
        # A list of columns that are need to be displayed / worked with
        selected_columns = ['tradingsymbol', 'name', 'strike', 'expiry', 'instrument_type', 'segment', 'exchange']

        if self.broker.get_name() == AlgoBullsSupportedBrokers.ALICEBLUE.value:
            self.logger.info(f'BROKER IS - {self.broker.get_name()}')
            if self.master_df is None:
                all_master_inst = BrokerConnectionBase.get_fresh_instruments_data(self.broker)
                self.master_df = all_master_inst.copy()
            else:
                all_master_inst = self.master_df.copy()
            all_instruments = all_master_inst.copy()
        else:
            self.logger.info(f'BROKER IS - {self.broker.get_name()}')
            if self.broker_df is None:
                all_broker_instruments = self.select_columns(self.broker.all_inst, selected_columns)
                self.broker_df = all_broker_instruments.copy()
            else:
                all_broker_instruments = self.broker_df.copy()
            all_instruments = all_broker_instruments.copy()

        if all_instruments is None or all_instruments.empty:
            self.logger.fatal('NO DATA RECEIVED FROM BROKER... EXITING')
            raise SystemExit
        # convert the expiry date column of the df to string
        all_instruments['expiry'] = pd.Series(all_instruments['expiry'], dtype="string")

        # select the row (from the df) for the current instrument (from yaml) and save it as a row
        current_instrument_row = all_instruments.loc[all_instruments['tradingsymbol'] == instrument.tradingsymbol]
        if current_instrument_row is None or current_instrument_row.empty:
            self.logger.fatal('CURRENT INSTRUMENT GIVEN BY PARAMETER NOT FOUND... EXITING')
            raise SystemExit
        # make ready the name column, expiry column, instrument_type column and exchange column to be used as filters on the df
        self.logger.info(f'CURRENT INSTRUMENT GIVEN BY PARAMETER: \n{current_instrument_row}')
        name_value = current_instrument_row['name'].iloc[-1]
        expiry_date_value = str(self.expiry_date)
        instrument_type_value = ['CE', 'PE']
        exchange_value = current_instrument_row['exchange'].iloc[-1]

        # fetch a new filtered df based on the above filters
        filtered_instruments = all_instruments.loc[
            (all_instruments['name'] == name_value) & (all_instruments['expiry'] == expiry_date_value) & (all_instruments['instrument_type'].isin(instrument_type_value)) & (all_instruments['exchange'] == exchange_value)]
        if filtered_instruments is None or filtered_instruments.empty:
            self.logger.fatal('NO INSTRUMENTS FOUND FOR CURRENT EXPIRY DATE... EXITING')
            raise SystemExit
        # calculate the delta = abs(strike_price - ltp) for every row in filtered df -- this will be saved as a series
        ltp = self.broker.get_ltp(instrument)
        strike_delta = (filtered_instruments['strike'].astype(float) - float(ltp)).abs()
        # add this series as a new column in the filtered df
        # filtered_instruments['delta'] = strike_delta
        filtered_instruments.loc[:, 'delta'] = strike_delta

        # Reset the Indexes of the filtered df
        filtered_instruments.reset_index(drop=True, inplace=True)

        # get the row number (index value) which has the smallest delta value among all rows (min of delta column)
        idx = filtered_instruments['delta'].idxmin()

        # copy this delta value using the index
        delta = filtered_instruments.iloc[[idx]]['delta'].iloc[-1]

        # now create the final df which should have 2 rows only, with the instrument_type as CE/PE (1 each)
        final_instruments_master = filtered_instruments.loc[(filtered_instruments['delta'] <= delta)].copy()
        self.logger.info(f'FINAL SELECTED INSTRUMENTS\n{final_instruments_master}')
        if final_instruments_master is None or final_instruments_master.empty:
            self.logger.fatal('NO INSTRUMENTS FOUND FOR CURRENT EXPIRY DATE... EXITING')
            raise SystemExit
        if not (len(final_instruments_master.axes[0]) == 2 and len(final_instruments_master['instrument_type'].unique()) == 2):
            if not (len(final_instruments_master.axes[0]) == 4):
                self.logger.fatal('NUMBER OF INSTRUMENTS RETURED FROM THE BROKER SHOULD BE EXACTLY 2.. THEY ARE NOT!! EXITING...')
                raise SystemExit
            else:
                final_instruments_master = final_instruments_master.loc[(final_instruments_master['strike'] >= ltp)].copy()
                self.logger.info(f' *NEW* FINAL SELECTED INSTRUMENTS\n{final_instruments_master}')
        trading_symbol_column_name_master = 'tradingsymbol'
        if self.broker.get_name() == AlgoBullsSupportedBrokers.ALICEBLUE.value:
            alice_instruments = self.broker.all_inst
            # alice_instruments.to_csv('aliceblue_final.csv')
            final_instruments_broker = alice_instruments.loc[(alice_instruments['token'].isin(final_instruments_master['exchange_token']))].copy()
            self.logger.info(f'ALICE FILTERED AND FINAL INSTRUMENTS ARE - \n {final_instruments_broker}')
            # alice_filtered_instruments.to_csv('aliceblue_final.csv')
            trading_symbol_column_name_broker = 'symbol'
        else:
            # case when broker is zerodha or abvirtualbroker
            final_instruments_broker = final_instruments_master.copy()
            trading_symbol_column_name_broker = trading_symbol_column_name_master

        # create instruments using both entries from the final df
        self.instruments[instrument] = []
        first_instrument_from_df_broker = self.broker.get_instrument(segment=instrument.segment, tradingsymbol=final_instruments_broker[trading_symbol_column_name_broker].iloc[-2])
        first_instrument_from_df_master = BrokerConnectionBase.get_instrument(self.broker, segment=instrument.segment, tradingsymbol=final_instruments_master[trading_symbol_column_name_master].iloc[-2])
        first_instrument_from_df_master.tradingsymbol_broker = first_instrument_from_df_broker.tradingsymbol

        if hasattr(first_instrument_from_df_master, 'tradingsymbol'):
            self.instruments[instrument].append(first_instrument_from_df_master)
            self.logger.debug(f'first instrument from df: {first_instrument_from_df_master} ')
        else:
            self.logger.warning(f'Instrument not found ---{first_instrument_from_df_master}')
            raise NotImplementedError
        second_instrument_from_df_broker = self.broker.get_instrument(segment=instrument.segment, tradingsymbol=final_instruments_broker[trading_symbol_column_name_broker].iloc[-1])
        second_instrument_from_df_master = BrokerConnectionBase.get_instrument(self.broker, segment=instrument.segment, tradingsymbol=final_instruments_master[trading_symbol_column_name_master].iloc[-1])
        second_instrument_from_df_master.tradingsymbol_broker = second_instrument_from_df_broker.tradingsymbol

        if hasattr(second_instrument_from_df_master, 'tradingsymbol'):
            self.instruments[instrument].append(second_instrument_from_df_master)
            self.logger.debug(f'second instrument from df: {second_instrument_from_df_master} ')
        else:
            self.logger.warning(f'Instrument not found ---{second_instrument_from_df_master}')
            raise NotImplementedError

        self.broker.historical_data_feed.subscribe_bulk(self.instruments[instrument])
        self.day_start[instrument] = False

    def strategy_select_instruments_for_entry(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []

        for instrument in instruments_bucket:
            if not (clock.CLOCK.now().time() >= self.candle_end_time):
                pass
            else:
                if self.day_start.get(instrument) is None:
                    self.set_and_subscribe_trading_symbols(instrument)
                elif not self.first_order_is_done:
                    if self.instruments.get(instrument) is not None:
                        for inst in self.instruments[instrument]:
                            selected_instruments_bucket.append(instrument)
                            sideband_info_bucket.append({'action': 'SELL', 'order': 'main', 'instrument': inst})
                            selected_instruments_bucket.append(instrument)
                            sideband_info_bucket.append({'action': 'SELL', 'order': 'stoploss', 'instrument': inst})
                            self.first_order_is_done = True

        return selected_instruments_bucket, sideband_info_bucket

    def strategy_enter_position(self, candle, instrument, sideband_info):
        if sideband_info['action'] == 'SELL':
            pushed_order = None
            qty = self.number_of_lots * instrument.lot_size
            fetched_instrument = sideband_info['instrument']
            self.logger.info('#' * 20)
            self.logger.info(f'FOR {sideband_info["order"]} ORDER, FETCHED INSTRUMENT IS - {fetched_instrument}')
            self.logger.info('#' * 20)
            ltp = self.broker.get_ltp(fetched_instrument)
            if sideband_info['order'] == 'main':
                variety = BrokerOrderVarietyConstants.MARKET
                self.main_order[fetched_instrument] = self.broker.SellOrderRegular(instrument=fetched_instrument,
                                                                                   order_code=BrokerOrderCodeConstants.INTRADAY if str(self.strategy_mode) == 'INTRADAY' else BrokerOrderCodeConstants.DELIVERY,
                                                                                   order_variety=variety,
                                                                                   quantity=qty)
                pushed_order = self.main_order[fetched_instrument]
                if str(fetched_instrument.tradingsymbol).endswith('CE'):
                    self.main_order_ce[fetched_instrument] = pushed_order
                    self.main_order_entry_price_ce[fetched_instrument] = ltp
                elif str(fetched_instrument.tradingsymbol).endswith('PE'):
                    self.main_order_pe[fetched_instrument] = pushed_order
                    self.main_order_entry_price_pe[fetched_instrument] = ltp
            elif sideband_info['order'] == 'stoploss':
                variety = BrokerOrderVarietyConstants.STOPLOSS_MARKET
                if self.main_order.get(instrument) is not None and \
                        self.main_order.get(instrument).get_order_status() is self.broker.constants.BROKER_ORDER_STATUS_CONSTANTS.value.COMPLETE:
                    self.stoploss_order[fetched_instrument] = self.broker.BuyOrderRegular(instrument=fetched_instrument,
                                                                                          order_code=BrokerOrderCodeConstants.INTRADAY if str(self.strategy_mode) == 'INTRADAY' else BrokerOrderCodeConstants.DELIVERY,
                                                                                          order_variety=variety,
                                                                                          quantity=qty,
                                                                                          trigger_price=ltp + self.stoploss_points,
                                                                                          position=BrokerExistingOrderPositionConstants.EXIT,
                                                                                          related_order=self.main_order[fetched_instrument])
                    pushed_order = self.stoploss_order[fetched_instrument]
                else:
                    self.logger.info('PROFIT ORDER NOT PLACED BECAUSE MAIN ORDER IS NOT YET COMPLETE. IGNORING AND CONTINUING')
                if str(fetched_instrument.tradingsymbol).endswith('CE'):
                    self.stoploss_order_ce[fetched_instrument] = pushed_order
                elif str(fetched_instrument.tradingsymbol).endswith('PE'):
                    self.stoploss_order_pe[fetched_instrument] = pushed_order
            else:
                raise NotImplementedError
        else:
            self.logger.fatal(f'Got invalid sideband_info value: {sideband_info}')
            raise SystemExit

        return pushed_order

    def strategy_select_instruments_for_exit(self, candle, instruments_bucket):

        selected_instruments_bucket = []
        sideband_info_bucket = []
        self.logger.info('------------------------- EXIT -------------------------')
        for instrument in instruments_bucket:
            if self.instruments.get(instrument) is not None:
                for inst in self.instruments.get(instrument):
                    instrument_ce, instrument_pe = self.extract_ce_pe_instruments(instrument)
                    if str(inst.tradingsymbol).endswith('CE'):
                        if self.stoploss_order_ce.get(inst) is not None and self.stoploss_order_ce.get(inst).get_order_status() is self.broker.constants.BROKER_ORDER_STATUS_CONSTANTS.value.COMPLETE:
                            self.logger.info(f'FOR INSTRUMENT {inst} SL ORDER IS COMPLETE')
                            self.logger.info('CANCELLING THE OTHER SL ORDER, AS WELL AS EXITING THE RELATED  MAIN ORDER')
                            if self.stoploss_order_pe.get(instrument_pe) is not None:
                                self.stoploss_order_pe[instrument_pe].cancel_order()
                            if self.main_order_pe.get(instrument_pe) is not None:
                                self.main_order_pe[instrument_pe].exit_position()
                            self.stoploss_order_pe[instrument_pe] = None
                            self.main_order_pe[instrument_pe] = None
                    elif str(inst.tradingsymbol).endswith('PE'):
                        if self.stoploss_order_pe.get(inst) is not None and self.stoploss_order_pe.get(inst).get_order_status() is self.broker.constants.BROKER_ORDER_STATUS_CONSTANTS.value.COMPLETE:
                            self.logger.info(f'FOR INSTRUMENT {inst} SL ORDER IS COMPLETE')
                            self.logger.info('CANCELLING THE OTHER SL ORDER, AS WELL AS EXITING THE RELATED  MAIN ORDER')
                            if self.stoploss_order_ce.get(instrument_ce) is not None:
                                self.stoploss_order_ce[instrument_ce].cancel_order()
                            if self.main_order_ce.get(instrument_ce) is not None:
                                self.main_order_ce[instrument_ce].exit_position()
                            self.stoploss_order_ce[instrument_ce] = None
                            self.main_order_ce[instrument_ce] = None
                    if self.main_order_pe.get(instrument_pe) is not None and self.main_order_ce.get(instrument_ce) is not None:
                        qty = self.number_of_lots * instrument.lot_size
                        main_order_entry_price_ce = self.main_order_entry_price_ce[instrument_ce]
                        main_order_entry_price_pe = self.main_order_entry_price_pe[instrument_pe]
                        main_order_ltp_ce = self.broker.get_ltp(instrument_ce)
                        main_order_ltp_pe = self.broker.get_ltp(instrument_pe)
                        delta_ce = (main_order_entry_price_ce - main_order_ltp_ce) * qty
                        delta_pe = (main_order_entry_price_pe - main_order_ltp_pe) * qty
                        delta_tot = delta_ce + delta_pe
                        self.logger.info(f'FOR {instrument_ce} ENTRY PRICE WAS {main_order_entry_price_ce} and CURRENT LTP IS {main_order_ltp_ce}')
                        self.logger.info(f'FOR {instrument_pe} ENTRY PRICE WAS {main_order_entry_price_pe} and CURRENT LTP IS {main_order_ltp_pe}')
                        self.logger.info(f'FOR {instrument_ce} DELTA_CE = ENTRY PRICE - LTP = {main_order_entry_price_ce} - {main_order_ltp_ce} = {delta_ce}')
                        self.logger.info(f'FOR {instrument_pe} DELTA_PE = ENTRY PRICE - LTP = {main_order_entry_price_pe} - {main_order_ltp_pe} = {delta_pe}')
                        self.logger.info('IF DELTA_CE + DELTA_PE >= PROFIT DESIRED, SQUARE OFF EVERYTHING')
                        self.logger.info(f'IF {delta_ce} + {delta_pe} = {delta_tot} >= {self.profit_desired}, SQUARE OFF EVERYTHING')
                        if delta_tot >= self.profit_desired:
                            self.logger.info(f'PROFIT DESIRED REACHED, SQUARING OFF EVERYTHING FOR {instrument_ce} and {instrument_pe}')
                            if self.stoploss_order_pe.get(instrument_pe) is not None:
                                self.stoploss_order_pe[instrument_pe].cancel_order()
                            self.main_order_pe[instrument_pe].exit_position()
                            if self.stoploss_order_ce.get(instrument_ce) is not None:
                                self.stoploss_order_ce[instrument_ce].cancel_order()
                            self.main_order_ce[instrument_ce].exit_position()
                            self.stoploss_order_pe[instrument_pe] = None
                            self.main_order_pe[instrument_pe] = None
                            self.stoploss_order_ce[instrument_ce] = None
                            self.main_order_ce[instrument_ce] = None

        return selected_instruments_bucket, sideband_info_bucket

    def strategy_exit_position(self, candle, instrument, sideband_info):
        return False

    def extract_ce_pe_instruments(self, instrument):
        instrument_ce = instrument_pe = None

        for inst in self.instruments[instrument]:
            if str(inst.tradingsymbol).endswith('CE'):
                instrument_ce = inst
            elif str(inst.tradingsymbol).endswith('PE'):
                instrument_pe = inst
            else:
                raise NotImplementedError

        return instrument_ce, instrument_pe
