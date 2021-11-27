import os
import functools
import logging
import logging.config


from flask import Flask
from flask import request
import redis
import hn_feeds
import logger_config

app = Flask(__name__)

logging.config.dictConfig(logger_config.get_config())


@functools.lru_cache(None)
def _get_feed_generator():
  redis_server = os.environ.get("REDIS_SERVER", None)
  if redis_server:
    host, port = redis_server.split(":")
    redis_db = os.environ.get("REDIS_DB", 0)
    redis_client = redis.Redis(host=host, port=int(port), db=redis_db)
    redis_client.ping()  # test connection
    logging.info(f"Connected to Redis at {host}:{port}")
  else:
    redis_client = None
    logging.warning("Not using Redis")

  return hn_feeds.HNFeedsGenerator(
      timeout_secs=int(os.environ.get("TIMEOUT_SECS", 5)),
      max_workers=int(os.environ.get("MAX_WORKERS", 5)),
      redis_client=redis_client,
      redis_expire_secs=int(os.environ.get("REDIS_EXPIRE_SECS", 172800)),
      fulltext_rss_url=os.environ.get("FULLTEXT_RSS_URL", None))


@app.route('/')
def base():
  return f'<p>Must pass an url with a feed to parse!</p>'


@app.route('/favicon.ico')
def no_favicon():
  """Returns 404 if we pass a favicon request."""
  return '', 404


@app.route('/<path:url>')
def main_entry(url):
  del url  # Unused since we need full path anyway.
  full_path = request.full_path[1:]  # Strip leading /.

  feed_generator = _get_feed_generator()
  base_rss = f'http://{full_path}'
  logging.info(f'Got request for "{base_rss}". Creating feed.')
  fg = feed_generator.create_feed(base_rss=base_rss)
  if not fg:
    return '', 404

  xml = fg.atom_str(pretty=True)
  return xml, 200, {'Content-Type': 'text/xml; charset=utf-8'}
