from __future__ import unicode_literals

import logging
import threading

import spotify
from spotify import ffi, lib, utils


__all__ = [
    'Artist',
    'ArtistBrowser',
    'ArtistBrowserType',
]

logger = logging.getLogger(__name__)


class Artist(object):
    """A Spotify artist.

    You can get artists from tracks and albums, or you can create an
    :class:`Artist` yourself from a Spotify URI::

        >>> artist = spotify.Artist('spotify:artist:22xRIphSN7IkPVbErICu7s')
        >>> artist.load().name
        u'Rob Dougan'
    """

    def __init__(self, uri=None, sp_artist=None):
        assert uri or sp_artist, 'uri or sp_artist is required'
        if uri is not None:
            artist = spotify.Link(uri).as_artist()
            if artist is None:
                raise ValueError(
                    'Failed to get artist from Spotify URI: %r' % uri)
            sp_artist = artist._sp_artist
        lib.sp_artist_add_ref(sp_artist)
        self._sp_artist = ffi.gc(sp_artist, lib.sp_artist_release)

    def __repr__(self):
        return 'Artist(%r)' % self.link.uri

    @property
    def name(self):
        """The artist's name.

        Will always return :class:`None` if the artist isn't loaded.
        """
        name = utils.to_unicode(lib.sp_artist_name(self._sp_artist))
        return name if name else None

    @property
    def is_loaded(self):
        """Whether the artist's data is loaded."""
        return bool(lib.sp_artist_is_loaded(self._sp_artist))

    def load(self, timeout=None):
        """Block until the artist's data is loaded.

        :param timeout: seconds before giving up and raising an exception
        :type timeout: float
        :returns: self
        """
        # TODO Need to send a browse request for the object to be populated
        # with data
        return utils.load(self, timeout=timeout)

    def portrait(self, image_size=None):
        """The artist's portrait :class:`Image`.

        ``image_size`` is an :class:`ImageSize` value, by default
        :attr:`ImageSize.NORMAL`.

        Will always return :class:`None` if the artist isn't loaded or the
        artist has no portrait.
        """
        if image_size is None:
            image_size = spotify.ImageSize.NORMAL
        portrait_id = lib.sp_artist_portrait(self._sp_artist, image_size)
        if portrait_id == ffi.NULL:
            return None
        sp_image = lib.sp_image_create(
            spotify.session_instance._sp_session, portrait_id)
        return spotify.Image(sp_image=sp_image, add_ref=False)

    def portrait_link(self, image_size=None):
        """A :class:`Link` to the artist's portrait.

        ``image_size`` is an :class:`ImageSize` value, by default
        :attr:`ImageSize.NORMAL`.

        This is equivalent with ``artist.portrait.link``, except that this
        method does not need to create the artist portrait image object to
        create a link to it.
        """
        if image_size is None:
            image_size = spotify.ImageSize.NORMAL
        return spotify.Link(sp_link=lib.sp_link_create_from_artist_portrait(
            self._sp_artist, image_size))

    @property
    def link(self):
        """A :class:`Link` to the artist."""
        return spotify.Link(
            sp_link=lib.sp_link_create_from_artist(self._sp_artist))

    def browse(self, type=None, callback=None):
        """Get an :class:`ArtistBrowser` for the artist.

        If ``type`` is :class:`None`, it defaults to
        :attr:`ArtistBrowserType.FULL`.

        If ``callback`` isn't :class:`None`, it is expected to be a callable
        that accepts a single argument, an :class:`ArtistBrowser` instance,
        when the browser is done loading.

        Can be created without the artist being loaded.
        """
        return spotify.ArtistBrowser(artist=self, type=type, callback=callback)


class ArtistBrowser(object):
    """An artist browser for a Spotify artist.

    You can get an artist browser from any :class:`Artist` instance by calling
    :meth:`Artist.browse`::

        >>> artist = spotify.Artist('spotify:artist:421vyBBkhgRAOz4cYPvrZJ')
        >>> browser = artist.browse()
        >>> browser.load()
        >>> len(browser.albums)
        7
    """

    def __init__(
            self, artist=None, type=None, callback=None,
            sp_artistbrowse=None, add_ref=True):

        assert artist or sp_artistbrowse, (
            'artist or sp_artistbrowse is required')

        self.complete_event = threading.Event()
        self._callback_handles = set()

        if sp_artistbrowse is None:
            if type is None:
                type = ArtistBrowserType.FULL

            handle = ffi.new_handle((callback, self))
            # TODO Think through the life cycle of the handle object. Can it
            # happen that we GC the browser and handle object, and then later
            # the callback is called?
            self._callback_handles.add(handle)

            sp_artistbrowse = lib.sp_artistbrowse_create(
                spotify.session_instance._sp_session, artist._sp_artist,
                int(type), _artistbrowse_complete_callback, handle)
            add_ref = False

        if add_ref:
            lib.sp_artistbrowse_add_ref(sp_artistbrowse)
        self._sp_artistbrowse = ffi.gc(
            sp_artistbrowse, lib.sp_artistbrowse_release)

    complete_event = None
    """:class:`threading.Event` that is set when the artist browser is loaded.
    """

    def __repr__(self):
        return 'ArtistBrowser(%r)' % self.artist.link.uri

    @property
    def is_loaded(self):
        """Whether the artist browser's data is loaded."""
        return bool(lib.sp_artistbrowse_is_loaded(self._sp_artistbrowse))

    def load(self, timeout=None):
        """Block until the artist browser's data is loaded.

        :param timeout: seconds before giving up and raising an exception
        :type timeout: float
        :returns: self
        """
        return utils.load(self, timeout=timeout)

    @property
    def error(self):
        """An :class:`ErrorType` associated with the artist browser.

        Check to see if there was problems creating the artist browser.
        """
        return spotify.ErrorType(
            lib.sp_artistbrowse_error(self._sp_artistbrowse))

    @property
    def backend_request_duration(self):
        """The time in ms that was spent waiting for the Spotify backend to
        create the artist browser.

        Returns ``-1`` if the request was served from local cache. Returns
        :class:`None` if the artist browser isn't loaded yet.
        """
        if not self.is_loaded:
            return None
        return lib.sp_artistbrowse_backend_request_duration(
            self._sp_artistbrowse)

    @property
    def artist(self):
        """Get the :class:`Artist` the browser is for."""
        # TODO Check behavior when not loaded
        return Artist(
            sp_artist=lib.sp_artistbrowse_artist(self._sp_artistbrowse))

    @property
    def portraits(self):
        """The artist's portraits.

        Will always return an empty list if the artist browser isn't loaded.
        """
        if not self.is_loaded:
            return []

        def get_image(sp_artistbrowse, key):
            image_id = lib.sp_artistbrowse_portrait(sp_artistbrowse, key)
            sp_image = lib.sp_image_create(image_id)
            return spotify.Image(sp_image=sp_image, add_ref=False)

        return utils.Sequence(
            sp_obj=self._sp_artistbrowse,
            add_ref_func=lib.sp_artistbrowse_add_ref,
            release_func=lib.sp_artistbrowse_release,
            len_func=lib.sp_artistbrowse_num_portraits,
            getitem_func=get_image)

    @property
    def tracks(self):
        """The artist's tracks.

        Will be an empty list if the browser was created with a ``type`` of
        :attr:`ArtistBrowserType.NO_TRACKS` or
        :attr:`ArtistBrowserType.NO_ALBUMS`.

        Will always return an empty list if the artist browser isn't loaded.
        """
        if not self.is_loaded:
            return []

        def get_track(sp_artistbrowse, key):
            return spotify.Track(
                sp_track=lib.sp_artistbrowse_track(sp_artistbrowse, key))

        return utils.Sequence(
            sp_obj=self._sp_artistbrowse,
            add_ref_func=lib.sp_artistbrowse_add_ref,
            release_func=lib.sp_artistbrowse_release,
            len_func=lib.sp_artistbrowse_num_tracks,
            getitem_func=get_track)

    @property
    def tophit_tracks(self):
        """The artist's top hit tracks.

        Will always return an empty list if the artist browser isn't loaded.
        """
        if not self.is_loaded:
            return []

        def get_track(sp_artistbrowse, key):
            return spotify.Track(
                sp_track=lib.sp_artistbrowse_tophit_track(
                    sp_artistbrowse, key))

        return utils.Sequence(
            sp_obj=self._sp_artistbrowse,
            add_ref_func=lib.sp_artistbrowse_add_ref,
            release_func=lib.sp_artistbrowse_release,
            len_func=lib.sp_artistbrowse_num_tophit_tracks,
            getitem_func=get_track)

    @property
    def albums(self):
        """The artist's albums.

        Will be an empty list if the browser was created with a ``type`` of
        :attr:`ArtistBrowserType.NO_ALBUMS`.

        Will always return an empty list if the artist browser isn't loaded.
        """
        if not self.is_loaded:
            return []

        def get_album(sp_artistbrowse, key):
            return spotify.Album(
                sp_album=lib.sp_artistbrowse_album(sp_artistbrowse, key))

        return utils.Sequence(
            sp_obj=self._sp_artistbrowse,
            add_ref_func=lib.sp_artistbrowse_add_ref,
            release_func=lib.sp_artistbrowse_release,
            len_func=lib.sp_artistbrowse_num_albums,
            getitem_func=get_album)

    @property
    def similar_artists(self):
        """The artist's similar artists.

        Will always return an empty list if the artist browser isn't loaded.
        """
        if not self.is_loaded:
            return []

        def get_artist(sp_artistbrowse, key):
            return spotify.Artist(
                sp_artist=lib.sp_artistbrowse_similar_artist(
                    sp_artistbrowse, key))

        return utils.Sequence(
            sp_obj=self._sp_artistbrowse,
            add_ref_func=lib.sp_artistbrowse_add_ref,
            release_func=lib.sp_artistbrowse_release,
            len_func=lib.sp_artistbrowse_num_similar_artists,
            getitem_func=get_artist)

    @property
    def biography(self):
        """A biography of the artist."""
        # TODO Check behavior when not loaded
        return utils.to_unicode(
            lib.sp_artistbrowse_biography(self._sp_artistbrowse))


@ffi.callback('void(sp_artistbrowse *, void *)')
def _artistbrowse_complete_callback(sp_artistbrowse, handle):
    logger.debug('artistbrowse_complete_callback called')
    if handle is ffi.NULL:
        logger.warning(
            'artistbrowse_complete_callback called without userdata')
        return
    (callback, artist_browser) = ffi.from_handle(handle)
    artist_browser._callback_handles.remove(handle)
    artist_browser.complete_event.set()
    if callback is not None:
        callback(artist_browser)


@utils.make_enum('SP_ARTISTBROWSE_')
class ArtistBrowserType(utils.IntEnum):
    pass
