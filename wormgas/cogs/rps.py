import discord
import discord.ext.commands as cmds
import logging
import random
from wormgas.config import ConfigManager
from wormgas.wormgas import Wormgas

log = logging.getLogger(__name__)


class RpsCog:
    canonical_actions = {
        'rock': 'rock',
        'paper': 'paper',
        'scissors': 'scissors',
        '\u2702': 'scissors',
        '\ufe0f': 'scissors'
    }

    def __init__(self, bot: Wormgas):
        self.bot = bot
        self.config_path = bot.config.path.with_name('_rps.json')
        self.config: ConfigManager = bot.config.__class__(self.config_path)

    async def get_rps_record(self, player: discord.Member):
        player_id = str(player.id)
        if player_id not in self.config:
            return f'{player.display_name} does not play. :('

        player_dict = self.config.get(player_id)
        w = player_dict.get('wins', 0)
        d = player_dict.get('draws', 0)
        _l = player_dict.get('losses', 0)
        t = w + d + _l
        m = f'RPS record for {player.display_name} ({t} game'
        if t != 1:
            m = f'{m}s'
        m = f'{m}) is {w}-{d}-{_l} (w-d-l).'
        return m

    async def get_rps_stats(self, player: discord.Member):
        player_id = str(player.id)
        if player_id not in self.config:
            return f'{player.display_name} does not play. :('

        player_dict = self.config.get(player_id)
        r = player_dict.get('rock', 0)
        p = player_dict.get('paper', 0)
        s = player_dict.get('scissors', 0)
        t = r + p + s
        if t > 0:
            r_rate = r / float(t) * 100
            p_rate = p / float(t) * 100
            s_rate = s / float(t) * 100
            m = f'{player.display_name} challenges with rock/paper/scissors at these rates: '
            m = f'{m}{r_rate:3.1f}/{p_rate:3.1f}/{s_rate:3.1f}%.'
        else:
            m = f'{player.display_name} does not play. :('
        return m

    async def play_game(self, challenger: discord.User, action: str):
        challenger = str(challenger.id)
        action = self.canonical_actions[action]
        action_map = ['rock', 'paper', 'scissors']
        challenge = action_map.index(action)
        response = random.randint(0, 2)
        player_dict = self.config.get(str(challenger), {})
        global_dict = self.config.get('!global', {})
        player_dict[action] = player_dict.get(action, 0) + 1
        global_dict[action] = global_dict.get(action, 0) + 1

        m = f'You challenge with **{action}**. I counter with **{action_map[response]}**.'

        if challenge == (response + 1) % 3:
            player_dict['wins'] = player_dict.get('wins', 0) + 1
            global_dict['wins'] = global_dict.get('wins', 0) + 1
            m = m + ' You win!'
        elif challenge == response:
            player_dict['draws'] = player_dict.get('draws', 0) + 1
            global_dict['draws'] = global_dict.get('draws', 0) + 1
            m = m + ' We draw!'
        elif challenge == (response + 2) % 3:
            player_dict['losses'] = player_dict.get('losses', 0) + 1
            global_dict['losses'] = global_dict.get('losses', 0) + 1
            m = m + ' You lose!'

        self.config[challenger] = player_dict
        self.config['!global'] = global_dict

        w = player_dict.get('wins', 0)
        d = player_dict.get('draws', 0)
        _l = player_dict.get('losses', 0)
        pw = int(float(w) / float(w + d + _l) * 100)
        pd = int(float(d) / float(w + d + _l) * 100)
        pl = int(float(_l) / float(w + d + _l) * 100)
        return m + f' Your current record is {w}-{d}-{_l} or {pw}%-{pd}%-{pl}% (w-d-l).'

    @cmds.command(name='rock', aliases=['paper', 'scissors', '\u2702'])
    async def rock(self, ctx: cmds.Context):
        """Play a game of rock-paper-scissors."""
        await ctx.send(await self.play_game(ctx.author, ctx.invoked_with))

    @cmds.group(name='rps')
    async def rps(self, ctx: cmds.Context):
        """Administrative commands for rock-paper-scissors."""

    @rps.command()
    async def record(self, ctx: cmds.Context, player: discord.Member = None):
        """Request the record for a rock-paper-scissors player."""
        if player is None:
            player = ctx.author
        await ctx.send(await self.get_rps_record(player))

    @rps.command()
    async def stats(self, ctx: cmds.Context, player: discord.Member = None):
        """Request statistics for a rock-paper-scissors player."""
        if player is None:
            player = ctx.author
        await ctx.send(await self.get_rps_stats(player))

    @rps.command()
    async def reset(self, ctx: cmds.Context, reset_code: str = None):
        """Reset your record and delete your game history."""
        player_dict = self.config.get(str(ctx.author.id))
        if reset_code and reset_code == player_dict.get('reset_code'):
            self.config.remove(str(ctx.author.id))
            await ctx.author.send(f'I reset your RPS record and deleted your game history.')
        else:
            reset_code = f'{random.randrange(999999):06d}'
            player_dict['reset_code'] = reset_code
            await ctx.author.send(f'Use !rps reset {reset_code} to reset your RPS record and delete your history.')


def setup(bot: Wormgas):
    bot.add_cog(RpsCog(bot))
