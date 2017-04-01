import json
import argparse
import os

import bongbot


parser = argparse.ArgumentParser()
default_config_file = '{}/.config/bongbot/bongbot.json'.format(os.path.expanduser('~'))
parser.add_argument(
    '--config',
    '-c',
    default=default_config_file,
    help='Path to configuration file. Default: {}'.format(default_config_file)
)

args = parser.parse_args()

with open(args.config, 'r') as fd:
    config = json.load(fd)

bot = bongbot.Bongbot(config)
bot.run()
