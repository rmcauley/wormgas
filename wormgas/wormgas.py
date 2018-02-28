import argparse
import discord.ext.commands as cmds
import logging
import pathlib
import sys

from wormgas.config import ConfigManager


class Wormgas(cmds.Bot):
    config: ConfigManager


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    return parser.parse_args()


def main():
    logging.basicConfig(level='INFO', format='%(asctime)s | %(name)s | %(levelname)s | %(message)s', stream=sys.stdout)
    args = parse_args()
    bot = Wormgas(command_prefix='!', pm_help=True)
    bot.config = ConfigManager(pathlib.Path(args.config).resolve())
    bot.load_extension('wormgas.cogs.chat')
    bot.load_extension('wormgas.cogs.config')
    bot.load_extension('wormgas.cogs.rainwave')
    bot.load_extension('wormgas.cogs.rand')
    bot.load_extension('wormgas.cogs.rps')
    bot.load_extension('wormgas.cogs.wiki')
    bot.load_extension('wormgas.cogs.wolframalpha')
    bot.run(bot.config.get('discord:token'))
