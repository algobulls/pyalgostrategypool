"""
    Strategy Description:
        The Strategy Options Short Straddle with SL trades based on the short straddle strategy and adds a stop loss (SL) to each leg.

    Strategy Parameters Description:
        Please refer to the link below for a detailed explanation of each strategy parameter:
        https://docs.google.com/spreadsheets/d/1-g128twcxHHlhIXO5a_Ycfu8eLDfTwjv1vofQlKpfAI/edit?usp=sharing
"""

from pyalgotrading.strategy import StrategyOptionsGiantNewV2


class StrategyOptionsShortStraddlewithSLIntraday(StrategyOptionsGiantNewV2):
    name = 'Strategy Options Short Straddle with SL Intraday'

    def __init__(self, *args, **kwargs):
        # Extract 'strategy_parameters' from kwargs
        strategy_parameters = kwargs['strategy_parameters']

        # --------------------------------------------
        # 1. Place Order Flags for Legs
        # Enable or disable individual legs (1 = active, 0 = inactive)
        strategy_parameters['PLACE_ORDER_LEG_ONE'] = 1
        strategy_parameters['PLACE_ORDER_LEG_TWO'] = 1

        # --------------------------------------------
        # 2. Transaction Type for Legs
        # 0 = Sell, 1 = Buy
        strategy_parameters['BUY_OR_SELL_LEG_ONE'] = 0
        strategy_parameters['BUY_OR_SELL_LEG_TWO'] = 0

        # --------------------------------------------
        # 3. Option Type for Legs
        # 0 = CE (Call), 1 = PE (Put)
        strategy_parameters['CE_OR_PE_LEG_ONE'] = 0
        strategy_parameters['CE_OR_PE_LEG_TWO'] = 1

        # --------------------------------------------
        # 4. Stoploss Settings per Leg
        # 1 = Allow SL to be applied on the leg
        strategy_parameters['ALLOW_STOPLOSS_LEG_ONE'] = 1
        strategy_parameters['ALLOW_STOPLOSS_LEG_TWO'] = 1

        # --------------------------------------------
        # 5. Global Strategy Limits
        # Used to avoid placing orders outside of logical range
        strategy_parameters['ALLOW_LOWER_LIMIT'] = 1
        strategy_parameters['ALLOW_UPPER_LIMIT'] = 1

        # --------------------------------------------
        # 6. Entry and Re-entry Configuration
        strategy_parameters['REENTRY_ORDER_COUNT'] = 0  # 0 = No reentry on SL
        strategy_parameters['ENTRY_TYPE'] = 0  # 0 = Entry on signal, 1 = Manual or other logic

        # --------------------------------------------
        # 7. Trailing Stop Loss Module Control
        # 1 = Use TSL module if available in strategy
        strategy_parameters['USE_TSL_MODULE'] = 1

        # Call parent class constructor with modified 'strategy_parameters'
        super().__init__(*args, **kwargs)
