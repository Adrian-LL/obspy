# -*- coding: utf-8 -*-
"""
NERIES Web service client for ObsPy.

.. seealso:: http://www.seismicportal.eu/jetspeed/portal/web-services.psml

:copyright:
    The ObsPy Development Team (devs@obspy.org)
:license:
    GNU Lesser General Public License, Version 3
    (http://www.gnu.org/copyleft/lesser.html)
"""
from obspy.core import UTCDateTime, read, Stream
from obspy.core.util import _getVersionString, NamedTemporaryFile
from suds.client import Client as SudsClient
from suds.plugin import MessagePlugin
from suds.sax.attribute import Attribute
import functools
import os
import platform
import sys
import urllib
import urllib2
try:
    import json
    if not getattr(json, "loads", None):
        json.loads = json.read #@UndefinedVariable
except ImportError:
    import simplejson as json


SEISMOLINK_WSDL = "http://www.orfeus-eu.org/wsdl/seismolink/seismolink.wsdl"
TAUP_WSDL = "http://www.orfeus-eu.org/wsdl/taup/taup.wsdl"

MAP = {'min_datetime': "dateMin", 'max_datetime': "dateMax",
       'min_latitude': "latMin", 'max_latitude': "latMax",
       'min_longitude': "lonMin", 'max_longitude': "lonMax",
       'min_depth': "depthMin", 'max_depth': "depthMax",
       'min_magnitude': "magMin", 'max_magnitude': "magMax",
       'magnitude_type': "magType", 'author': "auth",
       'max_results': "limit", 'sort_by': "sort", 'sort_direction': "dir",
       'format': "format", 'datetime': "datetime", 'depth': "depth",
       'flynn_region': "flynn_region", 'latitude': "lat",
       'longitude': "lon", 'magnitude': "mag", 'origin_id': "orid",
       'event_id': "unid"}

MAP_INVERSE = dict([(value, key) for key, value in MAP.iteritems()])
# in results the "magType" key is all lowercase, so add it to..
MAP_INVERSE['magtype'] = "magnitude_type"

VERSION = _getVersionString("obspy.neries")
DEFAULT_USER_AGENT = "ObsPy %s (%s, Python %s)" % (VERSION, platform.platform(),
                                                   platform.python_version())


def _mapKwargs(f):
    """
    Maps function arguments to keyword arguments.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        # set some default values
        new_kwargs = {'sort': "datetime", 'dir': "ASC", 'limit': 100,
                      'format': "list"}
        for key in kwargs:
            if key in MAP:
                new_kwargs[MAP[key]] = kwargs[key]
        v = f(*args, **new_kwargs)
        return v
    return wrapper


class _RequestMSEEDPlugin(MessagePlugin):
    """
    Suds plug-in extending the dataRequest method call with an custom attribute.
    """
    def marshalled(self, context):
        foo = context.envelope.getChild('Body')[0]
        if foo.name == 'dataRequest':
            foo.attributes.append(Attribute("DataFormat", "MSEED"))


class Client(object):
    """
    NERIES Web service request client.
    """
    def __init__(self, user="", password="", timeout=10, debug=False,
                 user_agent=DEFAULT_USER_AGENT):
        """
        Initializes the NERIES Web service client.

        :type user: string, optional
        :param user: The user name used for identification with the Web service.
            This entry in form of a email address is required for using the
            following methods:
                * :meth:`~saveWaveform`
                * :meth:`~getWaveform`
                * :meth:`~getInventory`
            Defaults to ``''``.
        :type password: string, optional
        :param password: A password used for authentication with the Web
            service. Defaults to ``''``.
        :type timeout: int, optional
        :param timeout: Seconds before a connection timeout is raised (default
            is 10 seconds). Available only for Python >= 2.6.x.
        :type debug: boolean, optional
        :param debug: Enables verbose output..
        :type user_agent: string, optional
        :param user_agent: Sets an client identification string which may be
            used on server side for statistical analysis (default contains the
            current module version and basic information about the used
            operation system, e.g.
            ``'ObsPy 0.4.7.dev-r2432 (Windows-7-6.1.7601-SP1, Python 2.7.1)'``.
        """
        self.base_url = "http://www.seismicportal.eu"
        self.timeout = timeout
        self.debug = debug
        self.user_agent = user_agent
        self.user = user
        self.password = password
        # Create an OpenerDirector for Basic HTTP Authentication
        password_mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, self.base_url, self.user, self.password)
        auth_handler = urllib2.HTTPBasicAuthHandler(password_mgr)
        opener = urllib2.build_opener(auth_handler)
        # install globally
        urllib2.install_opener(opener)

    def _fetch(self, url, headers={}, **params):
        """
        Send a HTTP request via urllib2.

        :type url: String
        :param url: Complete URL of resource
        :type headers: dict
        :param headers: Additional header information for request
        """
        headers['User-Agent'] = self.user_agent
        # replace special characters 
        remoteaddr = self.base_url + url + '?' + urllib.urlencode(params)
        if self.debug:
            print('\nRequesting %s' % (remoteaddr))
        # timeout exists only for Python >= 2.6
        if sys.hexversion < 0x02060000:
            response = urllib2.urlopen(remoteaddr)
        else:
            response = urllib2.urlopen(remoteaddr, timeout=self.timeout)
        doc = response.read()
        return doc

    def _json2list(self, data):
        """
        Converts a JSON formated string into a event/origin list.
        """
        results = json.loads(data)
        events = []
        float_keys = ('depth', 'latitude', 'longitude', 'magnitude')
        for result in results['unids']:
            event = dict([(MAP_INVERSE[k], v)
                          for k, v in result.iteritems()])
            for k in float_keys:
                event[k] = float(event[k])
            event['magnitude_type'] = event['magnitude_type'].lower()
            event['datetime'] = UTCDateTime(event['datetime'])
            # convention in ObsPy: all depths negative down
            event['depth'] = -event['depth']
            events.append(event)
        return events

    @_mapKwargs
    def getEvents(self, min_datetime=None, max_datetime=None,
                  min_longitude=None, max_longitude=None, min_latitude=None,
                  max_latitude=None, min_depth=None, max_depth=None,
                  min_magnitude=None, max_magnitude=None, magnitude_type=None,
                  author=None, sort_by="datetime", sort_direction="ASC",
                  max_results=100, format="list", **kwargs):
        """
        Gets a list of events.

        :type min_datetime: str, optional
        :param min_datetime: Earliest date and time for search.
            ISO 8601-formatted, in UTC: yyyy-MM-dd['T'HH:mm:ss].
            e.g.: ``"2002-05-17"`` or ``"2002-05-17T05:24:00"``
        :type max_datetime: str, optional
        :param max_datetime: Latest date and time for search.
        :type min_latitude: int or float, optional
        :param min_latitude: Minimum latitude for search. Format: +/- 90 decimal
            degrees.
        :type max_latitude: int or float, optional
        :param max_latitude: Maximum latitude for search.
        :type min_longitude: int or float, optional
        :param min_longitude: Minimum ("left-side") longitude for search.
            Format: +/- 180 decimal degrees.
        :type max_longitude: int or float, optional
        :param max_longitude: Maximum ("right-side") longitude for search.
        :type min_depth: int or float, optional
        :param min_depth: Minimum event depth. Format: in km, negative down.
        :type max_depth: int or float, optional
        :param max_depth: Maximum event depth.
        :type min_magnitude: int or float, optional
        :param min_magnitude: Minimum event magnitude.
        :type max_magnitude: int or float, optional
        :param max_magnitude: Maximum event magnitude.
        :type magnitude_type: str, optional
        :param magnitude_type: Magnitude scale type. Examples: ``"mw"`` or
            ``"mb"``.
        :type author: str, optional
        :param author: Origin author. Examples: ``"CSEM"``, ``"LDG"``, ...
        :type max_results: int (maximum: 2500)
        :param max_results: Maximum number of returned results.
        :type sort_by: str
        :param sort_by: Field to sort by. Options: ``"datetime"``,
            ``"magnitude"``, ``"flynn_region"``, ``"depth"``. Only available if
            attribute ``format`` is set to ``"list"``.
        :type sort_direction: str
        :param sort_direction: Sort direction. Format: ``"ASC"`` or ``"DESC"``.
        :type format: ``'list'`` or ``'xml'``, optional
        :param format: Format of returned results. Defaults to ``'xml'``.

            .. note:: 
                The JSON-formatted queries only look at preferred origin
                parameters, whereas QuakeML queries search all associated
                origins.

        :return: List of event dictionaries or QuakeML string.

        .. seealso:: http://www.seismicportal.eu/services/event/search/info/

        .. rubric:: Example

        >>> from obspy.neries import Client
        >>> client = Client()
        >>> events = client.getEvents(min_datetime="2004-12-01",
        ...                           max_datetime="2005-01-01",
        ...                           min_magnitude=9)
        >>> len(events)
        1
        >>> events #doctest: +SKIP
        [{'author': u'CSEM', 'event_id': u'20041226_0000148', 
          'origin_id': 127773, 'longitude': 95.724, 
          'datetime': UTCDateTime(2004, 12, 26, 0, 58, 50), 'depth': -10.0,
          'magnitude': 9.3, 'magnitude_type': u'mw', 'latitude': 3.498,
          'flynn_region': u'OFF W COAST OF NORTHERN SUMATRA'}]
        """
        # map request format string "list" -> "json"
        if format == "list":
            kwargs['format'] = "json"
        # switch depth to positive down
        if kwargs.get("depthMin"):
            kwargs['depthMin'] = -kwargs['depthMin']
        if kwargs.get("depthMax"):
            kwargs['depthMax'] = -kwargs['depthMax']
        # fetch data
        data = self._fetch("/services/event/search", **kwargs)
        # format output
        if format == "list":
            return self._json2list(data)
        else:
            return data

    def getLatestEvents(self, num=10, format="xml"):
        """
        Gets a list of recent events.

        :type num: int, optional
        :param num: Number of events to return. Defaults to ``10``.

            .. note::
                Absolute maximum is 2500 events.

        :type format: ``'list'`` or ``'xml'``, optional
        :param format: Format of returned results. Defaults to ``'xml'``.
        :return: List of event dictionaries or QuakeML string.

        .. seealso:: http://www.seismicportal.eu/services/event/latest/info/

        .. rubric:: Example

        >>> from obspy.neries import Client
        >>> client = Client()
        >>> events = client.getLatestEvents(num=5, format='list')
        >>> len(events)
        5
        >>> events[0] #doctest: +SKIP 
        [{'author': u'CSEM', 'event_id': u'20041226_0000148', 
          'origin_id': 127773, 'longitude': 95.724, 
          'datetime': u'2004-12-26T00:58:50Z', 'depth': -10.0, 'magnitude': 9.3,
          'magnitude_type': u'mw', 'latitude': 3.498,
          'flynn_region': u'OFF W COAST OF NORTHERN SUMATRA'}]
        """
        # parse parameters
        kwargs = {}
        try:
            kwargs['num'] = int(num)
        except:
            kwargs['num'] = 10
        if format == 'list':
            kwargs['format'] = 'json'
        else:
            kwargs['format'] = 'xml'
        # fetch data
        data = self._fetch("/services/event/latest", **kwargs)
        # format output
        if format == "list":
            return self._json2list(data)
        else:
            return data

    def getEventDetail(self, uri, format="xml"):
        """
        Gets event detail information.

        :type uri: str
        :param uri: Event identifier as either a EMSC event unique identifier,
            e.g. ``"19990817_0000001"`` or a QuakeML-formatted event URI, e.g.
            ``"quakeml:eu.emsc/event#19990817_0000001"``.
        :type format: ``'list'`` or ``'xml'``, optional
        :param format: Format of returned results. Defaults to ``'xml'``.
        :return: List of event dictionaries or QuakeML string.

        .. seealso:: http://www.seismicportal.eu/services/event/detail/info/

        .. rubric:: Example

        >>> from obspy.neries import Client
        >>> client = Client()
        >>> result = client.getEventDetail("19990817_0000001", 'list')
        >>> len(result)  # Number of calculated origins
        12
        >>> result[0]  # Details about first calculated origin #doctest: +SKIP
        {'author': u'EMSC', 'event_id': u'19990817_0000001',
         'origin_id': 1465935, 'longitude': 29.972,
         'datetime': UTCDateTime(1999, 8, 17, 0, 1, 35), 'depth': -10.0,
         'magnitude': 6.7, 'magnitude_type': u'mw', 'latitude': 40.749}
        """
        # parse parameters
        kwargs = {}
        if format == 'list':
            kwargs['format'] = 'json'
        else:
            kwargs['format'] = 'xml'
        if str(uri).startswith('quakeml:'):
            # QuakeML-formatted event URI
            kwargs['uri'] = str(uri)
        else:
            # EMSC event unique identifier 
            kwargs['unid'] = str(uri)
        # fetch data
        data = self._fetch("/services/event/detail", **kwargs)
        # format output
        if format == "list":
            return self._json2list(data)
        else:
            return data

    def getTravelTimes(self, latitude, longitude, depth, locations=[],
                       model='iasp91'):
        """
        Returns travel times for specified station-event geometry using
        standard velocity models such as ``iasp91``, ``ak135`` or ``qdt``.

        :type latitude: float
        :param latitude: Event latitude.
        :type longitude: float
        :param longitude: Event longitude.
        :type depth: float
        :param depth: Event depth in km.
        :type locations: list of tuples
        :param locations: Each tuple contains a pair of (latitude, longitude) of
            a station.
        :type model: ``'iasp91'``, ``'ak135'``, or ``'qdt'``, optional
        :param model: Velocity model, defaults to 'iasp91'.
        :return: List of dicts containing phase name and arrival times in ms.

        .. seealso:: http://www.orfeus-eu.org/wsdl/taup/taup.wsdl

        .. rubric:: Example

        >>> client = Client()
        >>> locations = [(48.0, 12.0), (48.1, 12.0)]
        >>> result = client.getTravelTimes(latitude=20.0, longitude=20.0,
        ...                                depth=10.0, locations=locations,
        ...                                model='iasp91')
        >>> len(result)
        2
        >>> result[0] # doctest: +SKIP
        {'P': 356981.13561726053, 'S': 646841.5619481194}
        """
        client = SudsClient(TAUP_WSDL)
        # set cache of 5 days
        cache = client.options.cache
        cache.setduration(days=5)
        # create request
        request = []
        for location in locations:
            req = {'event-depth': float(depth),
                   'event-lat': float(latitude),
                   'event-lon': float(longitude),
                   'model': str(model),
                   'point-lat': float(location[0]),
                   'point-lon': float(location[1])}
            request.append(req)
        data = client.service.getArrivalTimes(request)
        result = []
        for item in data:
            times = {}
            if hasattr(item, 'arrival-time'):
                for time in item['arrival-time']:
                    times[str(time._phase)] = float(time['_time-ms'])
            result.append(times)
        return result

    def getWaveform(self, network, station, location, channel, starttime,
                    endtime, format="MSEED"):
        """
        Retrieves waveform data from the NERIES Web service and returns a ObsPy
        Stream object.

        :type network: string
        :param network: Network code, e.g. ``'BW'``.
        :type station: string
        :param station: Station code, e.g. ``'MANZ'``.
        :type location: string
        :param location: Location code, e.g. ``'01'``. Location code may contain
            wild cards.
        :type channel: string
        :param channel: Channel code, e.g. ``'EHE'``. . Channel code may contain
            wild cards.
        :type starttime: :class:`~obspy.core.utcdatetime.UTCDateTime`
        :param starttime: Start date and time.
        :type endtime: :class:`~obspy.core.utcdatetime.UTCDateTime`
        :param endtime: End date and time.
        :type format: ``'FSEED'`` or ``'MSEED'``, optional
        :param format: Output format. Either as full SEED (``'FSEED'``) or 
            Mini-SEED (``'MSEED'``) volume. Defaults to ``'MSEED'``.
        :return: ObsPy :class:`~obspy.core.stream.Stream` object.

        .. rubric:: Example

        >>> from obspy.neries import Client
        >>> client = Client(user='test@obspy.org')
        >>> dt = UTCDateTime("2009-04-01T00:00:00")
        >>> st = client.getWaveform("NL", "WIT", "", "BH*", dt, dt+30)
        >>> print st
        3 Trace(s) in Stream:
        NL.WIT..BHE | 2009-04-01T00:00:00.010100Z - 2009-04-01T00:00:30.010100Z | 40.0 Hz, 1201 samples
        NL.WIT..BHZ | 2009-04-01T00:00:00.010100Z - 2009-04-01T00:00:30.010100Z | 40.0 Hz, 1201 samples
        NL.WIT..BHN | 2009-04-01T00:00:00.010100Z - 2009-04-01T00:00:30.010100Z | 40.0 Hz, 1201 samples
        """
        tf = NamedTemporaryFile()
        self.saveWaveform(tf._fileobj, network, station, location, channel,
                          starttime, endtime, format=format)
        # read stream using obspy.mseed
        tf.seek(0)
        try:
            stream = read(tf.name, 'MSEED')
        except:
            stream = Stream()
        tf.close()
        # remove temporary file:
        try:
            os.remove(tf.name)
        except:
            pass
        # trim stream
        stream.trim(starttime, endtime)
        return stream

    def saveWaveform(self, filename, network, station, location, channel,
                     starttime, endtime, format="MSEED"):
        """
        Writes a retrieved waveform directly into a file.

        This method ensures the storage of the unmodified waveform data
        delivered by the NERIES Web service, e.g. preserving the record based
        quality flags of MiniSEED files which would be neglected reading it
        with obspy.mseed.

        :type filename: string
        :param filename: Name of the output file.
        :type network: string
        :param network: Network code, e.g. ``'BW'``.
        :type station: string
        :param station: Station code, e.g. ``'MANZ'``.
        :type location: string
        :param location: Location code, e.g. ``'01'``. Location code may contain
            wild cards.
        :type channel: string
        :param channel: Channel code, e.g. ``'EHE'``. . Channel code may contain
            wild cards.
        :type starttime: :class:`~obspy.core.utcdatetime.UTCDateTime`
        :param starttime: Start date and time.
        :type endtime: :class:`~obspy.core.utcdatetime.UTCDateTime`
        :param endtime: End date and time.
        :type format: ``'FSEED'`` or ``'MSEED'``, optional
        :param format: Output format. Either as full SEED (``'FSEED'``) or 
            Mini-SEED (``'MSEED'``) volume. Defaults to ``'MSEED'``.
        :return: None

        .. seealso:: http://www.orfeus-eu.org/wsdl/seismolink/seismolink.wsdl

        .. rubric:: Example

        >>> from obspy.neries import Client
        >>> client = Client(user='test@obspy.org')
        >>> dt = UTCDateTime("2009-04-01T00:00:00")
        >>> st = client.saveWaveform("outfile.fseed" "NL", "WIT", "", "BH*",
        ...                          dt, dt+30, format="FSEED")  #doctest: +SKIP
        """
        client = SudsClient(SEISMOLINK_WSDL)
        # set cache of 5 days
        cache = client.options.cache
        cache.setduration(days=5)
        # create user token
        usertoken = client.factory.create('UserTokenType')
        usertoken.email = self.user
        usertoken.password = self.password
        usertoken.label = self.user_agent.replace(' ', '_')
        usertoken.locale = ""
        # create station filter 
        stationid = client.factory.create('StationIdentifierType')
        stationid.NetworkCode = network
        stationid.StationCode = station
        stationid.ChannelCode = channel
        stationid.LocId = location
        stationid.TimeSpan.TimePeriod.beginPosition = \
            UTCDateTime(starttime).strftime("%Y-%m-%dT%H:%M:%S")
        stationid.TimeSpan.TimePeriod.endPosition = \
            UTCDateTime(endtime).strftime("%Y-%m-%dT%H:%M:%S")
        # request data
        if format == 'MSEED':
            # XXX: dirty attribute hack for requesting waveform format MSEED
            client.options.plugins = [_RequestMSEEDPlugin()]
        # start data request
        response = client.service.dataRequest(usertoken, stationid)
        if format == 'MSEED':
            # XXX: see above
            client.options.plugins = []
        # filter for request ids
        request_ids = [r._Id for r in response.RoutedRequest]
        if not request_ids:
            return
        # check status using request ids
        while True:
            response = client.service.checkStatus(usertoken, request_ids)
            status = [r.ReadyFlag for r in response.RoutedRequest]
            if "false" in status:
                # retry until all are set to 'true'
                continue
            break
        # keep only request ids which are fulfilled and have 'status = OK'
        request_ids = [r._Id for r in response.RoutedRequest
                       if 'Status: OK' in r.StatusDescription
                       and r.Fulfillment == 100]
        if not request_ids:
            return
        # retrieve download URLs using request ids
        response = client.service.dataRetrieve(usertoken, request_ids)
        urls = [r.DownloadToken.DownloadURL for r in response.DataItem]
        # create file handler if a file name is given
        if isinstance(filename, basestring):
            fh = open(filename, "wb")
        elif isinstance(filename, file):
            fh = filename
        else:
            msg = "Parameter filename must be either string or file handler."
            raise TypeError(msg)
        for url in urls:
            fh.write(urllib2.urlopen(url).read())
        if isinstance(filename, basestring):
            fh.close()
        # clean up 
        response = client.service.purgeData(usertoken, request_ids)


if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)
