"""Unit tests for hn_feeds.

Run from the repo root with:
  python -m unittest discover -s app -p '*_test.py'
"""

import logging
import pickle
import unittest
from unittest import mock

import feedparser
import hn_feeds
import requests
from feedgen.entry import FeedEntry

# A minimal RSS document, as returned by the full-text-rss service.
_FULLTEXT_RSS_BODY = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>c</title><item>
<title>Full title</title>
<description>&lt;p&gt;Full article&lt;/p&gt;</description>
</item></channel></rss>"""


def setUpModule():
  # The module under test logs every entry it processes to stdout.
  logging.disable(logging.CRITICAL)


def tearDownModule():
  logging.disable(logging.NOTSET)


def _fp_entry(
  link: str = 'https://example.com/article',
  title: str = 'HN title',
  description: str = '<p>HN comments</p>',
) -> feedparser.FeedParserDict:
  """Builds a feedparser entry like the ones found in the HN RSS feed."""
  entry = feedparser.FeedParserDict()
  entry['link'] = link
  entry['title'] = title
  entry['description'] = description
  entry['published'] = 'Mon, 01 Jan 2024 00:00:00 GMT'
  return entry


def _fg_entry(
  title: str = 'Parsed title', content: str | None = '<p>Parsed article</p>'
) -> FeedEntry:
  """Builds a FeedEntry like the one a successful parser would return."""
  entry = FeedEntry()
  entry.title(title)
  if content is not None:
    entry.content(content, type='html')
  return entry


def _make_generator(**kwargs) -> hn_feeds.HNFeedsGenerator:
  """Creates a generator without building a real user agent rotator."""
  with (
    mock.patch.object(hn_feeds.HNFeedsGenerator, '_get_user_agent_rotator'),
    mock.patch.object(
      hn_feeds.requests, 'get', return_value=mock.Mock(status_code=200)
    ),
  ):
    return hn_feeds.HNFeedsGenerator(**kwargs)


class GetCookiesTest(unittest.TestCase):

  def test_returns_the_cookies_of_the_matching_site(self):
    # Matching is on a substring, as the feed urls carry a scheme and a path.
    self.assertEqual(
      hn_feeds._get_cookies('https://www.npr.org/2024/01/01/story'),
      hn_feeds.COOKIES_CFG['npr.org'],
    )

  def test_returns_none_for_an_unconfigured_site(self):
    # requests.get(cookies=None) means "send no cookies".
    self.assertIsNone(hn_feeds._get_cookies('https://example.com/article'))


class RobotCheckTest(unittest.TestCase):

  def test_detects_the_robot_check_page(self):
    doc = mock.Mock()
    doc.title.return_value = 'Are you a robot? Please verify'
    self.assertTrue(hn_feeds._robot_check(doc))

  def test_accepts_a_regular_page(self):
    doc = mock.Mock()
    doc.title.return_value = 'A perfectly normal article'
    self.assertFalse(hn_feeds._robot_check(doc))


class IsEmptyReadabilitySummaryTest(unittest.TestCase):
  """Readability returns a bare <body> wrapper when it extracts nothing."""

  def test_short_body_wrapper_is_empty(self):
    self.assertTrue(hn_feeds._is_empty_readability_summary('<body>hi</body>'))

  def test_long_summary_is_not_empty(self):
    # Over 1k chars means readability did extract an article, body tag or not.
    summary = '<body>' + 'a' * 1001 + '</body>'
    self.assertFalse(hn_feeds._is_empty_readability_summary(summary))

  def test_short_summary_without_body_tag_is_not_empty(self):
    # html_partial=True strips the body tag on a real extraction.
    self.assertFalse(hn_feeds._is_empty_readability_summary('<div>short</div>'))


class InitTest(unittest.TestCase):

  def test_appends_a_slash_to_the_fulltext_url(self):
    # Without it urljoin would drop the last path segment.
    generator = _make_generator(fulltext_rss_url='http://ftr:80/ftr')
    self.assertEqual(generator._fulltext_rss_url, 'http://ftr:80/ftr/')

  def test_drops_an_unreachable_fulltext_url(self):
    # So that every entry falls back to readability instead of failing.
    with (
      mock.patch.object(hn_feeds.HNFeedsGenerator, '_get_user_agent_rotator'),
      mock.patch.object(
        hn_feeds.requests, 'get', return_value=mock.Mock(status_code=500)
      ),
    ):
      generator = hn_feeds.HNFeedsGenerator(fulltext_rss_url='http://ftr/')
    self.assertIsNone(generator._fulltext_rss_url)


class FeedFromFulltextRssTest(unittest.TestCase):

  def setUp(self):
    self.generator = _make_generator(fulltext_rss_url='http://ftr/')

  def test_returns_the_parsed_entry(self):
    response = mock.Mock(status_code=200, content=_FULLTEXT_RSS_BODY)
    with mock.patch.object(hn_feeds.requests, 'get', return_value=response):
      entry = self.generator._feed_from_fulltext_rss('https://example.com/a')

    self.assertEqual(entry.title(), 'Full title')
    self.assertEqual(entry.content()['content'], '<p>Full article</p>')

  def test_quotes_the_url_it_asks_for(self):
    # An unquoted '&' would truncate the url in the service's query string.
    response = mock.Mock(status_code=200, content=_FULLTEXT_RSS_BODY)
    with mock.patch.object(
      hn_feeds.requests, 'get', return_value=response
    ) as mock_get:
      self.generator._feed_from_fulltext_rss('https://example.com/a?x=1&y=2')

    requested = mock_get.call_args.args[0]
    self.assertIn('http://ftr/makefulltextfeed.php?url=', requested)
    self.assertIn('https%3A%2F%2Fexample.com%2Fa%3Fx%3D1%26y%3D2', requested)

  def test_returns_none_when_the_content_could_not_be_retrieved(self):
    body = _FULLTEXT_RSS_BODY.replace(
      b'&lt;p&gt;Full article&lt;/p&gt;',
      b'[unable to retrieve full-text content]',
    )
    response = mock.Mock(status_code=200, content=body)
    with mock.patch.object(hn_feeds.requests, 'get', return_value=response):
      self.assertIsNone(
        self.generator._feed_from_fulltext_rss('https://example.com/a')
      )

  def test_returns_none_on_a_bad_status_code(self):
    response = mock.Mock(status_code=502, content=b'')
    with mock.patch.object(hn_feeds.requests, 'get', return_value=response):
      self.assertIsNone(
        self.generator._feed_from_fulltext_rss('https://example.com/a')
      )

  def test_returns_none_when_the_service_is_unreachable(self):
    with mock.patch.object(
      hn_feeds.requests, 'get', side_effect=requests.RequestException('boom')
    ):
      self.assertIsNone(
        self.generator._feed_from_fulltext_rss('https://example.com/a')
      )


class FeedFromReadabilityTest(unittest.TestCase):

  def setUp(self):
    self.generator = _make_generator()

  def _run(self, title='Doc title', summary=None, response=None):
    """Runs the parser with readability and the http response stubbed out."""
    summary = summary if summary is not None else '<div>' + 'a' * 1001 + '</div>'
    response = response or mock.Mock(ok=True, status_code=200, content=b'<html/>')
    doc = mock.Mock()
    doc.title.return_value = title
    doc.summary.return_value = summary
    with (
      mock.patch.object(
        hn_feeds.requests, 'get', return_value=response
      ) as self.mock_get,
      mock.patch.object(hn_feeds.readability, 'Document', return_value=doc),
    ):
      return self.generator._feed_from_readability('https://example.com/a')

  def test_returns_the_extracted_article(self):
    entry = self._run(title='Doc title', summary='<div>' + 'a' * 1001 + '</div>')
    self.assertEqual(entry.title(), 'Doc title')
    self.assertEqual(entry.content()['content'], '<div>' + 'a' * 1001 + '</div>')

  def test_returns_none_on_a_robot_check(self):
    # Caching a captcha page would poison the feed for the whole expiry window.
    self.assertIsNone(self._run(title='Are you a robot?'))

  def test_returns_none_on_an_empty_extraction(self):
    self.assertIsNone(self._run(summary='<body>nothing</body>'))

  def test_returns_none_on_a_timeout(self):
    with mock.patch.object(
      hn_feeds.requests, 'get', side_effect=requests.exceptions.Timeout
    ):
      self.assertIsNone(
        self.generator._feed_from_readability('https://example.com/a')
      )

  def test_returns_none_on_a_bad_response(self):
    self.assertIsNone(self._run(response=mock.Mock(ok=False, status_code=404)))

  def test_sends_the_configured_cookies(self):
    # npr.org serves a consent wall instead of the article without them.
    doc = mock.Mock()
    doc.title.return_value = 'Story'
    doc.summary.return_value = '<div>' + 'a' * 1001 + '</div>'
    response = mock.Mock(ok=True, status_code=200, content=b'<html/>')
    with (
      mock.patch.object(
        hn_feeds.requests, 'get', return_value=response
      ) as mock_get,
      mock.patch.object(hn_feeds.readability, 'Document', return_value=doc),
    ):
      self.generator._feed_from_readability('https://www.npr.org/story')

    self.assertEqual(
      mock_get.call_args.kwargs['cookies'], hn_feeds.COOKIES_CFG['npr.org']
    )


class CreateFeedgeneratorEntryTest(unittest.TestCase):
  """Tests the routing of an entry to the right parser."""

  def setUp(self):
    self.generator = _make_generator()

  def test_pdf_is_returned_as_it_is_with_a_prefix(self):
    # readability cannot parse a pdf, so it is passed through untouched.
    with mock.patch.object(self.generator, '_feed_from_readability') as parser:
      entry = self.generator._create_feedgenerator_entry(
        _fp_entry(link='https://example.com/paper.pdf')
      )

    parser.assert_not_called()
    self.assertEqual(entry.title(), '[pdf] HN title')
    self.assertEqual(entry.content()['content'], '<p>HN comments</p>')

  def test_ignored_url_is_returned_as_it_is_with_its_prefix(self):
    with mock.patch.object(self.generator, '_feed_from_readability') as parser:
      entry = self.generator._create_feedgenerator_entry(
        _fp_entry(link='https://youtube.com/watch?v=1')
      )

    parser.assert_not_called()
    self.assertEqual(entry.title(), '[YT] HN title')

  def test_ignored_url_without_a_prefix_keeps_its_title(self):
    entry = self.generator._create_feedgenerator_entry(
      _fp_entry(link='https://news.ycombinator.com/item?id=1')
    )
    self.assertEqual(entry.title(), 'HN title')

  def test_returns_none_without_a_link(self):
    self.assertIsNone(self.generator._create_feedgenerator_entry(_fp_entry(link='')))

  def test_uses_readability_when_no_fulltext_url_is_configured(self):
    with mock.patch.object(
      self.generator, '_feed_from_readability', return_value=_fg_entry()
    ) as parser:
      self.generator._create_feedgenerator_entry(_fp_entry())

    parser.assert_called_once_with('https://example.com/article')

  def test_uses_fulltext_rss_for_a_configured_domain(self):
    generator = _make_generator(fulltext_rss_url='http://ftr/')
    with (
      mock.patch.object(
        generator, '_feed_from_fulltext_rss', return_value=_fg_entry()
      ) as fulltext,
      mock.patch.object(generator, '_feed_from_readability') as readability_parser,
    ):
      generator._create_feedgenerator_entry(
        _fp_entry(link='https://github.com/a/b')
      )

    fulltext.assert_called_once_with('https://github.com/a/b')
    readability_parser.assert_not_called()

  def test_falls_back_to_readability_when_fulltext_rss_fails(self):
    generator = _make_generator(fulltext_rss_url='http://ftr/')
    with (
      mock.patch.object(generator, '_feed_from_fulltext_rss', return_value=None),
      mock.patch.object(
        generator, '_feed_from_readability', return_value=_fg_entry()
      ) as readability_parser,
    ):
      generator._create_feedgenerator_entry(_fp_entry(link='https://github.com/a/b'))

    readability_parser.assert_called_once_with('https://github.com/a/b')

  def test_falls_back_to_the_original_entry_when_parsing_fails(self):
    # An unparsable article still belongs in the feed, just unaugmented.
    with mock.patch.object(
      self.generator, '_feed_from_readability', return_value=None
    ):
      entry = self.generator._create_feedgenerator_entry(_fp_entry())

    self.assertEqual(entry.title(), 'HN title')
    self.assertEqual(entry.content()['content'], '<p>HN comments</p>')

  def test_keeps_the_hn_metadata_and_appends_the_original_description(self):
    # The HN title and the comments link must survive the parsing, otherwise
    # the entry loses its point/comment counts.
    with mock.patch.object(
      self.generator, '_feed_from_readability', return_value=_fg_entry()
    ):
      entry = self.generator._create_feedgenerator_entry(_fp_entry())

    self.assertEqual(entry.title(), 'HN title')
    self.assertEqual(entry.id(), 'https://example.com/article')
    self.assertEqual(entry.link()[0]['href'], 'https://example.com/article')
    self.assertEqual(entry.author(), [{'name': 'example.com'}])
    self.assertEqual(
      entry.content()['content'], '<p>Parsed article</p><p>HN comments</p>'
    )

  def test_keeps_the_entry_when_the_parser_returned_no_content(self):
    with mock.patch.object(
      self.generator, '_feed_from_readability', return_value=_fg_entry(content=None)
    ):
      entry = self.generator._create_feedgenerator_entry(_fp_entry())

    self.assertEqual(entry.content()['content'], '<p>HN comments</p>')


class CachedCreateFeedgeneratorEntryTest(unittest.TestCase):

  def setUp(self):
    self.redis_client = mock.Mock()
    self.generator = _make_generator(redis_client=self.redis_client)

  def test_returns_the_cached_entry_without_refetching(self):
    cached = _fg_entry(title='Cached')
    self.redis_client.get.return_value = pickle.dumps(cached)
    with mock.patch.object(self.generator, '_create_feedgenerator_entry') as build:
      entry = self.generator.create_feedgenerator_entry(_fp_entry())

    build.assert_not_called()
    self.redis_client.get.assert_called_once_with(
      name='py:https://example.com/article'
    )
    self.assertEqual(entry.title(), 'Cached')

  def test_caches_a_freshly_built_entry(self):
    self.redis_client.get.return_value = None
    built = _fg_entry()
    with mock.patch.object(
      self.generator, '_create_feedgenerator_entry', return_value=built
    ):
      entry = self.generator.create_feedgenerator_entry(_fp_entry())

    self.assertIs(entry, built)
    kwargs = self.redis_client.set.call_args.kwargs
    self.assertEqual(kwargs['name'], 'py:https://example.com/article')
    self.assertEqual(kwargs['ex'], self.generator._redis_expire_secs)
    self.assertEqual(pickle.loads(kwargs['value']).title(), 'Parsed title')

  def test_does_not_cache_a_failed_entry(self):
    # Caching a failure would keep the entry broken for the whole expiry.
    self.redis_client.get.return_value = None
    with mock.patch.object(
      self.generator, '_create_feedgenerator_entry', return_value=None
    ):
      self.assertIsNone(self.generator.create_feedgenerator_entry(_fp_entry()))

    self.redis_client.set.assert_not_called()

  def test_returns_none_when_anything_raises(self):
    # One broken entry must not take down the whole feed.
    self.redis_client.get.side_effect = ValueError('redis is down')
    self.assertIsNone(self.generator.create_feedgenerator_entry(_fp_entry()))

  def test_works_without_a_redis_client(self):
    generator = _make_generator()
    with mock.patch.object(
      generator, '_create_feedgenerator_entry', return_value=_fg_entry()
    ) as build:
      entry = generator.create_feedgenerator_entry(_fp_entry())

    build.assert_called_once()
    self.assertEqual(entry.title(), 'Parsed title')


class CreateFeedTest(unittest.TestCase):

  def setUp(self):
    self.generator = _make_generator()

  def test_returns_none_for_a_feed_without_entries(self):
    # main.py turns this into a 404.
    parsed = feedparser.FeedParserDict()
    parsed['entries'] = []
    with mock.patch.object(hn_feeds.feedparser, 'parse', return_value=parsed):
      self.assertIsNone(self.generator.create_feed('http://hn/rss'))

  def test_adds_the_parsed_entries_and_skips_the_failed_ones(self):
    parsed = feedparser.FeedParserDict()
    parsed['entries'] = [_fp_entry(), _fp_entry(link='https://example.com/b')]
    with (
      mock.patch.object(hn_feeds.feedparser, 'parse', return_value=parsed),
      mock.patch.object(
        self.generator,
        'create_feedgenerator_entry',
        side_effect=[_fg_entry(title='ok'), None],
      ),
    ):
      feed = self.generator.create_feed('http://hn/rss')

    self.assertEqual(feed.id(), 'http://hn/rss')
    self.assertEqual([entry.title() for entry in feed.entry()], ['ok'])


if __name__ == '__main__':
  unittest.main()
