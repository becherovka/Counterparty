#! /usr/bin/env python3

import os
import argparse
import json
import sqlite3

import colorama
colorama.init()
from prettytable import PrettyTable

import decimal
D = decimal.Decimal

import logging
import appdirs

import time
import dateutil.parser
from datetime import datetime

from lib import (config, util, exceptions, bitcoin, blocks)
from lib import (send, order, btcpay, issuance, broadcast, bet, dividend, burn, api)

json_print = lambda x: print(json.dumps(x, sort_keys=True, indent=4))

def format_order (order):
    price = D(order['get_amount']) / D(order['give_amount'])

    give_remaining = util.devise(D(order['give_remaining']), order['give_id'], 'output')
    get_remaining = util.devise(give_remaining * price, order['get_id'], 'ouput')
    give_name = util.get_asset_name(order['give_id'])
    get_name = util.get_asset_name(order['get_id'])
    give = str(give_remaining) + ' ' + give_name
    get = str(round(get_remaining, 8)) + ' ' + get_name

    price_string = str(price.quantize(config.FOUR).normalize())
    price_string += ' ' + get_name + '/' + give_name

    if order['fee_required']:
        fee = str(order['fee_required'] / config.UNIT) + ' BTC (required)'
    else:
        fee = str(order['fee_provided'] / config.UNIT) + ' BTC (provided)'

    return [give, get, price_string, fee, util.get_time_left(order), util.short(order['tx_hash'])]

def format_bet (bet):
    odds = D(bet['counterwager_amount']) / D(bet['wager_amount'])

    wager_remaining = D(bet['wager_remaining'])
    counterwager_remaining = round(wager_remaining * odds)

    if not bet['threshold']: threshold = None
    else: threshold = bet['threshold']
    if not bet['leverage']: leverage = None
    else: leverage = D(D(bet['leverage']) / 5040).quantize(config.FOUR).normalize()

    return [util.BET_TYPE_NAME[bet['bet_type']], bet['feed_address'], bet['deadline'], threshold, leverage, str(wager_remaining / config.UNIT) + ' XCP', str(counterwager_remaining / config.UNIT) + ' XCP', odds.quantize(config.FOUR).normalize(), util.get_time_left(bet), util.short(bet['tx_hash'])]

def format_order_match (order_match):
    order_match_id = order_match['tx0_hash'] + order_match['tx1_hash']
    order_match_time_left = util.get_order_match_time_left(order_match)
    return [order_match_id, order_match_time_left]

if __name__ == '__main__':
    data_dir_default = appdirs.user_data_dir('Counterparty', 'Counterparty')

    # Parse command‐line arguments.
    parser = argparse.ArgumentParser(prog='counterparty', description='')
    parser.add_argument('--version', action='store_true', 
                        help='print version information')
    parser.add_argument('--rpc-connect', default='localhost', help='')
    parser.add_argument('--rpc-port', type=int, default=18332, help='')    # testnet
    parser.add_argument('--rpc-user', default='bitcoinrpc', help='')
    parser.add_argument('--rpc-password', required=True, help='')
    parser.add_argument('--data-dir', default=data_dir_default, help='')
    parser.add_argument('--database-file', help='')
    parser.add_argument('--log-file', help='')

    subparsers = parser.add_subparsers(dest='action', 
                                       help='the action to be taken')

    parser_send = subparsers.add_parser('send', help='requires bitcoind')
    parser_send.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_send.add_argument('--to', metavar='DESTINATION', dest='destination', required=True, help='')
    parser_send.add_argument('--quantity', metavar='QUANTITY', required=True, help='')
    parser_send.add_argument('--asset', metavar='ASSET', dest='asset', required=True, help='')

    parser_order = subparsers.add_parser('order', help='requires bitcoind')
    parser_order.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_order.add_argument('--get-quantity', metavar='GET_QUANTITY', required=True, help='')
    parser_order.add_argument('--get-asset', metavar='GET_ASSET', required=True, help='')
    parser_order.add_argument('--give-quantity', metavar='GIVE_QUANTITY', required=True, help='')
    parser_order.add_argument('--give-asset', metavar='GIVE_ASSET', required=True, help='')
    parser_order.add_argument('--expiration', metavar='EXPIRATION', type=int, required=True, help='')
    parser_order.add_argument('--fee', metavar='FEE', required=True, help='either the required fee, or the provided fee, as appropriate; in BTC, to be paid to miners')

    parser_btcpay= subparsers.add_parser('btcpay', help='requires bitcoind')
    parser_btcpay.add_argument('--order-match-id', metavar='ORDER_MATCH_ID', required=True, help='')

    parser_issuance = subparsers.add_parser('issuance', help='requires bitcoind')
    parser_issuance.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_issuance.add_argument('--quantity', metavar='QUANTITY', required=True, help='')
    parser_issuance.add_argument('--asset-id', metavar='ASSET_ID', type=int, required=True, help='')
    parser_issuance.add_argument('--divisible', metavar='DIVISIBLE', type=bool, required=True, help='whether or not the asset is divisible (must agree with previous issuances, if this is a re‐issuance)')

    parser_broadcast = subparsers.add_parser('broadcast', help='requires bitcoind')
    parser_broadcast.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_broadcast.add_argument('--text', metavar='TEXT', required=True, help='')
    parser_broadcast.add_argument('--value', metavar='VALUE', type=float, default=0, help='numerical value of the broadcast')
    parser_broadcast.add_argument('--fee-multiplier', metavar='FEE_MULTIPLIER', required=True, help='how much of every bet on this feed should go to its operator; a fraction of 1, (i.e. .05 is five percent)')

    parser_order = subparsers.add_parser('bet', help='requires bitcoind')
    parser_order.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_order.add_argument('--feed-address', metavar='FEED_ADDRESS', required=True, help='')
    parser_order.add_argument('--bet-type', metavar='BET_TYPE', choices=list(util.BET_TYPE_NAME.values()), required=True, help='')
    parser_order.add_argument('--deadline', metavar='DEADLINE', required=True, help='')
    parser_order.add_argument('--wager', metavar='WAGER_QUANTITY', required=True, help='')
    parser_order.add_argument('--counterwager', metavar='COUNTERWAGER_QUANTITY', required=True, help='')
    parser_order.add_argument('--threshold', metavar='THRESHOLD', help='over‐under (?) (bet)')
    parser_order.add_argument('--leverage', metavar='LEVERAGE', type=int, default=5040, help='leverage, as a fraction of 5040')
    parser_order.add_argument('--expiration', metavar='EXPIRATION', type=int, required=True, help='')

    parser_dividend = subparsers.add_parser('dividend', help='requires bitcoind')
    parser_dividend.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_dividend.add_argument('--quantity-per-share', metavar='QUANTITY_PER_SHARE', required=True, help='in XCP')
    parser_dividend.add_argument('--share-asset', metavar='SHARE_ASSET', required=True, help='')

    parser_burn = subparsers.add_parser('burn', help='requires bitcoind')
    parser_burn.add_argument('--from', metavar='SOURCE', dest='source', required=True, help='')
    parser_burn.add_argument('--quantity', metavar='QUANTITY', required=True, help='quantity of BTC to be destroyed in miners’ fees')

    parser_watch = subparsers.add_parser('watch', help='')

    parser_history = subparsers.add_parser('history', help='')
    parser_history.add_argument('--address', metavar='ADDRESS', required=True, help='')

    args = parser.parse_args()

    # Configuration
    config.RPC = 'http://' + args.rpc_user + ':' + args.rpc_password + '@' + args.rpc_connect + ':' + str(args.rpc_port)

    if not args.data_dir: config.data_dir = data_dir_default
    else: config.data_dir = args.data_dir
    if not os.path.isdir(config.data_dir): os.mkdir(config.data_dir)

    if not args.database_file: config.DATABASE = data_dir_default + '/counterparty.' + str(config.DB_VERSION) + '.db'
    else: config.DATABASE = args.database_file
    db = sqlite3.connect(config.DATABASE)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    if not args.log_file: config.LOG = config.data_dir + '/counterparty.log'

    logging.basicConfig(filename=config.LOG, level=logging.INFO,
                        format='%(asctime)s %(message)s',
                        datefmt='%m-%d-%YT%I:%M:%S%z')
    requests_log = logging.getLogger("requests")
    requests_log.setLevel(logging.WARNING)

    # Do something.
    if args.version:
        print('This is Version 0.01 of counterparty.')

    elif args.action == 'send':
        bitcoin.bitcoind_check()

        asset_id = util.get_asset_id(args.asset)
        quantity = util.devise(args.quantity, asset_id, 'input')

        unsigned_tx_hex = send.create(args.source, args.destination,
                                      round(quantity), asset_id)
        json_print(bitcoin.transmit(unsigned_tx_hex))

    elif args.action == 'order':
        bitcoin.bitcoind_check()

        give_id = util.get_asset_id(args.give_asset)
        get_id = util.get_asset_id(args.get_asset)

        # Fee argument is either fee_required or fee_provided, as necessary.
        fee = round(D(args.fee) * config.UNIT)
        if not give_id:
            fee_provided = fee
            assert fee_provided >= config.MIN_FEE
            fee_required = 0
        elif not get_id:
            fee_required = fee
            assert fee_required >= config.MIN_FEE
            fee_provided = config.MIN_FEE

        give_quantity = util.devise(args.give_quantity, give_id, 'input')
        get_quantity = util.devise(args.get_quantity, get_id, 'input')
        unsigned_tx_hex = order.create(args.source, give_id, round(give_quantity),
                                get_id, round(get_quantity),
                                args.expiration, fee_required, fee_provided)
        json_print(bitcoin.transmit(unsigned_tx_hex))

    elif args.action == 'btcpay':
        json_print(btcpay.create(args.order_match_id))

    elif args.action == 'issuance':
        bitcoin.bitcoind_check()

        quantity = util.devise(args.quantity, asset_id, 'input')
        json_print(issuance.create(args.source, args.asset_id, round(quantity),
                                args.divisible))

    elif args.action == 'broadcast':
        bitcoin.bitcoind_check()

        # Use a magic number to store the fee multplier as an integer.
        fee_multiplier = D(args.fee_multiplier) * D(1e8)
        if fee_multiplier > 4294967295:
            raise exceptions.OverflowError('Fee multiplier must be less than or equal to 42.94967295.')

        json_print(broadcast.create(args.source, int(time.time()), args.value,
                                    round(fee_multiplier), args.text))

    elif args.action == 'bet':
        bitcoin.bitcoind_check()

        deadline = datetime.timestamp(dateutil.parser.parse(args.deadline))

        json_print(bet.create(args.source, args.feed_address,
                              util.BET_TYPE_ID[args.bet_type], round(deadline),
                              round(D(args.wager) * config.UNIT),
                              round(D(args.counterwager) * config.UNIT),
                              float(args.threshold), args.leverage,
                              args.expiration))

    elif args.action == 'dividend':
        bitcoin.bitcoind_check()

        asset_id = util.get_asset_id(args.share_asset)
        quantity_per_share = D(args.quantity_per_share) * config.UNIT

        json_print(dividend.create(args.source, round(quantity_per_share),
                                   asset_id))

    elif args.action == 'burn':
        bitcoin.bitcoind_check()
        unsigned_tx_hex = burn.create(args.source, round(D(args.quantity) * config.UNIT))
        json_print(bitcoin.transmit(unsigned_tx_hex))

    elif args.action == 'watch':
        while True:
            os.system('cls' if os.name=='nt' else 'clear')

            # Open orders.
            orders = api.get_orders(validity='Valid', show_expired=False, show_empty=False)
            table = PrettyTable(['Give', 'Get', 'Price', 'Fee', 'Time Left', 'Tx Hash'])
            for order in orders:
                order = format_order(order)
                table.add_row(order)
            print(colorama.Fore.WHITE + colorama.Style.BRIGHT + 'Open Orders' + colorama.Style.RESET_ALL)
            print(colorama.Fore.BLUE + str(table) + colorama.Style.RESET_ALL)
            print('\n')

            # Open bets.
            bets = api.get_bets(validity='Valid', show_expired=False, show_empty=False)
            table = PrettyTable(['Bet Type', 'Feed Address', 'Deadline', 'Threshold', 'Leverage', 'Wager', 'Counterwager', 'Odds', 'Time Left', 'Tx Hash'])
            for bet in bets:
                bet = format_bet(bet)
                table.add_row(bet)
            print(colorama.Fore.WHITE + colorama.Style.BRIGHT + 'Open Bets' + colorama.Style.RESET_ALL)
            print(colorama.Fore.GREEN + str(table) + colorama.Style.RESET_ALL)
            print('\n')

            # Matched orders waiting for BTC payments from you.
            my_addresses  = [ element['address'] for element in bitcoin.rpc('listreceivedbyaddress', [0,True])['result'] ]
            awaiting_btcs = api.get_order_matches(validity='Valid: awaiting BTC payment', addresses=my_addresses, show_expired=False)
            table = PrettyTable(['Matched Order ID', 'Time Left'])
            for order_match in awaiting_btcs:
                order_match = format_order_match(order_match)
                table.add_row(order_match)
            print(colorama.Fore.WHITE + colorama.Style.BRIGHT + 'Order Matches Awaiting BTC Payment' + colorama.Style.RESET_ALL)
            print(colorama.Fore.CYAN + str(table) + colorama.Style.RESET_ALL)

            time.sleep(30)
            
    elif args.action == 'history':
        history = api.get_history(args.address)

        # Balances.
        balances = history['balances']
        table = PrettyTable(['Asset', 'Amount'])
        for balance in balances:
            asset = util.get_asset_name(balance['asset_id'])
            amount = util.devise(balance['amount'], balance['asset_id'], 'output')
            table.add_row([asset, amount])
        print(colorama.Fore.WHITE + colorama.Style.BRIGHT + 'Balances' + colorama.Style.RESET_ALL)
        print(colorama.Fore.CYAN + str(table) + colorama.Style.RESET_ALL)
        print('\n')
 
        # Sends.
        sends = history['sends']
        table = PrettyTable(['Amount', 'Asset', 'Source', 'Destination', 'Tx Hash'])
        for send in sends:
            amount = util.devise(send['amount'], send['asset_id'], 'output')
            asset = util.get_asset_name(send['asset_id'])
            table.add_row([amount, asset, send['source'], send['destination'], util.short(send['tx_hash'])])
        print(colorama.Fore.WHITE + colorama.Style.BRIGHT + 'Sends' + colorama.Style.RESET_ALL)
        print(colorama.Fore.YELLOW + str(table) + colorama.Style.RESET_ALL)
        print('\n')

    elif args.action == 'help':
        parser.print_help()

    else:
        bitcoin.bitcoind_check()
        blocks.follow()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
