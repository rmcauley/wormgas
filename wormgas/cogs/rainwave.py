import asyncio
import datetime
import discord
import discord.ext.commands as cmds
import enum
import json
import logging
import pytz
import time
import urllib.parse
import urllib.request
import uuid

from typing import Dict
from wormgas.config import ConfigManager
from wormgas.util import to_bool
from wormgas.wormgas import Wormgas

log = logging.getLogger(__name__)


class RainwaveChannel(enum.Enum):
    game = 1
    rw = 1
    oc = 2
    ocr = 2
    cover = 3
    covers = 3
    mw = 3
    vw = 3
    bw = 4
    ch = 4
    chip = 4
    all = 5
    omni = 5
    ow = 5

    @property
    def channel_id(self):
        return self.value

    @property
    def long_name(self):
        return f'{self.short_name} channel'

    @property
    def short_name(self):
        return (None, 'Game', 'OCR', 'Covers', 'Chiptune', 'All')[self.value]

    @property
    def url(self):
        subdomains = ('', 'game.', 'ocr.', 'covers.', 'chiptune.', 'all.')
        return f'https://{subdomains[self.value]}rainwave.cc/'


class RainwaveCog:
    def __init__(self, bot: Wormgas):
        self.bot = bot
        self.config_path = bot.config.path.with_name('_rainwave.json')
        self.config = ConfigManager(self.config_path)
        self.nick_not_recognized = ('I do not recognize you. Use **!id add <id>** to link your Rainwave and Discord '
                                    'accounts.')
        self.missing_key = ('I do not have a key stored for you. Visit https://rainwave.cc/keys/ to get a key and tell '
                            'me about it with **!key add <key>**.')
        self.not_tuned_in = 'You are not tuned in and you did not specify a valid channel code.'
        codes = [code for code in RainwaveChannel.__members__.keys()]
        chan_code_ls = '**, **'.join(codes)
        self.channel_codes = f'Channel codes are **{chan_code_ls}**.'
        self.topic_task = self.bot.loop.create_task(self.check_special_events())

    @staticmethod
    async def _call(path: str, params: Dict = None):
        if params is None:
            params = {}
        base_url = 'https://rainwave.cc/api4/'
        url = base_url + path.lstrip('/')
        data = urllib.parse.urlencode(params).encode()
        headers = {'user-agent': str(uuid.uuid4())}
        request = urllib.request.Request(url, data, headers)
        response = urllib.request.urlopen(request)
        if response.status == 200:
            body = response.read().decode()
            return json.loads(body)
        raise RuntimeError

    @staticmethod
    def artist_string(artists):
        return ', '.join([a.get('name') for a in artists])

    def song_string(self, song, simple=False):
        album = song.get("albums")[0].get("name")
        title = song.get('title')
        artists = self.artist_string(song.get('artists'))
        m = f'{album} // {title} // {artists}'

        if simple:
            return m

        url = song.get('url')
        if url is not None:
            m += f' [ {url} ]'

        vote_count = song.get('entry_votes', 0)
        vote_plural = 's'
        if int(vote_count) == 1:
            vote_plural = ''
        rating = song.get('rating')
        m += f' ({vote_count} vote{vote_plural}, rated {rating}'

        elec_request_username = song.get('elec_request_username')
        if elec_request_username is not None:
            m += f', requested by {elec_request_username}'

        m += ')'
        return m

    async def get_api_auth_for_user(self, user: discord.User):
        auth = {'user_id': await self.get_id_for_user(user)}
        user_id = auth.get('user_id')
        auth['key'] = await self.get_key_for_user(user)
        auth['chan'] = await self.get_current_channel_for_id(user_id)
        return auth

    async def get_current_channel_for_id(self, listener_id: int):
        if listener_id is None:
            return None
        user_id = self.bot.config.get('rainwave:user_id')
        key = self.bot.config.get('rainwave:key')
        d = await self.rw_listener(user_id, key, listener_id)
        listener_name = d.get('listener').get('name')
        return await self.get_current_channel_for_name(listener_name)

    async def get_current_channel_for_name(self, name: str):
        user_id = self.bot.config.get('rainwave:user_id')
        key = self.bot.config.get('rainwave:key')
        d = await self.rw_user_search(user_id, key, name)
        chan_id = d.get('user').get('sid')
        if chan_id is None:
            return None
        return RainwaveChannel(int(chan_id))

    async def get_id_for_name(self, username: str):
        rw_user_id = self.bot.config.get('rainwave:user_id')
        key = self.bot.config.get('rainwave:key')
        d = await self.rw_user_search(rw_user_id, key, username)
        return d.get('user').get('user_id')

    async def get_id_for_user(self, user: discord.User):
        user_id = str(user.id)
        listener_id = self.config.get(user_id, {}).get('id')
        if listener_id is None:
            listener_id = await self.get_id_for_name(user.display_name)
        return listener_id

    async def get_key_for_user(self, user: discord.User):
        user_id = str(user.id)
        return self.config.get(user_id, {}).get('key')

    async def rw_admin_list_producers_all(self, user_id, key):
        params = {
            'user_id': user_id,
            'key': key
        }
        return await self._call('admin/list_producers_all', params=params)

    async def rw_clear_requests(self, user_id, key, sid):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid
        }
        return await self._call('clear_requests', params=params)

    async def rw_current_listeners(self, user_id, key, sid):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid
        }
        return await self._call('current_listeners', params=params)

    async def rw_info(self, sid):
        params = {'sid': sid}
        return await self._call('info', params=params)

    async def rw_info_all(self):
        params = {'sid': 1}
        return await self._call('info_all', params=params)

    async def rw_listener(self, user_id, key, listener_id):
        params = {
            'user_id': user_id,
            'key': key,
            'id': listener_id
        }
        return await self._call('listener', params=params)

    async def rw_pause_request_queue(self, user_id, key, sid):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid
        }
        return await self._call('pause_request_queue', params=params)

    async def rw_request(self, user_id, key, sid, song_id):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid,
            'song_id': song_id
        }
        return await self._call('request', params=params)

    async def rw_request_favorited_songs(self, user_id, key, sid):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid
        }
        return await self._call('request_favorited_songs', params=params)

    async def rw_request_unrated_songs(self, user_id, key, sid):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid
        }
        return await self._call('request_unrated_songs', params=params)

    async def rw_song(self, user_id, key, sid, song_id):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid,
            'id': song_id
        }
        return await self._call('song', params=params)

    async def rw_unpause_request_queue(self, user_id, key, sid):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid
        }
        return await self._call('unpause_request_queue', params=params)

    async def rw_user_search(self, user_id, key, username):
        params = {
            'user_id': user_id,
            'key': key,
            'username': username
        }
        return await self._call('user_search', params=params)

    async def rw_vote(self, user_id, key, sid, entry_id):
        params = {
            'user_id': user_id,
            'key': key,
            'sid': sid,
            'entry_id': entry_id
        }
        return await self._call('vote', params=params)

    @staticmethod
    def build_event_dict(chan, info):
        event_name = info['event_name']
        event = {'chan_id': chan.channel_id, 'chan_url': chan.url, 'chan_short_name': chan.short_name,
                 'name': f'{event_name} Power Hour'}
        event['text'] = '[{chan_short_name}] {name} on now!'.format(**event)
        return event

    async def get_current_events(self):
        current_events = []
        d = await self.rw_info_all()
        for sid, info in d.get('all_stations_info', {}).items():
            if info['event_type'] == 'OneUp':
                chan = RainwaveChannel(int(sid))
                event = self.build_event_dict(chan, info)
                current_events.append(event)
        return current_events

    async def get_future_events(self):
        future_events = []
        user_id = self.bot.config.get('rainwave:user_id')
        key = self.bot.config.get('rainwave:key')
        d = await self.rw_admin_list_producers_all(user_id=user_id, key=key)
        for p in d.get('producers', []):
            if p['type'] == 'OneUpProducer':
                chan = RainwaveChannel(p['sid'])
                e_name = p['name']
                when = pytz.timezone('US/Eastern').localize(datetime.datetime.fromtimestamp(p['start']))
                month = when.strftime('%b')
                w_time = when.strftime('%H:%M')
                e_text = f'[{chan.short_name}] {e_name} Power Hour: {month} {when.day} {w_time} Eastern'
                future_events.append(e_text)
        return future_events

    async def check_special_events(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            new_topic_head = 'Welcome to Rainwave!'
            event_now = False
            events = await self.get_current_events()
            future_events = await self.get_future_events()
            if events:
                event_now = True
                new_topic_head = ' '.join([e['text'] for e in events])
            elif future_events:
                new_topic_head = future_events[0]
            for channel_id in self.bot.config.get('rainwave:topic_control', []):
                channel = self.bot.get_channel(channel_id)
                channel_topic = channel.topic
                if channel_topic is None:
                    channel_topic = ''
                topic_parts = channel_topic.split(' | ')
                if new_topic_head != topic_parts[0]:
                    topic_parts[0] = new_topic_head
                    await channel.edit(topic=' | '.join(topic_parts))
                    if event_now:
                        for e in events:
                            m = '{text} {chan_url}'.format(**e)
                            await channel.send(m)
            await asyncio.sleep(60)

    @cmds.command()
    @cmds.has_permissions(manage_channels=True)
    async def topic(self, ctx: cmds.Context, on_off: to_bool = None):
        """Turn automatic topic control on or off."""
        if isinstance(ctx.channel, discord.TextChannel):
            topic_control_list = self.bot.config.get('rainwave:topic_control', [])
            if ctx.channel.id in topic_control_list:
                topic_control_list.remove(ctx.channel.id)
            if on_off:
                topic_control_list.append(ctx.channel.id)
                await ctx.author.send(f'Topic control is ON for {ctx.channel.mention}')
            else:
                await ctx.author.send(f'Topic control is OFF for {ctx.channel.mention}')
            self.bot.config['rainwave:topic_control'] = topic_control_list

    @cmds.group()
    async def id(self, ctx: cmds.Context):
        """Manage your Rainwave user id."""

    @id.command(name='add')
    async def id_add(self, ctx: cmds.Context, rainwave_id: int):
        """Add your Rainwave user id to your Discord account."""
        discord_id = str(ctx.author.id)
        user_dict = self.config.get(discord_id, {})
        user_dict['id'] = rainwave_id
        self.config[discord_id] = user_dict
        await ctx.author.send(f'I assigned the user id {rainwave_id} to {ctx.author.mention}')

    @id.command(name='drop')
    async def id_drop(self, ctx: cmds.Context):
        """Drop your Rainwave user id from your Discord account."""
        discord_id = str(ctx.author.id)
        user_dict = self.config.get(discord_id, {})
        if 'id' in user_dict:
            del user_dict['id']
            self.config[discord_id] = user_dict
        await ctx.author.send(f'I dropped the user id for {ctx.author.mention}')

    @id.command(name='show')
    async def id_show(self, ctx: cmds.Context):
        """See the Rainwave user id associated with your Discord account."""
        discord_id = str(ctx.author.id)
        user_dict = self.config.get(discord_id, {})
        if 'id' in user_dict:
            rainwave_id = user_dict['id']
            await ctx.author.send(f'The user id for {ctx.author.mention} is {rainwave_id}.')
        else:
            await ctx.author.send(f'I do not have a user id for {ctx.author.mention}.')

    @cmds.group()
    async def key(self, ctx: cmds.Context):
        """Manage your Rainwave key"""

    @key.command(name='add')
    async def key_add(self, ctx: cmds.Context, rainwave_key: str):
        """Add your Rainwave key to your Discord account."""
        discord_id = str(ctx.author.id)
        user_dict = self.config.get(discord_id, {})
        user_dict['key'] = rainwave_key
        self.config[discord_id] = user_dict
        await ctx.author.send(f'I assigned the key {rainwave_key} to {ctx.author.mention}.')

    @key.command(name='drop')
    async def key_drop(self, ctx: cmds.Context):
        """Drop your Rainwave key from your Discord account."""
        discord_id = str(ctx.author.id)
        user_dict = self.config.get(discord_id, {})
        if 'id' in user_dict:
            del user_dict['id']
            self.config[discord_id] = user_dict
        await ctx.author.send(f'I dropped the key for {ctx.author.mention}')

    @key.command(name='show')
    async def key_show(self, ctx: cmds.Context):
        """See the Rainwave key associated with your Discord account."""
        discord_id = str(ctx.author.id)
        user_dict = self.config.get(discord_id, {})
        if 'key' in user_dict:
            rainwave_key = user_dict['key']
            await ctx.author.send(f'The key for {ctx.author.mention} is {rainwave_key}.')
        else:
            await ctx.author.send(f'I do not have a key for {ctx.author.mention}')

    @cmds.command()
    async def lstats(self, ctx: cmds.Context):
        """See information about current Rainwave radio listeners."""
        m = 'Registered listeners: '
        total = 0
        user_id = self.bot.config.get('rainwave:user_id')
        key = self.bot.config.get('rainwave:key')
        for chan in RainwaveChannel:
            d = await self.rw_current_listeners(user_id, key, chan.channel_id)
            count = len(d.get('current_listeners'))
            m += f'{chan.long_name} = {count}, '
            total += count
        m += f'Total = {total}'
        await ctx.send(m)

    @cmds.command(name='next', aliases=['nx', 'nxgame', 'nxrw', 'nxoc', 'nxocr', 'nxcover', 'nxcovers', 'nxmw', 'nxvw',
                                        'nxbw', 'nxch', 'nxchip', 'nxall', 'nxomni', 'nxow'])
    async def next(self, ctx: cmds.Context, chan_abbr: str = None):
        """See what will play next on the radio.

        Use "!next [<channel>]" to show what is up next on the radio.
        Short version is "!nx[<channel>]".
        Leave off <channel> to auto-detect the channel you are tuned to."""

        cmd = ctx.invoked_with
        chan = None
        idx = 0

        if cmd in ['nxgame', 'nxrw']:
            chan = RainwaveChannel.game
        elif cmd in ['nxoc', 'nxocr']:
            chan = RainwaveChannel.ocr
        elif cmd in ['nxcover', 'nxcovers', 'nxmw', 'nxvw']:
            chan = RainwaveChannel.covers
        elif cmd in ['nxbw', 'nxch', 'nxchip']:
            chan = RainwaveChannel.chip
        elif cmd in ['nxall', 'nxomni', 'nxow']:
            chan = RainwaveChannel.all
        elif cmd in ['next', 'nx']:
            if chan_abbr:
                if chan_abbr.lower() in RainwaveChannel.__members__.keys():
                    chan = RainwaveChannel[chan_abbr.lower()]
            if chan is None:
                listener_id = await self.get_id_for_user(ctx.author)
                chan = await self.get_current_channel_for_id(listener_id)
            if chan is None:
                await ctx.author.send(self.not_tuned_in)
                return

        m = f'Next up on the {chan.long_name}'
        d = await self.rw_info(chan.channel_id)
        event = d.get('sched_next')[idx]
        sched_id = int(event.get('id'))
        sched_type = event.get('type')
        sched_name = event.get('name')
        if sched_type == 'OneUp':
            m += f' ({sched_name} Power Hour):'
            song = event.get('songs')[0]
            m += f' {self.song_string(song)}'
        elif sched_type == 'Election':
            if sched_name:
                m += f' ({sched_name})'
            m += f':'
            for i, s in enumerate(event.get('songs'), start=1):
                song_string = self.song_string(s, simple=True)
                m += f' **[{i}]** {song_string}'
                req = s.get('elec_request_username')
                if req:
                    m += f' (requested by {req})'

        if ctx.guild:
            config_id = f'rainwave:nx:{chan.channel_id}:{idx}'
            if sched_id == self.bot.config.get(config_id, 0):
                c = f'You can only use **{cmd}** in {ctx.channel.mention} once per song.'
                await ctx.author.send(c)
                await ctx.author.send(m)
            else:
                self.bot.config.set(config_id, sched_id)
                await ctx.send(m)
        else:
            await ctx.send(m)

    @cmds.command(name='nowplaying', aliases=['np', 'npgame', 'nprw', 'npoc', 'npocr', 'npcover', 'npcovers', 'npmw',
                                              'npvw', 'npbw', 'npch', 'npchip', 'npall', 'npomni', 'npow'])
    async def nowplaying(self, ctx: cmds.Context, channel: str = None):
        """See what is now playing on the radio.

        Use "!nowplaying [<channel>]" to show what is now playing on the radio.
        Short version is "!np[<channel>]".
        Leave off <channel> to auto-detect the channel you are tuned to."""

        cmd = ctx.invoked_with
        chan = None

        if cmd in ['npgame', 'nprw']:
            chan = RainwaveChannel.game
        elif cmd in ['npoc', 'npocr']:
            chan = RainwaveChannel.ocr
        elif cmd in ['npcover', 'npcovers', 'npmw', 'npvw']:
            chan = RainwaveChannel.cover
        elif cmd in ['npbw', 'npch', 'npchip']:
            chan = RainwaveChannel.chip
        elif cmd in ['npall', 'npomni', 'npow']:
            chan = RainwaveChannel.all
        elif cmd in ['nowplaying', 'np']:
            if channel:
                if channel.lower() in RainwaveChannel.__members__.keys():
                    chan = RainwaveChannel[channel.lower()]
            if chan is None:
                listener_id = await self.get_id_for_user(ctx.author)
                chan = await self.get_current_channel_for_id(listener_id)
            if chan is None:
                await ctx.author.send(self.not_tuned_in)
                return

        m = f'Now playing on the {chan.long_name}'
        d = await self.rw_info(chan.channel_id)
        event = d.get('sched_current')
        sched_id = int(event.get('id'))
        sched_type = event.get('type')
        sched_name = event.get('name')
        if sched_type == 'Election' and sched_name:
            m += f' ({sched_name})'
        elif sched_type == 'OneUp':
            m += f' ({sched_name} Power Hour)'
        song = event.get('songs')[0]
        m += f': {self.song_string(song)}'

        if ctx.guild:
            last = self.bot.config.get(f'rainwave:np:{chan.channel_id}', 0)
            if sched_id == last:
                c = f'You can only use **{cmd}** in {ctx.channel.mention} once per song.'
                await ctx.author.send(c)
                await ctx.author.send(m)
            else:
                self.bot.config.set(f'rainwave:np:{chan.channel_id}', sched_id)
                await ctx.send(m)
        else:
            await ctx.send(m)

    @cmds.command(aliases=['pp', 'ppgame', 'pprw', 'ppoc', 'ppocr', 'ppcover', 'ppcovers', 'ppmw', 'ppvw', 'ppbw',
                           'ppch', 'ppchip', 'ppall', 'ppomni', 'ppow'])
    async def prevplayed(self, ctx: cmds.Context, *, args: str = None):
        """Show what was previously playing on the radio.

        Use "!prevplayed [<channel>] [<index>]" to show what was previously playing on the radio.
        Short version is "!pp[<channel>] [<index>]".
        Leave off <channel> to auto-detect the channel you are tuned to.
        <index> should be a number from 0 to 4 (default 0). The higher the number, the further back in time you go."""

        cmd = ctx.invoked_with
        if args is None:
            args = ''
        tokens = args.split()

        chan = None
        idx = 0

        if cmd in ['ppgame', 'pprw']:
            chan = RainwaveChannel.game
        elif cmd in ['ppoc', 'ppocr']:
            chan = RainwaveChannel.ocr
        elif cmd in ['ppcover', 'ppcovers', 'ppmw', 'ppvw']:
            chan = RainwaveChannel.cover
        elif cmd in ['ppbw', 'ppch', 'ppchip']:
            chan = RainwaveChannel.chip
        elif cmd in ['ppall', 'ppomni', 'ppow']:
            chan = RainwaveChannel.all

        if chan in RainwaveChannel and len(tokens) > 0 and tokens[0].isdigit():
            if int(tokens[0]) in range(5):
                idx = int(tokens[0])

        if cmd in ['prevplayed', 'pp']:
            if len(tokens) > 0:
                if tokens[0].isdigit() and int(tokens[0]) in range(5):
                    idx = int(tokens[0])
                else:
                    if tokens[0].lower() in RainwaveChannel.__members__.keys():
                        chan = RainwaveChannel[tokens[0].lower()]
                    if len(tokens) > 1:
                        if tokens[1].isdigit() and int(tokens[1]) in range(5):
                            idx = int(tokens[1])
            if chan is None:
                listener_id = await self.get_id_for_user(ctx.author)
                if listener_id is None:
                    await ctx.author.send(self.nick_not_recognized)
                    return
                chan = await self.get_current_channel_for_id(listener_id)
            if chan is None:
                await ctx.author.send(self.not_tuned_in)
                return

        m = f'Previously on the {chan.long_name}'
        d = await self.rw_info(chan.channel_id)
        event = d.get('sched_history')[idx]
        sched_id = int(event.get('id'))
        sched_type = event.get('type')
        sched_name = event.get('name')
        if sched_type == 'Election' and sched_name:
            m += f' ({sched_name})'
        elif sched_type == 'OneUp':
            m += f' ({sched_name} Power Hour)'
        song = event.get('songs')[0]
        m += f': {self.song_string(song)}'

        if ctx.guild:
            last_sched_id = f'rainwave:pp:{chan.channel_id}:{idx}'
            if sched_id == self.bot.config.get(last_sched_id, 0):
                c = f'You can only use {cmd} in {ctx.channel.mention} once per song.'
                await ctx.author.send(c)
                await ctx.author.send(m)
            else:
                self.bot.config.set(last_sched_id, sched_id)
                await ctx.send(m)
        else:
            await ctx.send(m)

    @cmds.command(aliases=['rq'])
    async def request(self, ctx: cmds.Context, *, args: str = None):
        """Manage your Rainwave request queue.

        Use "!rq <song_id>" to add a song to your request queue.
        Use "!rq unrated" to fill your request queue with unrated songs.
        Use "!rq fav" to add favourite songs to your request queue.
        Use "!rq pause" to pause your request queue.
        Use "!rq resume" to resume your request queue.
        Use "!rq clear" to remove all songs from your request queue."""

        if args is None:
            args = ''
        tokens = args.split()

        if len(tokens) < 1:
            await ctx.author.send('Command not complete.')
            return

        auth = await self.get_api_auth_for_user(ctx.author)
        if auth.get('user_id') is None:
            await ctx.author.send(self.nick_not_recognized)
            return
        if auth.get('key') is None:
            await ctx.author.send(self.missing_key)
            return
        if auth.get('chan') is None:
            await ctx.author.send('You must be tuned in to request.')
            return

        if tokens[0].isdigit():
            song_id = int(tokens[0])
            user_id = auth.get('user_id')
            key = auth.get('key')
            chan_id = auth.get('chan').channel_id
            d = await self.rw_song(user_id, key, chan_id, song_id)
            song = d.get('song')
            song_str = self.song_string(song, simple=True)
            await ctx.author.send(f'Attempting request: {song_str}')
            d = await self.rw_request(user_id, key, chan_id, song_id)
            await ctx.author.send(d.get('request_result').get('text'))

        elif tokens[0] == 'unrated':
            user_id = auth.get('user_id')
            key = auth.get('key')
            chan_id = auth.get('chan').channel_id
            d = await self.rw_request_unrated_songs(user_id, key, chan_id)
            await ctx.author.send(d.get('request_unrated_songs_result').get('text'))

        elif tokens[0] == 'fav':
            user_id = auth.get('user_id')
            key = auth.get('key')
            chan_id = auth.get('chan').channel_id
            d = await self.rw_request_favorited_songs(user_id, key, chan_id)
            await ctx.author.send(d.get('request_favorited_songs_result').get('text'))

        elif tokens[0] == 'clear':
            user_id = auth.get('user_id')
            key = auth.get('key')
            chan_id = auth.get('chan').channel_id
            await self.rw_clear_requests(user_id, key, chan_id)
            await ctx.author.send('Request queue cleared.')

        elif tokens[0] == 'pause':
            user_id = auth.get('user_id')
            key = auth.get('key')
            chan_id = auth.get('chan').channel_id
            d = await self.rw_pause_request_queue(user_id, key, chan_id)
            await ctx.author.send(d.get('pause_request_queue_result').get('text'))

        elif tokens[0] == 'resume':
            user_id = auth.get('user_id')
            key = auth.get('key')
            chan_id = auth.get('chan').channel_id
            d = await self.rw_unpause_request_queue(user_id, key, chan_id)
            await ctx.author.send(d.get('unpause_request_queue_result').get('text'))

    @cmds.command()
    async def ustats(self, ctx: cmds.Context, *, username: str = None):
        """See some statistics about a Rainwave user.

        Use "!ustats [<username>]" to see some statistics about a Rainwave user.
        Leave off <username> to see your own stats."""

        log.info(f'username: {username!r}')
        out = []

        if username is None:
            listener_id = await self.get_id_for_user(ctx.author)
            if listener_id is None:
                await ctx.author.send(f'Use **!id add <id>** to connect your Rainwave and Discord accounts.')
                return
        elif username.startswith('<@') and username.endswith('>') and username[2:-1].isdigit():
            member = discord.utils.get(ctx.guild.members, id=int(username[2:-1]))
            username = member.display_name
            listener_id = await self.get_id_for_user(member)
        else:
            listener_id = await self.get_id_for_name(username)

        if listener_id is None:
            await ctx.author.send(f'{username} is not a valid Rainwave user.')
            return

        user_id = self.bot.config.get('rainwave:user_id')
        key = self.bot.config.get('rainwave:key')
        d = await self.rw_listener(user_id, key, listener_id)
        cun = d.get('listener').get('name')
        completion = d.get('listener').get('rating_completion')
        game = int(completion.get('1', 0))
        ocr = int(completion.get('2', 0))
        cover = int(completion.get('3', 0))
        chip = int(completion.get('4', 0))
        m = f'{cun} has rated {game}% of Game, {ocr}% of OCR, {cover}% of Covers, {chip}% of Chiptune channel content.'
        out.append(m)

        current_channel = await self.get_current_channel_for_name(cun)
        if current_channel:
            m = f'{cun} is currently listening to the {current_channel.long_name}.'
            out.append(m)

        if not ctx.guild:
            for line in out:
                await ctx.send(line)
            return

        now = int(time.time())
        last = int(self.bot.config.get('rainwave:ustats:last', 0))
        wait = int(self.bot.config.get('rainwave:ustats:wait', 0))
        if last < now - wait:
            for line in out:
                await ctx.send(line)
            self.bot.config.set('rainwave:ustats:last', now)
        else:
            for line in out:
                await ctx.author.send(line)
            remaining = last + wait - now
            cmd = ctx.invoked_with
            m = f'I am cooling down. You cannot use **{cmd}** in {ctx.channel.mention} for another {remaining} seconds.'
            await ctx.author.send(m)

    @cmds.command(aliases=['vt'])
    async def vote(self, ctx: cmds.Context, candidate: int):
        """Vote in the current election.

        Use "!vote <candidate>" to vote in the current election.
        Find the <candidate> (usually a number from 1 to 3) with "!next"."""

        auth = await self.get_api_auth_for_user(ctx.author)
        if auth.get('user_id') is None:
            await ctx.author.send(self.nick_not_recognized)
            return
        if auth.get('key') is None:
            await ctx.author.send(self.missing_key)
            return
        if auth.get('chan') is None:
            await ctx.author.send('You must be tuned in to vote.')
            return

        chan = auth.get('chan')
        d = await self.rw_info(chan.channel_id)
        event = d.get('sched_next')[0]
        sched_type = event.get('type')

        if sched_type == 'OneUp':
            await ctx.author.send('You cannot vote during a Power Hour.')
            return

        if candidate < 1 or candidate > len(event.get('songs')):
            await ctx.author.send(f'{candidate} is not a valid voting option')
            return

        song = event.get('songs')[candidate - 1]
        elec_entry_id = song.get('entry_id')
        user_id = auth.get('user_id')
        key = auth.get('key')
        d = await self.rw_vote(user_id, key, chan.channel_id, elec_entry_id)
        if d.get('vote_result').get('success'):
            song_string = self.song_string(song, simple=True)
            await ctx.author.send(f'You successfully voted for {song_string}')
        else:
            await ctx.author.send('Your attempt to vote was not successful.')


def setup(bot: Wormgas):
    bot.add_cog(RainwaveCog(bot))
