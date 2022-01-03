from concurrent import futures
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from typing import Dict, Optional
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem

import feedparser
import pickle
import re
import urllib.parse

import readability
import requests
import redis
import threading
import logger_config

logger = logger_config.get_logger()


redis_mutex = threading.Lock()

################################################################################
# Site configs
# TODO: move them to a config file.

# The websites that will be sent to full-text-rss.
# Note: some of those require custom site patterns.
FULL_TEXT_RSS = [
    'arxiv.org',
    'bbc.co.uk',
    'ai.googleblog.com',
    'github.com',
    'nature.com',
    'newyorker.com',
    'quantamagazine.org',
    'techcrunch.com',
    'theatlantic.com',
    'thedrive.com',
    r'wired.co[^/]+',
]

# The websites which will NOT be parsed and just returned as they are in
# the original feed.
IGNORED_URLS = [
    # URL and title prefix
    ('news.ycombinator.com', None),
    ('youtube.com', 'YT'),
    ('twitter.com', 'twit'),
    ('spectrum.ieee.org', None),  # temporary until "full page reload" is fixed
]

# The individual cookies configuration per website, usually to get around
# "Data Protection Choices".
# TODO: move this to a file.
COOKIES_CFG = {
    'npr.org': {
        'trackingChoice': 'true',
        'choiceVersion': '1',
        'dateOfChoice': '1596844800021'
    },
    # TODO: This is a temporary solution, as it doesn't always work.
    'techcrunch.com': {
        'EuConsent': 'BOsb5w6O4A6WNAOABCENCuuAAAAuJ6__f_97_8_v2fdvduz_Ov_j_c__'
                     '3XWcfPZvcELzhK9Meu_2wxd4u9wNRM5wckx87eJrEso5YzISsG-RMod_'
                     'zl_v3ziX9ohPowEc9qzznZEw6vs2o8JzBAAAgAAA',
        'GUC': 'AQABAQFe_MVfN0IiWQTD',
        'GUCS': 'AXGZ9Av6'
    }
}

################################################################################


def _get_cookies(url: str) -> Dict[str, str]:
  """Returns the specific cookies per site, if available."""
  for key, cookies in COOKIES_CFG.items():
    if key in url:
      return cookies


def _robot_check(readability_doc: readability.Document) -> bool:
  """Checks if the readability document returned a robot check entry."""
  if 'Are you a robot?' in readability_doc.title():
    return True
  # TODO: add more
  return False


def _empty_readability_check(summary: str) -> bool:
  """Check if the readability summary is actually an empty entry or not."""
  # It must be smaller than 1k chars, and contain a body tag, which should
  # not be there as summary should strip it.
  return (len(summary) > 1000 or
          not bool(re.match(r'<body.+</body>', summary, re.MULTILINE | re.DOTALL)))


class HNFeedsGenerator:
  """Creates the RSS feeds."""

  def __init__(self, timeout_secs: int = 5,
               max_workers: int = 30,
               redis_client: Optional[redis.Redis] = None,
               redis_expire_secs: int = 60 * 60 * 24 * 2,
               fulltext_rss_url: Optional[str] = None):
    """Initializes the FeedsCreator.
    
    Args:
      timeout_secs: The timeout in seconds for the requests.
      max_workers: The maximum number of concurrent workers to use.
      redis_client: The redis client. If None, it will always fetch the content
        rather than use the cache.
      redis_expire_secs: The time in seconds to expire the redis value.
      full_text_rss_url: The url of the full-text RSS feed.
    """
    self._timeout_secs = timeout_secs
    self._max_workers = max_workers
    self._redis_client = redis_client
    self._redis_expire_secs = redis_expire_secs
    self._fulltext_rss_url = fulltext_rss_url
    logger.info('Getting agent rotator..')
    self._user_agent_rotator = self._get_user_agent_rotator()

    if self._fulltext_rss_url:
      # Make sure it ends with a slash otherwise urljoin will not work as
      # expected.
      logger.info(f'[FULLTEXT RSS]: Testing {self._fulltext_rss_url}..')
      if not self._fulltext_rss_url.endswith('/'):
        self._fulltext_rss_url += '/'
      response = requests.get(self._fulltext_rss_url)
      if response.status_code == 200:
        logger.info(f'[FULLTEXT RSS]: Connected!')
      else:
        logger.error('Failure to connect to full-text RSS feed: %s',
                     response.status_code)
        self._fulltext_rss_url = None
    else:
      logger.warning('[FULLTEXT_RSS]: No URL provided. Will only use the '
                     'internal [READABILITY]')

  def _get_user_agent_rotator(self) -> UserAgent:
    software_names = [SoftwareName.CHROME.value]
    operating_systems = [OperatingSystem.WINDOWS.value,
                         OperatingSystem.LINUX.value,
                         OperatingSystem.MAC.value]
    return UserAgent(software_names=software_names,
                     operating_systems=operating_systems)

  def _feed_from_fulltext_rss(self, url: str) -> Optional[FeedEntry]:
    """Get the feed entry by parsing with Full text RSS.
    
    Full text RSS can work better than readability, as it is based on patterns
    provided by the community.

    Args:
      url: The url of the article.
    Returns:
      The feed parsed using full text RSS. 
    """
    quoted_url = urllib.parse.quote(url, safe='')
    path = urllib.parse.urljoin(
        self._fulltext_rss_url,
        f'makefulltextfeed.php?url={quoted_url}&links=preserve')

    try:
      response = requests.get(path)
    except requests.RequestException as e:
      logger.error(f'[FULLTEXT_RSS]: Failed to get {path}: {e}')
      return None
    if response.status_code != 200:
      logger.error(f'[FULLTEXT_RSS]: Failed to get {path}: '
                    f'got code {response.status_code}')
      return None

    feed = feedparser.parse(response.content)
    if not feed.entries:
      return None

    fp_entry = feed.entries[0]
    if '[unable to retrieve full-text content]' in fp_entry.description:
      return None

    fg_entry = FeedEntry()

    fg_entry.title(fp_entry.title)
    fg_entry.content(fp_entry.description, type='html')

    logger.info(f'[FULLTEXT_RSS]: {url}')
    return fg_entry

  def _feed_from_readability(self, url: str) -> Optional[FeedEntry]:
    """Get the feed entry by parsing the url with python readability."""
    ua = self._user_agent_rotator.get_random_user_agent()
    header = {'User-Agent': str(ua)}
    try:
      response = requests.get(url, headers=header,
                              timeout=self._timeout_secs,
                              cookies=_get_cookies(url))
    except requests.exceptions.Timeout:
      logger.warning(f'[TIMEOUT]: {url}')
      return None

    if not response.ok:
      logger.error(f'[BAD RESPONSE {response.status_code}]: {url}')
      return None
    doc = readability.Document(response.content)
    if _robot_check(doc):
      return None  # it's has a robot check
    summary = doc.summary(html_partial=True)
    if not _empty_readability_check(summary):
      return None

    fg_entry = FeedEntry()
    fg_entry.title(doc.title())
    fg_entry.content(summary, type='html')
    logger.info(f'[READABILITY]: {url}')
    return fg_entry

  def _feed_as_it_is(self,
                     fp_entry: feedparser.FeedParserDict,
                     title_prefix: Optional[str] = None) -> FeedEntry:
    """Get the feed without any parsing, just copying the original RSS entry."""
    fg_entry = FeedEntry()
    fg_entry.id(fp_entry.link)
    fg_entry.link(href=fp_entry.link)
    if title_prefix:
      title = f'[{title_prefix}] {fp_entry.title}'
    else:
      title = fp_entry.title
    fg_entry.title(title)
    fg_entry.published(fp_entry.published)
    fg_entry.content(fp_entry.description, type='html')
    fg_entry.author({'name': urllib.parse.urlparse(fp_entry.link).netloc})

    logger.info(f'[NO_CHANGE]: {fp_entry.link}')
    return fg_entry

  def _create_feedgenerator_entry(
          self, fp_entry: feedparser.FeedParserDict) -> Optional[FeedEntry]:
    """Creates a FeedGenerator entry for the given feedparser entry."""
    url = fp_entry.link
    if url.endswith('.pdf'):
      # pdfs are returned as they are, with just a pdf prefix added to the
      # title, e.g. '[pdf]: this is my title'
      return self._feed_as_it_is(fp_entry, title_prefix='pdf')

    for ignored, title_prefix in IGNORED_URLS:
      if ignored in url:
        return self._feed_as_it_is(fp_entry, title_prefix)

    fg_entry = None
    if self._fulltext_rss_url:
      for ftrss_url_re in FULL_TEXT_RSS:
        if re.search(ftrss_url_re, url):
          fg_entry = self._feed_from_fulltext_rss(url)
          break

    fg_entry = fg_entry or self._feed_from_readability(url)
    if not fg_entry:
      return self._feed_as_it_is(fp_entry)

    # This is a bit of a hack to get the title. I'm not 100% sure if it's
    # the best way to do it.
    fg_entry.title(fp_entry.title)

    fg_entry.id(url)
    fg_entry.link(href=url)
    fg_entry.published(fp_entry.published)
    fg_entry.author({'name': urllib.parse.urlparse(url).netloc})

    # Appends the original bit.
    fg_entry.content(fg_entry.content()[
        'content'] + fp_entry.description, type='html')
    return fg_entry

  def create_feedgenerator_entry(
          self, fp_entry: feedparser.FeedParserDict) -> Optional[FeedEntry]:
    """Creates a feed entry or fetch the cached version if available.
    
    Note: this method is thread-safe.
    Args:
      fp_entry: The feedparser entry to augment.
    Returns:
      The feed entry if successful, None otherwise.
    """
    url = fp_entry['link']
    redis_key = f'py:{url}'
    pickled_fe = None

    try:
      if self._redis_client:
        pickled_fe = self._redis_client.get(name=redis_key)

      if pickled_fe:
        logger.debug(f'[CACHED]: {url}')
        fg_entry = pickle.loads(pickled_fe)
      else:
        fg_entry = self._create_feedgenerator_entry(fp_entry)
        if fg_entry and self._redis_client:
          self._redis_client.set(
              name=redis_key,
              value=pickle.dumps(fg_entry, protocol=pickle.HIGHEST_PROTOCOL),
              ex=self._redis_expire_secs)
    except Exception as e:
      logger.error(f'[ERROR] {e}: {url}')
      return None

    return fg_entry

  def create_feed(self, base_rss: str) -> Optional[FeedGenerator]:
    """Creates the parsed feed from the base rss feed.
    
    Args:
      base_rss: The base rss feed.
    Returns:
      The feed if successful, None otherwise.
    """
    feed = feedparser.parse(base_rss)  # type: feedparser.feedParserDict
    if not feed.entries:
      return None

    logger.info(f'Fetching {len(feed.entries)} feeds '
                 f'with {self._max_workers} workers..')

    num_added = 0
    with futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
      res = executor.map(self.create_feedgenerator_entry, feed.entries,
                         timeout=self._timeout_secs*1.5)
      fg = FeedGenerator()
      fg.id(base_rss)
      fg.title('Hacker News (hn_feeds)')
      fg.link(href=base_rss, rel='alternate')
      for fg_entry in res:
        try:
          if not fg_entry:
            continue
          fg.add_entry(fg_entry)
          num_added += 1
        except Exception:
          pass

    logger.info(f'Got {num_added} feeds for "{base_rss}".')
    return fg
