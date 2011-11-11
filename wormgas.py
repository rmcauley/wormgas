#!/usr/bin/python
"""
wormgas -- IRC bot for Rainwave (http://rainwave.cc)
https://github.com/subtlecoolness/wormgas
"""

import gzip
import httplib
import json
import math
import os
import random
import re
import StringIO
import sys
import time
import urllib, urllib2

import dbaccess
from ircbot import SingleServerIRCBot

_abspath = os.path.abspath(__file__)

PRIVMSG = "__privmsg__"

class Output(object):
    """Dead-simple abstraction for splitting output between public and private.

    When created, specify the default output, either public or private. This
    way command handlers don't have to care about the output mode unless they
    need to.
    """
    def __init__(self, default):
        """Create an Output object.

        Args:
            default: string, either "public" or "private".
        """
        self.rs = []
        self.privrs = []
        if default == "public":
            self._default = self.rs
        elif default == "private":
            self._default = self.privrs
        else:
            raise ValueError("default should be 'public' or 'private'")

    @property
    def default(self):
        """The default output list."""
        return self._default

_commands = set()

def command_handler(command):
    """Decorate a method to register as a command handler for provided regex."""
    def decorator(func):
        # Compile the command into a regex.
        regex = re.compile(command)

        def wrapped(self, nick, msg, channel, output):
            """Command with stored regex that will execute if it matches msg."""
            # If the regex does not match the message, return False.
            result = regex.search(msg)
            if not result:
                return False

            # The msg matches pattern for this command, so run it.
            return func(self, nick, channel, output, **result.groupdict())
        # Add the wrapped function to a set, so we can iterate over them later.
        _commands.add(wrapped)

        # Return the original, undecorated method. It can still be called
        # directly without worrying about regexes. This also allows registering
        # one method as a handler for multiple input regexes.
        return func
    return decorator

class wormgas(SingleServerIRCBot):
    answers_8ball = [
            "As I see it, yes.",
            "Ask again later.",
            "Better not tell you now.",
            "Cannot predict now.",
            "Concentrate and ask again.",
            "Don't count on it.",
            "It is certain.",
            "It is decidedly so.",
            "Most likely.",
            "My reply is no.",
            "My sources say no.",
            "Outlook good.",
            "Outlook not so good.",
            "Reply hazy, try again.",
            "Signs point to yes.",
            "Very doubtful.",
            "Without a doubt.",
            "Yes.",
            "Yes - definitely.",
            "You may rely on it."]


    station_names = ("Rainwave Network", "Game channel",  "OCR channel",
        "Covers channel", "Chiptune channel", "All channel")

    station_ids = {"game": 1, "ocr": 2, "cover": 3, "chip": 4, "all": 5}

    def __init__(self):
        (self.path, self.file) = os.path.split(_abspath)

        self.config = dbaccess.Config()
        self.config.open(self.path)

        try:
            self.rwdb = dbaccess.RainwaveDatabase(self.config)
            self.rwdb.connect()
        except dbaccess.RainwaveDatabaseUnavailableError:
            self.rwdb = None

        server = self.config.get("irc:server")
        nick = self.config.get("irc:nick")
        name = self.config.get("irc:name")
        SingleServerIRCBot.__init__(self, [(server, 6667)], nick, name)

    @command_handler("!8ball")
    def handle_8ball(self, nick, channel, output):
        """Ask a question of the magic 8ball."""
        result = random.choice(self.answers_8ball)
        # Private messages always get the result.
        if channel == PRIVMSG:
            output.default.append(result)
            return True

        # Otherwise, check for the cooldown and respond accordingly.
        ltb = int(self.config.get("lasttime:8ball"))
        wb = int(self.config.get("wait:8ball"))
        if ltb < time.time() - wb:
            output.default.append(result)
            if "again" not in rs[0]:
                self.config.set("lasttime:8ball", time.time())
        else:
            output.privrs.append(result)
            wait = ltb + wb - int(time.time())
            cdmsg = ("I am cooling down. You cannot use !8ball in "
                    "%s for another %s seconds." % (channel, wait))
            output.privrs.append(cdmsg)
        return True

    @command_handler(r"^!el(?P<station>\w+)(\s(?P<index>\d))?")
    @command_handler(r"^!election\s(?P<station>\w+)(\s(?P<index>\d))?")
    def handle_election(self, nick, channel, output, station=None, index=None):
        """Show the candidates in an election

        Returns: a list of sched_id, response strings
        """
        # Make sure the index is valid.
        try:
            index = int(index)
        except TypeError:
            index = 0
        if index not in [0, 1]:
            # Not a valid index, return the help text.
            output.privrs.extend(self.handle_help(nick, channel, output,
                topic="election"))
            return True

        if station in self.station_ids:
            sid = self.station_ids.get(station, 5)
        else:
            return(self.handle_help(nick, channel, output, topic="election"))

        sched_config = "el:%s:%s" % (sid, index)
        sched_id, text = self._fetch_election(index, sid)

        # Prepend the message description to the output string.
        time = ["Current", "Future"][index]
        result = "%s election on %s: %s" % (time, self.station_names[sid], text)

        if channel == PRIVMSG:
            output.privrs.append(result)
        elif sched_id == self.config.get(sched_config):
            # !election has already been called for this election
            output.privrs.append(result)
            output.privrs.append(
                    "I am cooling down. You can only use "
                    "!election in %s once per election." % channel)
        else:
            output.default.append(result)
            self.config.set(sched_config, sched_id)
        return True

    def _fetch_election(self, index, sid):
        """Return (sched_id, election string) for given index and sid.

        A sched_id is a unique ID given to every scheduled event, including
        elections. The results of this call can therefore be cached locally
        using the sched_id as the cache key.
        """
        data = self.api_call("http://rainwave.cc/async/%s/get" % sid)
        elec = data["sched_next"][index]
        text = ""

        for i, song in enumerate(elec["song_data"], start=1):
            album = song["album_name"]
            title = song["song_title"]
            artt = ", ".join(art["artist_name"] for art in song["artists"])
            text += " \x02[%s]\x02 %s / %s by %s" % (i, album, title, artt)
            etype = song["elec_isrequest"]
            if etype in (3, 4):
                requestor = song["song_requestor"]
                text += " (requested by %s)" % requestor
            elif etype in (0, 1):
                text += " (conflict)"
        return elec["sched_id"], text

    @command_handler("!flip")
    def handle_flip(self, nick, channel, output):
        """Simulate a coin flip"""

        answers = ("Heads!", "Tails!")
        result = random.choice(answers)
        if channel == PRIVMSG:
            output.default.append(result)
            return True

        ltf = int(self.config.get("lasttime:flip"))
        wf = int(self.config.get("wait:flip"))
        if ltf < time.time() - wf:
            output.default.append(result)
            self.config.set("lasttime:flip", time.time())
        else:
            output.privrs.append(result)
            wait = ltf + wf - int(time.time())
            cdmsg = ("I am cooling down. You cannot use !flip in %s for "
                "another %s seconds." % (channel, wait))
            output.privrs.append(cdmsg)
        return True

    @command_handler(r"^!help(\s(?P<topic>\w+))?")
    def handle_help(self, nick, channel, output, topic=None):
        """Look up help about a topic"""

        priv = self.config.get("privlevel:%s" % nick)
        rs = []

        if (not topic) or (topic == "all"):
            rs.append("Use \x02!help [<topic>]\x02 with one of these topics: "
                "8ball, election, flip, id, key, lookup, lstats, nowplaying, "
                "prevplayed, rate")
            if priv > 0:
                rs.append("Level 1 administration topics: (none)")
            if priv > 1:
                rs.append("Level 2 administration topics: config, stop")
        elif topic == "8ball":
            rs.append("Use \x02!8ball\x02 to ask a question of the magic 8ball")
        elif topic == "config":
            if priv > 1:
                rs.append("Use \x02!config [<id>] [<value>]\x02 to display or "
                    "change configuration settings")
                rs.append("Leave off <value> to see the current setting, or "
                    "use a <value> of -1 to remove a setting")
                rs.append("Leave off <id> and <value> to see a list of all "
                    "available config ids")
            else:
                rs.append("You are not permitted to use this command")
        elif topic == "election":
            rs.append("Use \x02!election <stationcode> [<election index>]\x02 "
                "to see the candidates in an election")
            rs.append("Short version is \x02!el<stationcode> [<index>]\x02")
            rs.append("Index should be 0 (current) or 1 (future), default is 0")
            rs.append("Station codes are \x02game\x02, \x02ocr\x02, "
                "\x02cover\x02, \x02chip\x02, or \x02all\x02")
        elif topic == "flip":
            rs.append("Use \x02!flip\x02 to flip a coin")
        elif topic == "id":
            rs.append("Look up your Rainwave user id at "
                "http://rainwave.cc/auth/ and use \x02!id add <id>\x02 to tell "
                "me about it")
            rs.append("Use \x02!id drop\x02 to delete your user id and \x02!id "
                "show\x02 to see it")
        elif topic == "key":
            rs.append("Get an API key from http://rainwave.cc/auth/ and use "
                "\x02!key add <key>\x02 to tell me about it")
            rs.append("Use \x02!key drop\x02 to delete your key and \x02!key "
                "show\x02 to see it")
        elif topic == "lookup":
            rs.append("Use \x02!lookup <stationcode> song|album <text>\x02 "
                "to search for songs or albums with <text> in the title")
            rs.append("Short version is \x02!lu<stationcode> song|album "
                "<text>\x02")
            rs.append("Station codes are \x02game\x02, \x02ocr\x02, "
                "\x02cover\x02, \x02chip\x02, or \x02all\x02")
        elif topic == "lstats":
            rs.append("Use \x02!lstats [<stationcode>]\x02 to see information "
                "about current listeners, all stations are aggregated if you "
                "leave off <stationcode>")
            rs.append("Use \x02!lstats chart [<num>]\x02 to see a chart of "
                "average hourly listener activity over the last <num> days, "
                "leave off <num> to use the default of 30")
            rs.append("Station codes are \x02game\x02, \x02ocr\x02, "
                "\x02cover\x02, \x02chip\x02, or \x02all\x02")
        elif topic == "nowplaying":
            rs.append("Use \x02!nowplaying <stationcode>\x02 to show what is "
                "now playing on the radio")
            rs.append("Short version is \x02!np<stationcode>\x02")
            rs.append("Station codes are \x02game\x02, \x02ocr\x02, "
                "\x02cover\x02, \x02chip\x02, or \x02all\x02")
        elif topic == "prevplayed":
            rs.append("Use \x02!prevplayed <stationcode> [<index>]\x02 to show "
                "what was previously playing on the radio")
            rs.append("Short version is \x02!pp<stationcode> [<index>]\x02")
            rs.append("Index should be one of (0, 1, 2), 0 is default, higher "
                "numbers are further in the past")
            rs.append("Station codes are \x02game\x02, \x02ocr\x02, "
                "\x02cover\x02, \x02chip\x02, or \x02all\x02")
        elif topic == "rate":
            rs.append("Use \x02!rate <stationcode> <rating>\x02 to rate the "
                "currently playing song")
            rs.append("Short version is \x02!rt<stationcode> <rating>\x02")
            rs.append("Station codes are \x02game\x02, \x02ocr\x02, "
                "\x02cover\x02, \x02chip\x02, or \x02all\x02")
        elif topic == "stop":
            if priv > 1:
                rs.append("Use \x02!stop\x02 to shut down the bot")
            else:
                rs.append("You are not permitted to use this command")
        else:
            rs.append("I cannot help you with '%s'" % topic)

        output.privrs.extend(rs)
        return True

    @command_handler(r"^!id(\s(?P<mode>\w+))?(\s(?P<id>\d+))?")
    def handle_id(self, nick, channel, output, mode=None, id=None):
        """Manage correlation between an IRC nick and Rainwave User ID

        Arguments:
            mode: string, one of "help", "add", "drop", "show"
            id: numeric, the person's Rainwave User ID"""

        # Make sure this nick is in the user_keys table
        self.config.store_nick(nick)

        if mode == "add" and id:
            self.config.add_id_to_nick(id, nick)
            output.privrs.append("I assigned the user id %s to nick '%s'" %
                (id, nick))
        elif mode == "drop":
            self.config.drop_id_for_nick(nick)
            output.privrs.append("I dropped the user id for nick '%s'" % nick)
        elif mode == "show":
            stored_id = self.config.get_id_for_nick(nick)
            if stored_id:
                output.privrs.append("The user id for nick '%s' is %s" %
                    (nick, stored_id))
            else:
                output.privrs.append("I do not have a user id for nick '%s'" %
                    nick)
        else:
            return(self.handle_help(nick, channel, output, topic="id"))

        return True

    def handle_key(self, nick, mode="help", key=None):
        """Manage API keys

        Arguments:
            nick: string, IRC nick of person to manage key for
            mode: string, one of "help", "add", "drop", "show"
            key: string, the API key to add

        Returns: a list of strings"""

        rs = []

        # Make sure this nick is in the user_keys table
        self.config.store_nick(nick)

        if mode == "help":
            priv = self.config.get("privlevel:%s" % nick)
            rs = self.handle_help(priv, "key")
        elif mode == "add":
            self.config.add_key_to_nick(key, nick)
            rs.append("I assigned the API key '%s' to nick '%s'" % (key, nick))
        elif mode == "drop":
            self.config.drop_key_for_nick(nick)
            rs.append("I dropped the API key for nick '%s'" % nick)
        elif mode == "show":
            stored_id = self.config.get_key_for_nick(nick)
            if stored_id:
                rs.append("The API key for nick '%s' is '%s'" %
                    (nick, stored_id))
            else:
                rs.append("I do not have an API key for nick '%s'" % nick)

        return(rs)

    @command_handler(r"^!lookup\s(?P<station>\w+)\s(?P<mode>\w+)\s(?P<text>\w+)")
    @command_handler(r"^!lu(?P<station>\w+)\s(?P<mode>\w+)\s(?P<text>\w+)")
    def handle_lookup(self, nick, channel, output, station, mode, text):
        """Look up (search for) a song or album"""

        if not self.rwdb:
            return ["Cannot access Rainwave database. Sorry."]

        if station in self.station_ids:
            sid = self.station_ids.get(station, 5)
        else:
            return(self.handle_help(nick, channel, output, topic="lookup"))
        st = self.station_names[sid]

        if mode == "song":
            rows, unreported_results = self.rwdb.search_songs(sid, text)
            out = "%(station)s: %(album_name)s / %(song_title)s [%(song_id)s]"
        elif mode == "album":
            rows, unreported_results = self.rwdb.search_albums(sid, text)
            out = "%(station)s: %(album_name)s [%(album_id)s]"
        else:
            return(self.handle_help(nick, channel, output, topic="lookup"))

        # If I got results, output them

        for row in rows:
            row["station"] = st
            output.privrs.append(out % row)

        # If I had to trim the results, be honest about it

        if unreported_results > 0:
            r = "%s: %s more result" % (st, unreported_results)
            if unreported_results > 1:
                r = "%ss" % r
            r = ("%s. If you do not see what you are looking for, be more "
                "specific with your search." % r)
            output.privrs.append(r)

        # If I did not find anything with this search, mention that

        if len(output.privrs) < 1:
            r = "%s: No results." % st
            output.privrs.append(r)
        elif unreported_results < 1:

            # I got between 1 and 10 results

            r = ("%s: Your search returned %s results." % (st, len(rs)))
            output.privrs.insert(0, r)

        return True

    def handle_lstats(self, sid, mode="text", days=30):
        """ Reports listener statistics, as numbers or a chart

        Arguments:
            sid: station id of station to ask about
            mode: "text" or "chart"
            days: number of days to include data for chart

        Returns: A list of strings"""
        if not self.rwdb:
            return ["Cannot access Rainwave database. Sorry."]

        rs = []
        st = self.station_names[sid]

        if mode == "text":
            regd, guest = self.rwdb.get_listener_stats(sid)
            rs.append("%s: %s registered users, %s guests." % (st, regd, guest))
        elif mode == "chart":

            # Base url
            url = "http://chart.apis.google.com/chart"

            # Axis label styles
            url = "".join((url, "?chxs=0,676767,11.5,-1,l,676767"))

            # Visible axes
            url = "".join((url, "&chxt=y,x"))

            # Bar width and spacing
            url = "".join((url, "&chbh=a"))

            # Chart size
            url = "".join((url, "&chs=600x400"))

            # Chart type
            url = "".join((url, "&cht=bvs"))

            # Series colors
            c1 = "&chco=A2C180,3D7930,3399CC,244B95,FFCC33,"
            c2 = "FF9900,cc80ff,66407f,900000,480000"
            url = "".join((url, c1, c2))

            # Chart legend text
            t1 = "&chdl=Rainwave+Guests|Rainwave+Registered|OCR+Radio+Guests|"
            t2 = "OCR+Radio+Registered|Mixwave+Guests|Mixwave+Registered|"
            t3 = "Bitwave+Guests|Bitwave+Registered|Omniwave+Guests|"
            t4 = "Omniwave+Registered"
            url = "".join((url, t1, t2, t3, t4))

            # Chart title
            t1 = "&chtt=Rainwave+Average+Hourly+Usage+by+User+Type+and+Station|"
            t2 = "%s+Day" % days
            if days > 1:
                t2 = "".join((t2, "s"))
            t3 = "+Ending+"
            t4 = time.strftime("%Y-%m-%d", time.gmtime())
            url = "".join((url, t1, t2, t3, t4))

            rwg = []
            rwr = []
            ocg = []
            ocr = []
            mwg = []
            mwr = []
            bwg = []
            bwr = []
            owg = []
            owr = []
            for sid, guests, users in self.rwdb.get_listener_chart_data():
                if sid == 1:
                    rwg.append(guests)
                    rwr.append(users)
                elif sid == 2:
                    ocg.append(guests)
                    ocr.append(users)
                elif sid == 3:
                    mwg.append(guests)
                    mwr.append(users)
                elif sid == 4:
                    bwg.append(guests)
                    bwr.append(users)
                elif sid == 5:
                    owg.append(guests)
                    owr.append(users)

            lmax = sum((max(rwg), max(rwr), max(ocg), max(ocr), max(mwg),
                max(mwr), max(bwg), max(bwr), max(owg), max(owr)))
            lceil = math.ceil(lmax / 50) * 50

            # Chart data
            url = "".join((url, "&chd=t:"))
            url = "".join((url, ",".join(["%s" % el for el in rwg]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in rwr]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in ocg]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in ocr]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in mwg]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in mwr]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in bwg]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in bwr]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in owg]), "|"))
            url = "".join((url, ",".join(["%s" % el for el in owr])))

            # Axis ranges
            t1 = "&chxr=0,0,%s|1,0,23" % lceil
            url = "".join((url, t1))

            # Scale for text format with custom range
            url = "".join((url, "&chds="))
            t1 = "0,%s" % lceil
            t2 = []
            for i in range(10):
                t2.append(t1)
            t3 = ",".join(t2)
            url = "".join((url, t3))
            rs.append(self.shorten(url))
        else:
            rs = self.handle_help(topic="lstats")

        return(rs)

    @command_handler(r"!nowplaying\s(?P<station>\w+)")
    @command_handler(r"!np(?P<station>\w+)")
    def handle_nowplaying(self, nick, channel, output, station=None):
        """Report what is currently playing on the radio"""

        rs = []
        if station in self.station_ids:
            sid = self.station_ids[station]
        else:
            sid = 5
        st = self.station_names[sid]

        url = "http://rainwave.cc/async/%s/get" % sid
        data = self.api_call(url)
        sched_id = data["sched_current"]["sched_id"]
        sched_type = data["sched_current"]["sched_type"]
        if sched_type in (0, 4):
            np = data["sched_current"]["song_data"][0]
            album = np["album_name"]
            song = np["song_title"]
            arts = np["artists"]
            art_list = []
            for art in arts:
                art_name = art["artist_name"]
                art_list.append(art_name)
            artt = ", ".join(art_list)
            r = "%s: Now playing: %s / %s by %s" % (st, album, song, artt)
            url = np["song_url"]
            if url and "http" in url:
                r = "%s <%s>" % (r, self.shorten(url))

            votes = np["elec_votes"]
            ratings = np["song_rating_count"]
            avg = np["song_rating_avg"]

            r = "%s (%s vote" % (r, votes)
            if votes <> 1:
                r = "%ss" % r
            r = "%s, %s rating" % (r, ratings)
            if ratings <> 1:
                r = "%ss" % r
            r = "%s, rated %s" % (r, avg)

            type = np["elec_isrequest"]
            if type in (3, 4):
                r = "%s, requested by %s" % (r, np["song_requestor"])
            elif type in (0, 1):
                r = "%s, conflict" % r
            r = "%s)" % r
            rs.append(r)
        else:
            r = "%s: I have no idea (sched_type = %s)" % (st, sched_type)
            rs.append(r)

        if channel == PRIVMSG:
            output.default.extend(rs)
            return True

        if sched_id == int(self.config.get("np:%s" % sid)):
            output.privrs.extend(rs)
            output.privrs.append("I am cooling down. You can only use "
                "!nowplaying in %s once per song." % channel)
        else:
            output.default.extend(rs)
            self.config.set("np:%s" % sid, sched_id)

        return True

    def handle_prevplayed(self, sid, index=0):
        """Report what was previously playing on the radio

        Arguments:
            sid: (int) station id of station to check
            index: (int) (0, 1, 2) which previously played song, higher number =
                further in the past

        Returns: a list of sched_id, strings"""

        rs = []
        st = self.station_names[sid]

        url = "http://rainwave.cc/async/%s/get" % sid
        data = self.api_call(url)
        sched_id = data["sched_history"][index]["sched_id"]
        rs.append(sched_id)
        sched_type = data["sched_history"][index]["sched_type"]
        if sched_type in (0, 4):
            pp = data["sched_history"][index]["song_data"][0]
            album = pp["album_name"]
            song = pp["song_title"]
            arts = pp["artists"]
            art_list = []
            for art in arts:
                art_name = art["artist_name"]
                art_list.append(art_name)
            artt = ", ".join(art_list)
            r = "%s: Previously: %s / %s by %s" % (st, album, song, artt)

            votes = pp["elec_votes"]
            avg = pp["song_rating_avg"]

            r = "%s (%s vote" % (r, votes)
            if votes <> 1:
                r = "%ss" % r
            r = "%s, rated %s" % (r, avg)

            type = pp["elec_isrequest"]
            if type in (3, 4):
                r = "%s, requested by %s" % (r, pp["song_requestor"])
            elif type in (0, 1):
                r = "%s, conflict" % r
            r = "%s)" % r
            rs.append(r)
        else:
            r = "%s: I have no idea (sched_type = %s)" % (st, sched_type)
            rs.append(r)

        return(rs)

    def handle_rate(self, nick, sid, rating):
        """Rate the currently playing song

        Arguments:
            nick: person who is submitting the rating
            sid: station id of song to rate
            rating: the rating

        Returns: a list of strings"""

        rs = []

        # Make sure this nick matches a username

        user_id = self.config.get_id_for_nick(nick)
        if not user_id:
            if self.rwdb:
                if self.rwdb.validate_nick(nick):
                    user_id = nick
                else:
                    rs.append("I cannot find an account for '%s'. "
                        "Is the username correct?" % nick)
                    return(rs)
            else:
                rs.append("I'll try to rate with account '%s'. If this is not "
                        "your Rainwave account, tell me what account to use "
                        "with \x02!id add [account]\x02" % nick)
                user_id = nick

        # Get the key for this user

        key = self.config.get_key_for_nick(nick)
        if not key:
            r = ("I do not have a key store for you. Visit "
                "http://rainwave.cc/auth/ to get a key and tell me about it "
                "with \x02!key add [key]\x02")
            rs.append(r)
            return(rs)

        # Get the song_id

        url = "http://rainwave.cc/async/%s/get" % sid
        data = self.api_call(url)
        song_id = data["sched_current"]["song_data"][0]["song_id"]

        # Try the rate

        url = "http://rainwave.cc/async/%s/rate" % sid
        args = {"user_id": user_id, "key": key, "song_id": song_id,
            "rating": rating}
        data = self.api_call(url, args)

        if data["rate_result"]:
            rs.append(data["rate_result"]["text"])
        else:
            rs.append(data["error"]["text"])

        return(rs)

    def on_privmsg(self, c, e):
        """This method is called when a message is sent directly to the bot

        Arguments:
            c: the Connection object associated with this event
            e: the Event object"""
        nick = e.source().split("!")[0]
        priv = self.config.get("privlevel:%s" % nick)
        msg = e.arguments()[0].strip()

        cmdtokens = msg.split()
        try:
            cmd = cmdtokens[0]
        except IndexError:
            cmd = None

        rs = []

        # Try all the new command handlers first.
        output = Output("private")
        for command in _commands:
            if command(self, nick, msg, PRIVMSG, output):
                rs = output.privrs
                break

        # !config

        if not rs and priv > 1 and cmd == "!config":
            try:
                id = cmdtokens[1]
            except IndexError:
                id = None
            try:
                value = cmdtokens[2]
            except IndexError:
                value = None
            rs = self.config.handle(id, value)

        # !key

        elif cmd == "!key":
            try:
                mode = cmdtokens[1]
            except IndexError:
                mode = "help"
            try:
                key = cmdtokens[2]
            except IndexError:
                key = None
            rs = self.handle_key(nick, mode, key)

        # !lstats

        elif cmd == "!lstats":
            if len(cmdtokens) > 1:
                mode = cmdtokens[1]
                if mode == "chart":
                    if len(cmdtokens) > 2:
                        try:
                            days = int(cmdtokens[2])
                        except ValueError:
                            days = 30
                    else:
                        days = 30
                    if days < 1:
                        days = 30
                    rs = self.handle_lstats(0, "chart", days)
                elif mode in self.station_ids:
                    sid = self.station_ids[mode]
                    rs = self.handle_lstats(sid)
                else:
                    rs = self.handle_lstats(0)
            else:
                rs = self.handle_lstats(0)

        # !ppbw

        elif cmd == "!ppchip":
            sid = 4
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                    if index in (0, 1, 2):
                        rs = self.handle_prevplayed(sid, index)
                        rs.pop(0)
                    else:
                        rs = self.handle_help(topic="prevplayed")
                except ValueError:
                    rs = self.handle_prevplayed(sid)
                    rs.pop(0)
            else:
                rs = self.handle_prevplayed(sid)
                rs.pop(0)

        # !ppmw

        elif cmd == "!ppcover":
            sid = 3
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                    if index in (0, 1, 2):
                        rs = self.handle_prevplayed(sid, index)
                        rs.pop(0)
                    else:
                        rs = self.handle_help(topic="prevplayed")
                except ValueError:
                    rs = self.handle_prevplayed(sid)
                    rs.pop(0)
            else:
                rs = self.handle_prevplayed(sid)
                rs.pop(0)

        # !ppoc

        elif cmd == "!ppocr":
            sid = 2
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                    if index in (0, 1, 2):
                        rs = self.handle_prevplayed(sid, index)
                        rs.pop(0)
                    else:
                        rs = self.handle_help(topic="prevplayed")
                except ValueError:
                    rs = self.handle_prevplayed(sid)
                    rs.pop(0)
            else:
                rs = self.handle_prevplayed(sid)
                rs.pop(0)

        # !ppow

        elif cmd == "!ppall":
            sid = 5
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                    if index in (0, 1, 2):
                        rs = self.handle_prevplayed(sid, index)
                        rs.pop(0)
                    else:
                        rs = self.handle_help(topic="prevplayed")
                except ValueError:
                    rs = self.handle_prevplayed(sid)
                    rs.pop(0)
            else:
                rs = self.handle_prevplayed(sid)
                rs.pop(0)

        # !pprw

        elif cmd == "!ppgame":
            sid = 1
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                    if index in (0, 1, 2):
                        rs = self.handle_prevplayed(sid, index)
                        rs.pop(0)
                    else:
                        rs = self.handle_help(topic="prevplayed")
                except ValueError:
                    rs = self.handle_prevplayed(sid)
                    rs.pop(0)
            else:
                rs = self.handle_prevplayed(sid)
                rs.pop(0)

        # !prevplayed

        elif cmd == "!prevplayed":
            if len(cmdtokens) > 1:
                station = cmdtokens[1]
                if station in self.station_ids:
                    sid = self.station_ids[station]
                    if len(cmdtokens) > 2:
                        try:
                            index = int(cmdtokens[2])
                            if index in (0, 1, 2):
                                rs = self.handle_prevplayed(sid, index)
                                rs.pop(0)
                            else:
                                rs = self.handle_help(topic="prevplayed")
                        except ValueError:
                            rs = self.handle_prevplayed(sid)
                            rs.pop(0)
                    else:
                        rs = self.handle_prevplayed(sid)
                        rs.pop(0)
                else:
                    rs = self.handle_help(topic="prevplayed")
            else:
                rs = self.handle_help(topic="prevplayed")

        # !rate

        elif cmd == "!rate":
            if len(cmdtokens) > 1:
                station = cmdtokens[1]
                if station in self.station_ids:
                    sid = self.station_ids[station]
                    if len(cmdtokens) > 2:
                        rating = cmdtokens[2]
                        rs = self.handle_rate(nick, sid, rating)
                    else:
                        rs = self.handle_help(topic="rate")
                else:
                    rs = self.handle_help(topic="rate")
            else:
                rs = self.handle_help(topic="rate")

        # !rtchip

        elif cmd == "!rtchip":
            sid = 4
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                rs = self.handle_rate(nick, sid, rating)
            else:
                rs = self.handle_help(topic="rate")

        # !rtcover

        elif cmd == "!rtcover":
            sid = 3
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                rs = self.handle_rate(nick, sid, rating)
            else:
                rs = self.handle_help(topic="rate")

        # !rtocr

        elif cmd == "!rtocr":
            sid = 2
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                rs = self.handle_rate(nick, sid, rating)
            else:
                rs = self.handle_help(topic="rate")

        # !rtall

        elif cmd == "!rtall":
            sid = 5
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                rs = self.handle_rate(nick, sid, rating)
            else:
                rs = self.handle_help(topic="rate")

        # !rtgame

        elif cmd == "!rtgame":
            sid = 1
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                rs = self.handle_rate(nick, sid, rating)
            else:
                rs = self.handle_help(topic="rate")

        # !stop

        elif priv > 1 and "!stop" in msg:
            self.die()

        # Send responses

        for r in rs:
            if type(r) is unicode:
                message = r.encode("utf-8")
            else:
                message = unicode(r, "utf-8").encode("utf-8")
            c.privmsg(nick, message)

    def on_pubmsg(self, c, e):
        """This method is called when a message is sent to the channel the bot
        is on

        Arguments:
            c: the Connection object associated with this event
            e: the Event object"""

        nick = e.source().split("!")[0]
        priv = self.config.get("privlevel:%s" % nick)
        chan = e.target()
        msg = e.arguments()[0].strip()
        cmdtokens = msg.split()
        try:
            cmd = cmdtokens[0]
        except IndexError:
            cmd = None

        rs = []
        privrs = []

        # Try all the new command handlers first.
        output = Output("public")
        for command in _commands:
            if command(self, nick, msg, chan, output):
                rs = output.rs
                privrs = output.privrs
                break

        # !config

        if not rs and priv > 1 and cmd == "!config":
            try:
                id = cmdtokens[1]
            except IndexError:
                id = None
            try:
                value = cmdtokens[2]
            except IndexError:
                value = None
            privrs = self.config.handle(id, value)

        # !key

        elif cmd == "!key":
            try:
                mode = cmdtokens[1]
            except IndexError:
                mode = "help"
            try:
                key = cmdtokens[2]
            except IndexError:
                key = None
            privrs = self.handle_key(nick, mode, key)

        # !lstats

        elif cmd == "!lstats":
            if len(cmdtokens) > 1:
                mode = cmdtokens[1]
                if mode == "chart":
                    if len(cmdtokens) > 2:
                        try:
                            days = int(cmdtokens[2])
                        except ValueError:
                            days = 30
                    else:
                        days = 30
                    if days < 1:
                        days = 30
                    rs = self.handle_lstats(0, "chart", days)
                elif mode in self.station_ids:
                    sid = self.station_ids[mode]
                    rs = self.handle_lstats(sid)
                else:
                    rs = self.handle_lstats(0)
            else:
                rs = self.handle_lstats(0)

            ltls = int(self.config.get("lasttime:lstats"))
            wls = int(self.config.get("wait:lstats"))
            if ltls < time.time() - wls:
                self.config.set("lasttime:lstats", time.time())
            else:
                if rs:
                    privrs = rs
                    rs = []
                    wait = ltls + wls - int(time.time())
                    privrs.append("I am cooling down. You cannot use !lstats "
                        "in %s for another %s seconds." % (chan, wait))

        # !ppbw

        elif cmd == "!ppbw":
            sid = 4
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                except ValueError:
                    index = 0
            else:
                index = 0
            if index in (0, 1, 2):
                rs = self.handle_prevplayed(sid, index)
                sched_id = rs.pop(0)
                last = int(self.config.get("pp:%s:%s" % (sid, index)))
                if sched_id == last:
                    privrs = rs
                    rs = []
                    privrs.append("I am cooling down. You can only use "
                        "!prevplayed in %s once per song." % chan)
                else:
                    self.config.set("pp:%s:%s" % (sid, index), sched_id)
            else:
                privrs = self.handle_help(topic="prevplayed")

        # !ppmw

        elif cmd == "!ppmw":
            sid = 3
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                except ValueError:
                    index = 0
            else:
                index = 0
            if index in (0, 1, 2):
                rs = self.handle_prevplayed(sid, index)
                sched_id = rs.pop(0)
                last = int(self.config.get("pp:%s:%s" % (sid, index)))
                if sched_id == last:
                    privrs = rs
                    rs = []
                    privrs.append("I am cooling down. You can only use "
                        "!prevplayed in %s once per song." % chan)
                else:
                    self.config.set("pp:%s:%s" % (sid, index), sched_id)
            else:
                privrs = self.handle_help(topic="prevplayed")

        # !ppoc

        elif cmd == "!ppoc":
            sid = 2
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                except ValueError:
                    index = 0
            else:
                index = 0
            if index in (0, 1, 2):
                rs = self.handle_prevplayed(sid, index)
                sched_id = rs.pop(0)
                last = int(self.config.get("pp:%s:%s" % (sid, index)))
                if sched_id == last:
                    privrs = rs
                    rs = []
                    privrs.append("I am cooling down. You can only use "
                        "!prevplayed in %s once per song." % chan)
                else:
                    self.config.set("pp:%s:%s" % (sid, index), sched_id)
            else:
                privrs = self.handle_help(topic="prevplayed")

        # !ppow

        elif cmd == "!ppow":
            sid = 5
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                except ValueError:
                    index = 0
            else:
                index = 0
            if index in (0, 1, 2):
                rs = self.handle_prevplayed(sid, index)
                sched_id = rs.pop(0)
                last = int(self.config.get("pp:%s:%s" % (sid, index)))
                if sched_id == last:
                    privrs = rs
                    rs = []
                    privrs.append("I am cooling down. You can only use "
                        "!prevplayed in %s once per song." % chan)
                else:
                    self.config.set("pp:%s:%s" % (sid, index), sched_id)
            else:
                privrs = self.handle_help(topic="prevplayed")

        # !pprw

        elif cmd == "!pprw":
            sid = 1
            if len(cmdtokens) > 1:
                try:
                    index = int(cmdtokens[1])
                except ValueError:
                    index = 0
            else:
                index = 0
            if index in (0, 1, 2):
                rs = self.handle_prevplayed(sid, index)
                sched_id = rs.pop(0)
                last = int(self.config.get("pp:%s:%s" % (sid, index)))
                if sched_id == last:
                    privrs = rs
                    rs = []
                    privrs.append("I am cooling down. You can only use "
                        "!prevplayed in %s once per song." % chan)
                else:
                    self.config.set("pp:%s:%s" % (sid, index), sched_id)
            else:
                privrs = self.handle_help(topic="prevplayed")

        # !prevplayed

        elif cmd == "!prevplayed":
            if len(cmdtokens) > 1:
                station = cmdtokens[1]
                if station in self.station_ids:
                    sid = self.station_ids[station]
                    if len(cmdtokens) > 2:
                        try:
                            index = int(cmdtokens[2])
                        except ValueError:
                            index = 0
                    else:
                        index = 0
                    if index in (0, 1, 2):
                        rs = self.handle_prevplayed(sid, index)
                        sched_id = rs.pop(0)
                        last = int(self.config.get("pp:%s:%s" % (sid, index)))
                        if sched_id == last:
                            privrs = rs
                            rs = []
                            privrs.append("I am cooling down. You can only use "
                                "!prevplayed in %s once per song." % chan)
                        else:
                            self.config.set("pp:%s:%s" % (sid, index), sched_id)
                    else:
                        privrs = self.handle_help(topic="prevplayed")
                else:
                    privrs = self.handle_help(topic="prevplayed")
            else:
                privrs = self.handle_help(topic="prevplayed")

        # !rate

        elif cmd == "!rate":
            if len(cmdtokens) > 1:
                station = cmdtokens[1]
                if station in self.station_ids:
                    sid = self.station_ids[station]
                    if len(cmdtokens) > 2:
                        rating = cmdtokens[2]
                        privrs = self.handle_rate(nick, sid, rating)
                    else:
                        privrs = self.handle_help(topic="rate")
                else:
                    privrs = self.handle_help(topic="rate")
            else:
                privrs = self.handle_help(topic="rate")

        # !rtbw

        elif cmd == "!rtbw":
            sid = 4
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                privrs = self.handle_rate(nick, sid, rating)
            else:
                privrs = self.handle_help(topic="rate")

        # !rtmw

        elif cmd == "!rtmw":
            sid = 3
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                privrs = self.handle_rate(nick, sid, rating)
            else:
                privrs = self.handle_help(topic="rate")

        # !rtoc

        elif cmd == "!rtoc":
            sid = 2
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                privrs = self.handle_rate(nick, sid, rating)
            else:
                privrs = self.handle_help(topic="rate")

        # !rtow

        elif cmd == "!rtow":
            sid = 5
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                privrs = self.handle_rate(nick, sid, rating)
            else:
                privrs = self.handle_help(topic="rate")

        # !rtrw

        elif cmd == "!rtrw":
            sid = 1
            if len(cmdtokens) > 1:
                rating = cmdtokens[1]
                privrs = self.handle_rate(nick, sid, rating)
            else:
                privrs = self.handle_help(topic="rate")

        # !stop

        elif priv > 1 and "!stop" in msg:
            self.die()

        # Send responses

        for r in rs:
            if type(r) is unicode:
                message = r.encode("utf-8")
            else:
                message = unicode(r, "utf-8").encode("utf-8")
            c.privmsg(chan, message)

        for privr in privrs:
            if type(privr) is unicode:
                message = privr.encode("utf-8")
            else:
                message = unicode(privr, "utf-8").encode("utf-8")
            c.privmsg(nick, message)

    def on_welcome(self, c, e):
        """This method is called when the bot first connects to the server

        Arguments:
            c: the Connection object associated with this event
            e: the Event object"""

        passwd = self.config.get("irc:nickservpass")
        c.privmsg("nickserv", "identify %s" % passwd)
        c.join(self.config.get("irc:channel"))

    def api_call(self, url, args=None):
        """Make a call to the Rainwave API

        Arguments:
            url: the url of the API call
            args: a dictionary of optional arguments for the API call

        Returns: the API response object"""

        request = urllib2.Request(url)
        request.add_header("user-agent",
            "wormgas/0.1 +http://github.com/subtlecoolness/wormgas")
        request.add_header("accept-encoding", "gzip")
        if args:
            request.add_data(urllib.urlencode(args))
        opener = urllib2.build_opener()
        gzipdata = opener.open(request).read()
        gzipstream = StringIO.StringIO(gzipdata)
        result = gzip.GzipFile(fileobj=gzipstream).read()
        data = json.loads(result)
        return(data)

    def shorten(self, lurl):
        """Shorten a URL

        Arguments:
            lurl: The long url

        Returns: a string, the short url"""

        body = json.dumps({"longUrl": lurl})
        headers = {}
        headers["content-type"] = "application/json"
        ua = "wormgas/0.1 +http://github.com/subtlecoolness/wormgas"
        headers["user-agent"] = ua

        h = httplib.HTTPSConnection("www.googleapis.com")
        gkey = self.config.get("googleapikey")
        gurl = "/urlshortener/v1/url?key=%s" % gkey
        h.request("POST", gurl, body, headers)
        content = h.getresponse().read()

        result = json.loads(content)
        return(result["id"])

def main():
    bot = wormgas()
    bot.start()

if __name__ == "__main__":
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except (OSError, AttributeError):
        pass # Non-Unix systems will run wormgas in the foreground.

    if len(sys.argv) > 1:
        sleeptime = float(sys.argv[1])
    else:
        sleeptime = 0
    time.sleep(sleeptime)

    main()
