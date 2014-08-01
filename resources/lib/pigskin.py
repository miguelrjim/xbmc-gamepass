"""
An XBMC plugin agnostic library for NFL Game Pass and Game Rewind support.
"""
import cookielib
import hashlib
import random
from traceback import format_exc
from uuid import getnode as get_mac
from urlparse import urlsplit

try:
    import requests
except ImportError: # XBMC calls v2 requests2... :-/
    import requests2 as requests
import xmltodict

class pigskin(object):
    def __init__(self, subscription, cookiefile, debug=False):
        self.subscription = subscription
        self.debug = debug
        self.non_seasonal_shows = {'Super Bowl Archives': '117'}
        self.seasonal_shows = {
            'NFL Gameday': {'2014': '212', '2013': '179', '2012': '146'},
            'Top 100 Players': {'2014': '217', '2013': '185', '2012': '153'}
        }

        if subscription == 'gamepass':
            self.base_url = 'https://gamepass.nfl.com/nflgp'
            self.servlets_url = 'http://gamepass.nfl.com/nflgp/servlets'
            self.seasonal_shows.update({
                'Playbook': {'2014': '213', '2013': '180', '2012': '147'},
                'NFL Total Access': {'2014': '214', '2013': '181', '2012': '148'},
                'NFL RedZone Archives': {'2014': '221', '2013': '182', '2012': '149'},
                'Sound FX': {'2014': '215', '2013': '183', '2012': '150'},
                'Coaches Show': {'2014': '216', '2013': '184', '2012': '151'},
                'A Football Life': {'2014': '218', '2013': '186', '2012': '154'},
                'NFL Films Presents': {'2014': '219', '2013': '187'},
                'Hard Knocks': {'2014': '220'}
            })
        elif subscription == 'gamerewind':
            self.base_url = 'https://gamerewind.nfl.com/nflgr'
            self.servlets_url = 'http://gamerewind.nfl.com/nflgr/servlets'
        else:
            raise ValueError('"%s" is not a supported subscription.' %subscription)

        self.http_session = requests.Session()
        self.cookie_jar = cookielib.LWPCookieJar(cookiefile)
        try:
            self.cookie_jar.load(ignore_discard=True, ignore_expires=True)
        except IOError:
            pass
        self.http_session.cookies = self.cookie_jar

    class LoginFailure(Exception):
        def __init__(self, value):
            self.value = value
        def __str__(self):
            return repr(self.value)

    def log(self, string):
        if self.debug:
            print '[pigskin]: %s' %string

    def check_for_subscription(self):
        """Return whether a subscription and user name are detected. Determines
        whether a login was successful."""
        url = self.servlets_url + '/simpleconsole'
        post_data = {'isFlex': 'true'}
        sc_data = self.make_request(url=url, method='post', payload=post_data)

        if '</userName>' not in sc_data:
            self.log('No user name detected.')
            return False
        elif '</subscription>' not in sc_data:
            self.log('No subscription detected.')
            return False
        else:
            self.log('Subscription and user name detected.')
            return True

    def gen_plid(self):
        """Return a "unique" MD5 hash. Getting the video path requires a plid,
        which looks like an and always changes. Reusing a plid does not work,
        so our guess is that it's a id for each instance of the player.
        """
        rand = random.getrandbits(10)
        mac_address = str(get_mac())
        md5 = hashlib.md5(str(rand) + mac_address)
        return md5.hexdigest()

    def get_manifest(self, video_path):
        """Return the XML manifest of a stream."""
        parsed_url = urlsplit(video_path)
        url = ('http://' + parsed_url.netloc + '/play' +
               '?url=' + parsed_url.path + '&' + parsed_url.query)
        manifest_data = self.make_request(url=url, method='get')
        return manifest_data

    def get_current_season_and_week(self):
        """Return the current season and week_code (e.g. 210) in a dict."""
        url = self.servlets_url + '/simpleconsole'
        post_data = {'isFlex': 'true'}
        sc_data = self.make_request(url=url, method='post', payload=post_data)

        sc_dict = xmltodict.parse(sc_data)['result']
        current_s_w = {sc_dict['currentSeason']: sc_dict['currentWeek']}
        return current_s_w

    def get_stream_manifest(self, vpath, vtype):
        """Return, as a dict, the manifest of a stream."""
        self.get_current_season_and_week() # set cookies
        video_path = self.get_video_path(vpath, vtype)
        xml_manifest = self.get_manifest(video_path)
        stream_manifest = self.parse_manifest(xml_manifest)
        return stream_manifest

    def get_live_url(self, game_id, bitrate):
        """Return the URL of a live stream."""
        self.get_current_season_and_week() # set cookies
        url = self.servlets_url + '/publishpoint'

        if game_id == 'nfl_network':
            post_data = {'id': '1', 'type': 'channel', 'nt': '1'}
        elif game_id == 'rz':
            post_data = {'id': '2', 'type': 'channel', 'nt': '1'}
        else:
            post_data = {'id': game_id, 'type': 'game', 'nt': '1', 'gt': 'live'}

        headers = {'User-Agent': 'Android'}
        m3u8_data = self.make_request(url=url, method='post', payload=post_data, headers=headers)
        m3u8_dict = xmltodict.parse(m3u8_data)['result']
        self.log('NFL Dict %s' %m3u8_dict)
        m3u8_url = m3u8_dict['path'].replace('adaptive://', 'http://')
        return m3u8_url.replace('androidtab', bitrate)

    def get_shows(self, season):
        """Return a list of all shows for a season."""
        seasons_shows = self.non_seasonal_shows.keys()
        for show_name, show_codes in self.seasonal_shows.items():
            if season in show_codes:
                seasons_shows.append(show_name)

        return sorted(seasons_shows)

    def get_shows_episodes(self, show_name, season=None):
        """Return a list of episodes for a show. Return empty list if none are
        found or if an error occurs.
        """
        url = self.servlets_url + '/browse'
        try:
            cid = self.seasonal_shows[show_name][season]
        except KeyError:
            try:
                cid = self.non_seasonal_shows[show_name]
            except KeyError:
                return []

        if show_name == 'NFL RedZone Archives':
            ps = 17
        else:
            ps = 50

        post_data = {
            'isFlex': 'true',
            'cid': cid,
            'pm': 0,
            'ps': ps,
            'pn': 1
        }

        archive_data = self.make_request(url=url, method='post', payload=post_data)
        archive_dict = xmltodict.parse(archive_data)['result']

        count = int(archive_dict['paging']['count'])
        if count >= 1:
            items = archive_dict['programs']['program']
            # if only one episode is returned, we explicitly put it into a list
            if isinstance(items, dict):
                items = [items]
            return items
        else:
            return []

    def get_seasons_and_weeks(self):
        """Return a multidimensional array of all seasons and weeks."""
        seasons_and_weeks = {}

        try:
            url = 'http://smb.cdnak.neulion.com/fs/nfl/nfl/mobile/weeks_v2.xml'
            s_w_data = self.make_request(url=url, method='get')
            s_w_data_dict = xmltodict.parse(s_w_data)
        except:
            self.log('Acquiring season and week data failed.')
            raise

        try:
            for season in s_w_data_dict['seasons']['season']:
                year = season['@season']
                season_dict = {}

                for week in season['week']:
                    if week['@section'] == "pre": # preseason
                        week_code = '1' + week['@value'].zfill(2)
                        season_dict[week_code] = week
                    else: # regular season and post season
                        week_code = '2' + week['@value'].zfill(2)
                        season_dict[week_code] = week

                seasons_and_weeks[year] = season_dict
        except KeyError:
            self.log('Parsing season and week data failed.')
            raise

        return seasons_and_weeks

    def get_video_path(self, vpath, vtype):
        """Return the "video path", which is the URL of stream's manifest."""
        url = self.servlets_url + '/encryptvideopath'
        plid = self.gen_plid()
        post_data = {
            'path': vpath,
            'plid': plid,
            'type': vtype,
            'isFlex': 'true'
        }
        video_path_data = self.make_request(url=url, method='post', payload=post_data)

        try:
            video_path_dict = xmltodict.parse(video_path_data)['result']
            self.log('Video Path Acquired Successfully.')
            return video_path_dict['path']
        except:
            self.log('Video Path Acquisition Failed.')
            return False

    def get_weeks_games(self, season, week_code):
        """Return a list of games for a week."""
        url = self.servlets_url + '/games'
        post_data = {
            'isFlex': 'true',
            'season': season,
            'week': week_code
        }

        game_data = self.make_request(url=url, method='post', payload=post_data)
        game_data_dict = xmltodict.parse(game_data)['result']
        games = game_data_dict['games']['game']
        # if only one game is returned, we explicitly put it into a list
        if isinstance(games, dict):
            games = [games]

        return games

    # Handles neccesary steps and checks to login to Game Pass/Rewind
    def login(self, username=None, password=None):
        """Complete login process for Game Pass/Rewind. Errors (auth issues,
        blackout, etc) are raised as LoginFailure.
        """
        if self.check_for_subscription():
            self.log('Already logged into %s' %self.subscription)
        else:
            if username and password:
                self.log('Not (yet) logged into %s' %self.subscription)
                self.login_to_account(username, password)
                if not self.check_for_subscription():
                    raise self.LoginFailure('%s login failed' %self.subscription)
                elif self.subscription == 'gamerewind' and self.service_blackout():
                    raise LoginFailure('Game Rewind Blackout')
            else:
                # might need sans-login check here for Game Pass, though as of
                # 2014, there /may/ no longer be any sans-login regions.
                self.log('No username and password supplied.')
                raise self.LoginFailure('No username and password supplied.')

    def login_to_account(self, username, password):
        """Blindly authenticate to Game Pass/Rewind. Use
        check_for_subscription() to determine success.
        """
        url = 'https://id.s.nfl.com/login'
        post_data = {
            'username': username,
            'password': password,
            'vendor_id': 'nflptnrnln',
            'error_url': self.base_url + '/secure/login?redirect=loginform&redirectnosub=packages&redirectsub=schedule',
            'success_url': self.base_url + '/secure/login?redirect=loginform&redirectnosub=packages&redirectsub=schedule'
        }
        self.make_request(url=url, method='post', payload=post_data)

    def make_request(self, url, method, payload=None, headers=None):
        """Make an http request. Return the response."""
        self.log('Request URL: %s' %url)
        self.log('Headers: %s' %headers)

        try:
            if method == 'get':
                req = self.http_session.get(url, params=payload, headers=headers, allow_redirects=False)
            else: # post
                req = self.http_session.post(url, data=payload, headers=headers, allow_redirects=False)
            self.log('Response code: %s' %req.status_code)
            self.log('Response: %s' %req.text)
            self.cookie_jar.save(ignore_discard=True, ignore_expires=False)
            return req.text
        except requests.exceptions.RequestException as error:
            self.log('Error: - %s' %error.value)

    def parse_manifest(self, manifest):
        """Return a dict of the supplied XML manifest. Builds and adds
        "full_url" for convenience.
        """
        streams = {}
        manifest_dict = xmltodict.parse(manifest)

        for stream in manifest_dict['channel']['streamDatas']['streamData']:
            try:
                url_path = stream['@url']
                bitrate = url_path[(url_path.rindex('_') + 1):url_path.rindex('.')]
                try:
                    stream['full_url'] = 'http://%s%s.m3u8' %(stream['httpservers']['httpserver']['@name'], url_path)
                except TypeError: # if multiple servers are returned, use the first in the list
                    stream['full_url'] = 'http://%s%s.m3u8' %(stream['httpservers']['httpserver'][0]['@name'], url_path)

                streams[bitrate] = stream
            except KeyError:
                self.log(format_exc())

        return streams

    def redzone_on_air(self):
        """Return whether RedZone Live is currently broadcasting."""
        url = self.servlets_url + '/simpleconsole'
        post_data = {'isFlex': 'true'}
        sc_data = self.make_request(url=url, method='post', payload=post_data)

        sc_dict = xmltodict.parse(sc_data)['result']
        if sc_dict['rzPhase'] == 'in':
            self.log('RedZone is on air.')
            return True
        else:
            self.log('RedZone is not on air.')
            return False

    def service_blackout(self):
        """Return whether Game Rewind is blacked out."""
        url = self.base_url + '/secure/schedule'
        blackout_message = ('Due to broadcast restrictions, the NFL Game Rewind service is currently unavailable.'
                            ' Please check back later.')
        service_data = self.make_request(url=url, method='get')

        if blackout_message in service_data:
            return True
        else:
            return False
