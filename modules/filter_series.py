import logging
import re
import types
from datetime import tzinfo, timedelta, datetime

log = logging.getLogger('series')

# might be better of just being function which returns dict ...
class SerieParser:

    qualities = ['1080p', '1080', '720p', '720', 'hr', 'dvd', 'hdtv', 'dsr', 'dsrip', 'unknown']
    season_ep_regexps = ['s(\d+)e(\d+)', 's(\d+)ep(\d+)', '(\d+)x(\d+)']
    
    def __init__(self, name, title):
        self.name = name
        self.item = title
        # parse produces these
        self.season = None
        self.episode = None
        self.quality = 'unknown'
        # false if item does not match serie
        self.valid = False
        # parse
        self.parse()
        # optional for storing entry from which this instance is made from
        self.entry = None

    def parse(self):
        serie = self.name.replace('.', ' ').lower()
        item = self.item.replace('.', ' ').replace('_', ' ').lower()
        item = item.replace('[','').replace(']','')
        serie_data = serie.split(' ')
        item_data = item.split(' ')
        for part in serie_data:
            if part in item_data:
                item_data.remove(part)
            else:
                #log.debug('part %s not found from %s' % (part, item_data))
                # leave this invalid
                return

        for part in item_data:
            # search for quality
            if part in self.qualities:
                if self.qualities.index(part) < self.qualities.index(self.quality):
                    log.debug('%s storing quality %s' % (self.name, part))
                    self.quality = part
                else:
                    log.debug('%s ignoring quality %s because found better %s' % (self.name, part, self.quality))
            # search for season and episode number
            for sre in self.season_ep_regexps:
                m = re.search(sre, part)
                if m:
                    if len(m.groups())==2:
                        season, episode = m.groups()
                        self.season = int(season)
                        self.episode = int(episode)
                        self.valid = True
                        break

    def identifier(self):
        """Return episode in form of S<Season>E<Episode>"""
        if not self.valid: raise Exception('Serie flagged invalid')
        return "S%sE%s" % (self.season, self.episode)

    def __str__(self):
        valid = 'INVALID'
        if self.valid:
            valid = 'OK'
        return 'serie: %s, season: %s episode: %s quality: %s status: %s' % (str(self.name), str(self.season), str(self.episode), str(self.quality), valid)
        


class FilterSeries:

    """
        Intelligent filter for tv-series. This solves duplicate downloads
        problem that occurs when using patterns (regexp) matching since same
        episode is often released by multiple groups.

        Example configuration:

        series:
          - some serie
          - another serie
          
        If "some serie" and "another serie" have understandable episode
        numbering any given episode is downloaded only once.

        So if we get same episode twice:
        
        Some.Serie.S2E10.More.Text
        Some.Serie.S2E10.Something.Else

        Only first file is downloaded.

        If two different qualities come available at the same moment,
        flexget will always download the better one. (more options coming ..)
        
        Timeframe:

        Serie filter allows you to specify a timeframe for each serie in which
        flexget waits better quality.

        Example configuration:

        series:
          - some serie:
              timeframe:
                hours: 4
                enough: 720p
          - another serie
          - third serie

        In this example when a epsisode of 'some serie' appears, flexget will wait
        for 4 hours incase and then proceeds to download best quality available.

        The enough parameter will tell the quality that you find good enough to start
        downloading without waiting whole timeframe. If qualities meeting enough parameter
        and above are available, flexget will prefer the enough. Ie. if enough value is set
        to 'hdtv' and qualities dsk, hdtv and 720p are available, hdtv will be chosen.
        If we take hdtv off from list, 720p would be downloaded.

        Enough has default value of 720p.

        Possible values for enough (in order): 1080p, 1080, 720p, 720, hr, dvd, hdtv, dsr, dsrip
    """

    def register(self, manager, parser):
        manager.register(instance=self, event="filter", keyword="series", callback=self.filter_series)
        manager.register(instance=self, event="input", keyword="series", callback=self.input_series, order=65535)
        manager.register(instance=self, event="exit", keyword="series", callback=self.learn_succeeded)

    def input_series(self, feed):
        """Retrieve stored series from cache, incase they've been expired from feed while waiting"""
        for name in feed.config.get('series', []):
            conf = {}
            if type(name) == types.DictType:
                name, conf = name.items()[0]

            serie = feed.cache.get(name)
            if not serie: continue
            for identifier in serie.keys():
                for quality in SerieParser.qualities:
                    if quality=='info': continue # a hack, info dict is not quality
                    entry = serie[identifier].get(quality)
                    if not entry: continue
                    # check if episode is still in feed, if not add it
                    exists = False
                    for feed_entry in feed.entries:
                        if feed_entry['title'] == entry['title'] and feed_entry['url'] == entry['url']:
                            exists = True
                    if not exists:
                        log.debug('restoring entry %s from cache' % entry['title'])
                        feed.entries.append(entry)


    def cmp_serie_quality(self, s1, s2):
        return self.cmp_quality(s1.quality, s2.quality)

    def cmp_quality(self, q1, q2):
        return cmp(SerieParser.qualities.index(q1), SerieParser.qualities.index(q2))

    def filter_series(self, feed):
        for name in feed.config.get('series', []):
            conf = {}
            if type(name) == types.DictType:
                name, conf = name.items()[0]

            series = {} # ie. S1E2: [Serie, Serie, ..]
            for entry in feed.entries:
                serie = SerieParser(name, entry['title'])
                if not serie.valid: continue
                serie.entry = entry
                self.store(feed, serie, entry)
                # add this episode into list of available episodes
                eps = series.setdefault(serie.identifier(), [])
                eps.append(serie)

            # choose episode from available qualities
            for identifier, eps in series.iteritems():
                if not eps: continue
                eps.sort(self.cmp_serie_quality)
                best = eps[0]
                
                if self.downloaded(feed, best):
                    log.debug('Rejecting all episodes of %s. Episode has been already downloaded.' % identifier)
                    for ep in eps:
                        feed.reject(ep.entry)
                    continue

                # timeframe present
                if conf.has_key('timeframe'):
                    tconf = conf.get('timeframe')
                    hours = tconf.get('hours', 0)
                    enough = tconf.get('enough', '720p')

                    if not enough in SerieParser.qualities:
                        log.error('Parameter enough has unknown value: %s' % enoigh)

                    # scan for enough, starting from worst quality (reverse)
                    eps.reverse()
                    for ep in eps:
                        if self.cmp_quality(enough, ep.quality) >= 0: # 1=greater, 0=equal, -1=does not meet
                            log.debug('Episode %s meets quality %s' % (ep.entry['title'], enough))
                            feed.accept(ep.entry)
                            continue
                            
                    # timeframe
                    diff = datetime.today() - self.get_first_seen(feed, best)
                    age_hours = divmod(diff.seconds, 60*60)[0]
                    log.debug('age_hours %i - %s ' % (age_hours, best))
                    log.debug('best ep in %i hours is %s' % (hours, best))
                    if age_hours >= hours:
                        log.debug('Accepting %s' % best.entry)
                        feed.accept(best.entry)
                        # store serie instance to entry for later use
                        best.entry['serie_parser'] = best
                        # remove entry instance from serie instance, not needed any more (save memory, circular reference?)
                        best.entry = None
                    else:
                        log.debug('Timeframe ignoring %s' % (best.entry['title']))
                else:
                    # no timeframe, just choose best
                    feed.accept(best.entry)

        # filter ALL entries, only previously accepted will remain
        # other modules may still accept entries
        for entry in feed.entries:
            feed.filter(entry)

    def reject_eps(self, feed, eps):
        for ep in eps:
            feed.reject(ep.entry)

    def get_first_seen(self, feed, serie):
        """Return datetime when this episode of serie was first seen"""
        fs = feed.cache.get(serie.name)[serie.identifier()]['info']['first_seen']
        return datetime(*fs)

    def downloaded(self, feed, serie):
        """Return true if this episode of serie is downloaded"""
        cache = feed.cache.get(serie.name)
        return cache[serie.identifier()]['info']['downloaded']

    def store(self, feed, serie, entry):
        """Stores serie into cache"""
        # serie_name:
        #   S1E2:
        #     info:
        #       first_seen: <time>
        #       downloaded: <boolean>
        #     720p: <entry>
        #     dsr: <entry>
        cache = feed.cache.storedetault(serie.name, {}, 30)
        episode = cache.setdefault(serie.identifier(), {})
        info = episode.setdefault('info', {})
        # store and make first seen time
        fs = info.setdefault('first_seen', list(datetime.today().timetuple())[:-4] )
        first_seen = datetime(*fs)
        info.setdefault('downloaded', False)
        ec = {}
        ec['title'] = entry['title']
        ec['url'] = entry['url']
        episode.setdefault(serie.quality, ec)

    def mark_downloaded(self, feed, serie):
        cache = feed.cache.get(serie.name)
        cache[serie.identifier()]['info']['downloaded'] = True

    def learn_succeeded(self, feed):
        for entry in feed.get_succeeded_entries():
            serie = entry.get('serie_parser')
            if serie:
                self.mark_downloaded(feed, serie)

if __name__ == '__main__':
    fs = SerieParser('mock serie', 'Mock.Serie.S04E01.HDTV.XviD-TEST.avi')
    fs.parse()
    print fs
